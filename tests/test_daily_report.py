import io
import json
import re
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from functools import partial
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from emotion_index.app.cli import main
from emotion_index.app.daily_report import deliver_report, report_market
from emotion_index.app.scoring import score_market
from emotion_index.domain.anchor import DailyBar, MarketSeries
from emotion_index.domain.ledger import LedgerEntry
from emotion_index.domain.market import Market, MarketAnchor, Sector
from emotion_index.domain.models import parse_utc_timestamp
from emotion_index.integrations.capture import capture_market
from emotion_index.integrations.deepseek_client import DeepSeekClient
from emotion_index.integrations.ledger import EmotionLedger
from emotion_index.integrations.notifier import BARK_FLAGS, notify_report
from emotion_index.integrations.providers.http import XUEQIU_UNAUTHORIZED
from emotion_index.integrations.storage import atomic_publish_bytes
from emotion_index.reporting.html_report import render_daily_html
from emotion_index.research.market_anchor import build_market_anchors

DAY = date(2026, 11, 23)
CN_CAPTURE = datetime.combine(DAY, time(8), timezone.utc)
US_CAPTURE = CN_CAPTURE.replace(hour=22, minute=30)
NOW = US_CAPTURE + timedelta(minutes=15)
EUPHORIA = "EXTREME_EUPHORIA_WARNING"
PANIC = "EXTREME_CAPITULATION_ALERT"
WEAK_MARKET = "SOCIAL_OVERHEATED_VS_MARKET_WEAK"
RESILIENT_MARKET = "SOCIAL_PANIC_VS_MARKET_RESILIENT"

SMTP_ENV = {
    "EMOTION_INDEX_SMTP_HOST": "mail.invalid",
    "EMOTION_INDEX_SMTP_FROM": "report@example.invalid",
    "EMOTION_INDEX_SMTP_TO": "first@example.invalid, second@example.invalid",
    "EMOTION_INDEX_SMTP_USER": "fixture-user",
    "EMOTION_INDEX_SMTP_PASSWORD": "fixture-smtp-password",
}
BARK_ENV = {"EMOTION_INDEX_BARK_KEY": "fixture-bark-key", "EMOTION_INDEX_BARK_URL": "https://bark.invalid"}


def prior_sessions(length):
    days = []
    day = DAY
    while len(days) < length:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return tuple(reversed(days))


def json_response(body, status=200):
    stream = io.BytesIO(json.dumps(body).encode())
    stream.code = stream.status = status
    return stream


def deepseek_reply(request, timeout):
    messages = json.loads(request.data)["messages"]
    posts = json.loads(messages[1]["content"])["posts"]
    scores = [
        {"post_id": post["post_id"], "polarity": 0.9, "emotion_state": "EUPHORIA_FOMO", "is_sarcasm": False,
         "sector": "TECH_GROWTH", "confidence": 0.9, "reason_short": "Synthetic bullish signal"}
        for post in posts
    ]
    content = json.dumps({"scores": scores})
    return json_response({"choices": [{"finish_reason": "stop", "message": {"content": content}}]})


def ledger_row(market, day, count_, cutoff=None):
    return LedgerEntry(
        market, day, cutoff or datetime.combine(day, time(23), timezone.utc),
        tuple((sector, count_ if sector is Sector.BROAD_MARKET else 0) for sector in Sector),
        text_emotion_index=52, volume_zscore_20d=0.35, anchor_score=50, composite_score=51,
    )


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(".runtime/tmp")
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.runtime = Path(self.enterContext(TemporaryDirectory(dir=temporary_root)))
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("Live network is forbidden")))
        self.enterContext(patch("emotion_index.app.scoring.utc_now", return_value=NOW))
        self.enterContext(patch("emotion_index.app.daily_report.utc_now", return_value=NOW))
        self.enterContext(patch("emotion_index.integrations.capture.utc_now", return_value=NOW))
        self.enterContext(patch(
            "emotion_index.app.scoring.DeepSeekClient", partial(DeepSeekClient, clock=lambda: NOW, holidays=frozenset()),
        ))
        self.deepseek = self.enterContext(patch(
            "emotion_index.integrations.deepseek_client.urlopen", side_effect=deepseek_reply,
        ))
        self.provider = self.enterContext(patch(
            "emotion_index.integrations.providers.http.urlopen", side_effect=self.public_api,
        ))
        self.smtp = self.enterContext(patch("emotion_index.integrations.notifier.smtplib.SMTP"))
        self.smtp.return_value.__enter__.return_value.send_message.return_value = {}
        self.bark = self.enterContext(patch(
            "emotion_index.integrations.notifier.urlopen", side_effect=lambda *args, **kwargs: json_response({"code": 200}),
        ))
        self.rclone = self.enterContext(patch("emotion_index.integrations.gdrive_sync.subprocess.run"))
        self.enterContext(patch.dict("os.environ", {
            **SMTP_ENV, **BARK_ENV, "DEEPSEEK_API_KEY": "fixture-deepseek-key",
            "EMOTION_INDEX_RCLONE_REMOTE": "workdrive", "EMOTION_INDEX_CN_MARKET_HOLIDAYS": "",
            "EMOTION_INDEX_US_MARKET_HOLIDAYS": "", "EMOTION_INDEX_SMTP_TLS": "starttls",
            "EMOTION_INDEX_SMTP_PORT": "587",
        }))

    def public_api(self, request, timeout):
        url = urlsplit(request.full_url)
        if url.hostname == "public.api.bsky.app":
            posts = [
                {
                    "uri": f"at://did:fixture/post/{number}", "author": {"did": "did:fixture"},
                    "record": {"createdAt": (US_CAPTURE - timedelta(minutes=30)).isoformat(),
                               "text": f"FOMO bullish on $QQQ {number}"},
                    "likeCount": number,
                }
                for number in range(10)
            ]
            return json_response({"posts": posts})
        if url.hostname == "api.stocktwits.com":
            return json_response({"messages": []})
        if url.hostname == "apewisdom.io":
            return json_response({"results": []})
        if url.hostname == "production.dataviz.cnn.io":
            return json_response({"fear_and_greed": {"score": 30, "timestamp": US_CAPTURE.isoformat()}})
        if url.hostname == "query1.finance.yahoo.com":
            symbol = unquote(url.path.rsplit("/", 1)[1])
            days = (*prior_sessions(5), DAY)
            prices = {"^VIX": 28, "SPY": 99, "QQQ": 98, "XLF": 99, "IWM": 98, "XLU": 101}
            quote = {"close": [20 if symbol == "^VIX" else 100] * 5 + [prices[symbol]]}
            return json_response({"chart": {"error": None, "result": [{
                "timestamp": [datetime.combine(day, time(14, 30), timezone.utc).timestamp() for day in days],
                "indicators": {"quote": [quote]},
            }]}})
        if url.hostname == "web.ifzq.gtimg.cn":
            symbol = parse_qs(url.query)["param"][0].split(",")[0]
            close = "101" if symbol == "sh510880" else "104"
            rows = [[day.isoformat(), "99", "100", "101", "99", "100"] for day in prior_sessions(20)]
            rows.append([DAY.isoformat(), "100", close, "104", "99", "150"])
            key = "qfqday" if symbol in {"sh510880", "sh563300"} else "day"
            return json_response({"code": 0, "data": {symbol: {key: rows}}})
        if url.path == "/xueqiu":
            return json_response({}, 403)
        if url.path == "/cls":
            xml = (
                f"<rss><channel><item><guid>fixture-cn</guid><title>暴跌恐慌 $159915</title>"
                f"<pubDate>{(CN_CAPTURE - timedelta(minutes=30)).isoformat()}</pubDate>"
                "</item></channel></rss>"
            )
            stream = io.BytesIO(xml.encode())
            stream.code = 200
            return stream
        raise AssertionError(f"Unexpected fixture request: {url.hostname}{url.path}")

    def capture(self, market, at):
        ticks = count()
        env = {"EMOTION_INDEX_RSS_CN_CLS_URL": "https://feeds.invalid/cls",
               "EMOTION_INDEX_RSS_CN_XUEQIU_URL": "https://feeds.invalid/xueqiu"}
        receipt = capture_market(market, self.runtime, env=env, clock=lambda: at + timedelta(seconds=next(ticks)))
        return json.loads(receipt.read_bytes())

    def cli_report(self, market, *options):
        with patch("emotion_index.app.cli.report_market", partial(report_market, runtime=self.runtime)), patch(
            "emotion_index.app.cli.deliver_report", partial(deliver_report, runtime=self.runtime),
        ), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["report", "--market", market.value, *options]), 0)
        html_path, summary_path = map(Path, output.getvalue().splitlines())
        return html_path, summary_path

    def verify_report(self, html_path, json_path, market):
        self.assertEqual(html_path.name, f"EMOTION_{market.value}_{DAY}_close.html")
        self.assertEqual(json_path.name, f"emotion_summary_{market.value}_{DAY}.json")
        html = html_path.read_text(encoding="utf-8")
        summary = json.loads(json_path.read_bytes())
        audit = re.search(r'<script id="emotion-index-audit" type="application/json">(.*?)</script>', html, flags=re.S)
        self.assertEqual(json.loads(audit[1]), summary)
        self.assertEqual(summary["schema_version"], 2)
        self.assertEqual(summary["market"], market.value)
        self.assertEqual(summary["trading_date"], DAY.isoformat())
        self.assertEqual(set(summary["sectors"]), {sector.value for sector in Sector if sector is not Sector.BROAD_MARKET})
        self.assertEqual(html.count('<th scope="row">'), 4)
        self.assertEqual(html.count("<svg"), 2)
        self.assertEqual(html.count("<script"), 1)
        self.assertNotRegex(html, r'''(?i)(src|href)\s*=|@import|url\(''')
        self.assertIn("XHS_NOT_PROVIDED", html.split("</header>", 1)[0])
        return html, summary

    def test_us_and_cn_cli_reports_use_real_pipeline_with_mocked_external_services(self):
        us_receipt = self.capture(Market.US, US_CAPTURE)
        cn_receipt = self.capture(Market.CN, CN_CAPTURE)
        us_ledger = EmotionLedger(Market.US, self.runtime)
        for index, day in enumerate(prior_sessions(25)):
            us_ledger.upsert(ledger_row(Market.US, day, 1 + index % 2))
        # The more recent session's replacement is unavailable at the report cutoff.
        previous_day = prior_sessions(1)[0]
        us_ledger.upsert(ledger_row(Market.US, previous_day, 999, NOW))
        score_market(Market.US, self.runtime, capture_date=DAY)
        self.assertEqual(self.deepseek.call_count, 1)
        us_paths = self.cli_report(Market.US)
        us_html, us = self.verify_report(*us_paths, Market.US)
        self.assertEqual(us["cutoff_at"], us_receipt["finished_at"])
        self.assertEqual(us["scoring_mode"], "DEEPSEEK")
        self.assertEqual(us["audit"]["scoring"]["cache_hits"], 10)
        self.assertEqual(self.deepseek.call_count, 1)
        self.assertEqual(us["post_count"], 10)
        self.assertEqual(us["text_emotion_index"], 95)
        self.assertLess(us["market_anchor"]["anchor_score"], 45)
        self.assertEqual(us["volume_baseline_days"], 20)
        self.assertGreater(us["volume_zscore_20d"], 1.5)
        self.assertEqual(us["volume_flag"], "VOLUME_SPIKE")
        self.assertEqual(us["history"][0]["trading_date"], prior_sessions(21)[0].isoformat())
        self.assertEqual(us["history"][-1]["trading_date"], prior_sessions(2)[0].isoformat())
        self.assertEqual(us["divergence_flags"], [WEAK_MARKET, EUPHORIA])
        self.assertAlmostEqual(us["composite_score"], (95 + us["market_anchor"]["anchor_score"]) / 2)
        self.assertTrue(all(item["market_anchor"] is not None for item in us["sectors"].values()))
        # DeepSeek sector classification populates TECH_GROWTH even when tickers only mention broad QQQ.
        self.assertEqual(us["sectors"]["TECH_GROWTH"]["text_emotion_index"], 95)
        self.assertEqual(us["sectors"]["TECH_GROWTH"]["scored_post_count"], 10)
        for code in (WEAK_MARKET, EUPHORIA, "VOLUME_SPIKE"):
            self.assertIn(code, us_html)

        self.deepseek.side_effect = TimeoutError
        cn_html, cn = self.verify_report(*self.cli_report(Market.CN, "--notify", "--sync"), Market.CN)
        self.assertEqual(self.deepseek.call_count, 4)
        self.assertEqual(cn["cutoff_at"], cn_receipt["finished_at"])
        self.assertEqual(cn["scoring_mode"], "DEGRADED_LEXICON")
        self.assertEqual(cn["volume_flag"], "WARMUP")
        self.assertIsNone(cn["volume_zscore_20d"])
        self.assertEqual(cn["post_count"], 1)
        self.assertEqual(cn["panic_ratio"], 1.0)
        self.assertEqual(cn["divergence_flags"], [RESILIENT_MARKET, PANIC])
        self.assertGreater(cn["market_anchor"]["anchor_score"], 55)
        self.assertTrue(all(item["market_anchor"] is not None for item in cn["sectors"].values()))
        self.assertIn(cn["scoring_mode"], cn_html.split("</header>", 1)[0])
        self.assertIn(XUEQIU_UNAUTHORIZED, cn["status_flags"])
        self.assertFalse((self.runtime / "data" / "raw" / "CN" / "xueqiu" / f"{DAY}.json").exists())
        self.assertLessEqual(parse_utc_timestamp(cn["market_anchor"]["available_at"], "anchor"),
                             parse_utc_timestamp(cn["cutoff_at"], "cutoff"))

        self.smtp.assert_called_once_with("mail.invalid", 587, timeout=15)
        connection = self.smtp.return_value.__enter__.return_value
        connection.starttls.assert_called_once()
        connection.login.assert_called_once_with("fixture-user", "fixture-smtp-password")
        sent = connection.send_message.call_args
        self.assertEqual(sent.kwargs["to_addrs"], ["first@example.invalid", "second@example.invalid"])
        attachment, = sent.args[0].iter_attachments()
        self.assertEqual(attachment.get_filename(), f"EMOTION_CN_{DAY}_close.html")
        self.assertEqual(attachment.get_content().rstrip("\n"), cn_html.rstrip("\n"))
        self.bark.assert_called_once()
        request = self.bark.call_args.args[0]
        self.assertEqual(request.full_url, "https://bark.invalid/push")
        self.assertEqual(set(json.loads(request.data)["body"].splitlines()),
                         {PANIC, "DEGRADED_LEXICON", XUEQIU_UNAUTHORIZED})
        self.assert_rclone_commands(dry_run=False)
        for secret in (SMTP_ENV["EMOTION_INDEX_SMTP_PASSWORD"], BARK_ENV["EMOTION_INDEX_BARK_KEY"],
                       "fixture-deepseek-key"):
            self.assertNotIn(secret, cn_html)
        self.assertEqual(len(EmotionLedger(Market.CN, self.runtime).path.read_bytes().splitlines()), 1)

        before = tuple(path.read_bytes() for path in us_paths)
        self.assertEqual(self.cli_report(Market.US), us_paths)
        self.assertEqual(tuple(path.read_bytes() for path in us_paths), before)
        self.assertEqual(self.deepseek.call_count, 4)
        self.assertEqual(len(us_ledger.path.read_bytes().splitlines()), 26)

    def test_cutoff_at_capture_start_excludes_later_responses_from_all_tracks(self):
        self.capture(Market.CN, CN_CAPTURE)
        result = report_market(Market.CN, self.runtime, cutoff_at=CN_CAPTURE).summary
        self.assertEqual(result["cutoff_at"], CN_CAPTURE.isoformat())
        self.assertEqual(result["scoring_mode"], "NO_POSTS")
        self.assertEqual(result["volume_flag"], "NO_SOURCE_DATA")
        self.assertEqual(result["audit"]["scoring"]["late_unavailable_count"], 1)
        self.assertEqual(result["audit"]["anchor"]["late_responses"], 7)
        self.assertIsNone(result["market_anchor"])
        self.assertIsNone(result["composite_score"])
        self.assertIsNone(result["volume_zscore_20d"])
        self.assertNotIn(XUEQIU_UNAUTHORIZED, result["status_flags"])
        self.deepseek.assert_not_called()
        recorded, = EmotionLedger(Market.CN, self.runtime).read_history(
            before=DAY + timedelta(days=1), cutoff_at=NOW,
        )
        self.assertEqual(recorded.volume_observations(), ())

    def test_retries_finish_a_staged_report_without_rescoring(self):
        self.capture(Market.US, US_CAPTURE)
        json_path = self.runtime / "artifacts" / "daily" / f"emotion_summary_US_{DAY}.json"

        def fail_commit(path, content):
            if path == json_path:
                raise OSError("simulated summary publication failure")
            return atomic_publish_bytes(path, content)

        with patch("emotion_index.app.daily_report.atomic_publish_bytes", side_effect=fail_commit):
            with self.assertRaisesRegex(OSError, "publication failure"):
                report_market(Market.US, self.runtime)
        self.assertFalse(json_path.exists())
        html_path = json_path.with_name(f"EMOTION_US_{DAY}_close.html")
        html_bytes = html_path.read_bytes()
        with patch("emotion_index.app.daily_report.collect_market_scores", side_effect=AssertionError):
            finished = report_market(Market.US, self.runtime)
            self.verify_report(finished.html_path, finished.summary_path, Market.US)
            self.assertEqual(html_path.read_bytes(), html_bytes)
            self.assertEqual(len(EmotionLedger(Market.US, self.runtime).path.read_bytes().splitlines()), 1)
            with self.assertRaisesRegex(ValueError, "cutoff_at"):
                report_market(Market.US, self.runtime, cutoff_at=US_CAPTURE)
        self.assertFalse((self.runtime / "tmp" / "daily" / json_path.name).exists())

    def test_scheduled_run_skips_configured_closures_and_never_reuses_yesterday(self):
        with patch.dict("os.environ", {"EMOTION_INDEX_CN_MARKET_HOLIDAYS": DAY.isoformat()}):
            self.assertEqual(main(["capture", "--market", "CN", "--current-session"]), 0)
            self.assertEqual(main(["report", "--market", "CN", "--current-session"]), 0)
        self.provider.assert_not_called()
        self.capture(Market.CN, CN_CAPTURE - timedelta(days=3))
        with patch("emotion_index.app.cli.report_market", partial(report_market, runtime=self.runtime)), redirect_stderr(
            io.StringIO(),
        ) as errors, self.assertRaises(SystemExit) as failed:
            main(["report", "--market", "CN", "--current-session"])
        self.assertEqual(failed.exception.code, 2)
        self.assertIn(DAY.isoformat(), errors.getvalue())

    def assert_rclone_commands(self, *, dry_run):
        directories = [("data/raw", "raw"), ("data/ledger", "ledger"),
                       ("artifacts/daily", "artifacts/daily"), ("cache", "cache")]
        self.assertEqual(self.rclone.call_count, len(directories))
        for call, (source, dest) in zip(self.rclone.call_args_list, directories, strict=True):
            arguments = ["rclone", "copy", str(self.runtime / source), f"workdrive:EmotionIndex/{dest}",
                         "--exclude", ".*.tmp"]
            if dest == "cache":
                arguments += ["--exclude", "*.sqlite-{journal,wal,shm}"]
            if dry_run:
                arguments.append("--dry-run")
            self.assertEqual(call.args, (arguments,))
            self.assertEqual(call.kwargs, {"check": True, "timeout": 1800})

    def test_sync_preview_and_failed_backup_preserve_raw_then_success_prunes(self):
        self.capture(Market.US, US_CAPTURE)
        report = report_market(Market.US, self.runtime)
        platform = self.runtime / "data" / "raw" / "US" / "bluesky"
        remove = platform / f"{DAY - timedelta(days=60)}.json"
        keep = platform / f"{DAY - timedelta(days=59)}.json"
        unknown = platform / "unrelated.json"
        receipt = (platform.parent / "_receipts" / (DAY - timedelta(days=60)).isoformat()
                   / ("a" * 32 + ".json"))
        receipt.parent.mkdir()
        for path in (remove, keep, unknown, receipt):
            path.write_bytes(b"synthetic raw")
        deliver_report(report, self.runtime, notify=True, sync=True, dry_run=True, prune_raw=True)
        self.assert_rclone_commands(dry_run=True)
        self.smtp.assert_not_called()
        self.bark.assert_not_called()
        self.assertTrue(remove.exists())
        self.rclone.reset_mock()
        self.rclone.side_effect = subprocess.CalledProcessError(1, ["rclone"])
        with self.assertRaisesRegex(OSError, "rclone"):
            deliver_report(report, self.runtime, sync=True, prune_raw=True)
        self.assertTrue(remove.exists())
        self.assertTrue(receipt.exists())
        self.rclone.reset_mock(side_effect=True)
        deliver_report(report, self.runtime, sync=True, prune_raw=True)
        self.assert_rclone_commands(dry_run=False)
        self.assertFalse(remove.exists())
        self.assertFalse(receipt.parent.exists())
        self.assertTrue(keep.exists())
        self.assertTrue(unknown.exists())

    def test_notifier_ignores_other_flags_and_sends_all_documented_alerts(self):
        notify_report("subject", "body", "<html></html>", "fixture.html", (WEAK_MARKET, "XHS_NOT_PROVIDED"),
                      env=BARK_ENV)
        self.bark.assert_not_called()
        for flag in sorted(BARK_FLAGS):
            notify_report("subject", "body", "<html></html>", "fixture.html", (flag,), env=BARK_ENV)
            self.assertEqual(json.loads(self.bark.call_args.args[0].data)["body"], flag)
        self.assertEqual(self.bark.call_count, 4)
        self.smtp.side_effect = OSError("fixture-smtp-password")
        self.bark.reset_mock()
        with self.assertRaisesRegex(OSError, "^日报通知失败：SMTP$"):
            notify_report("subject", "body", "<html></html>", "fixture.html", (EUPHORIA,),
                          env={**SMTP_ENV, **BARK_ENV})
        self.bark.assert_called_once()

    def test_json_audit_cannot_close_the_script_element(self):
        self.capture(Market.US, US_CAPTURE)
        summary = report_market(Market.US, self.runtime).summary
        summary["audit"]["test"] = "</script><img src=example.invalid>"
        html = render_daily_html(json.dumps(summary))
        self.assertNotIn("<img", html)
        payload = html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
        self.assertEqual(json.loads(payload), summary)

    def test_yahoo_duplicate_same_day_timestamps_keep_latest_bar_and_recovered_xueqiu_clears_alert(self):
        # Yahoo chart API often appends a second intraday/close timestamp for the latest session.
        original_api = self.public_api

        def api_with_dup_yahoo_and_recovered_xueqiu(request, timeout):
            url = urlsplit(request.full_url)
            if url.hostname == "query1.finance.yahoo.com":
                symbol = unquote(url.path.rsplit("/", 1)[1])
                days = (*prior_sessions(5), DAY)
                prices = {"^VIX": 28, "SPY": 99, "QQQ": 98, "XLF": 99, "IWM": 98, "XLU": 101}
                stamps = [datetime.combine(day, time(14, 30), timezone.utc).timestamp() for day in days]
                stamps.append(datetime.combine(DAY, time(20, 0), timezone.utc).timestamp())
                closes = [20 if symbol == "^VIX" else 100] * 5 + [prices[symbol] + 0.5, prices[symbol]]
                return json_response({"chart": {"error": None, "result": [{
                    "timestamp": stamps, "indicators": {"quote": [{"close": closes}]},
                }]}})
            if url.path == "/xueqiu":
                xml = (
                    f"<rss><channel><item><guid>xq-ok</guid><title>科技股反弹</title>"
                    f"<pubDate>{(CN_CAPTURE - timedelta(minutes=20)).isoformat()}</pubDate>"
                    "</item></channel></rss>"
                )
                stream = io.BytesIO(xml.encode())
                stream.code = 200
                return stream
            return original_api(request, timeout)

        # First CN capture gets Xueqiu 403; second CN capture recovers after cookie renewal.
        self.capture(Market.CN, CN_CAPTURE)
        self.provider.side_effect = api_with_dup_yahoo_and_recovered_xueqiu
        self.capture(Market.CN, CN_CAPTURE + timedelta(minutes=2))
        cn_summary = report_market(Market.CN, self.runtime).summary
        self.assertNotIn(XUEQIU_UNAUTHORIZED, cn_summary["status_flags"])

        self.capture(Market.US, US_CAPTURE)
        us_summary = report_market(Market.US, self.runtime).summary
        self.assertIsNotNone(us_summary["market_anchor"])
        self.assertEqual(us_summary["audit"]["anchor"]["invalid_responses"], 0)


class LedgerAndAnchorTests(unittest.TestCase):
    def test_ledger_concurrent_upserts_replace_once_and_filter_at_cutoff(self):
        temporary_root = Path(".runtime/tmp")
        temporary_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=temporary_root) as temp:
            runtime = Path(temp)
            ledger = EmotionLedger(Market.US, runtime)
            days = prior_sessions(10)
            entries = [ledger_row(Market.US, day, idx) for idx, day in enumerate(days)]
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(ledger.upsert, entries + list(reversed(entries))))
            before = ledger.path.read_bytes()
            ledger.upsert(entries[0])
            self.assertEqual(ledger.path.read_bytes(), before)
            updated = replace(entries[-1], cutoff_at=NOW, composite_score=60)
            ledger.upsert(updated)
            self.assertEqual(ledger.read_history(before=DAY, cutoff_at=US_CAPTURE), tuple(entries[:-1]))
            self.assertEqual(ledger.read_history(before=DAY, cutoff_at=NOW)[-1], updated)
            self.assertEqual(len(ledger.path.read_bytes().splitlines()), 10)
            self.assertEqual(EmotionLedger(Market.CN, runtime).read_history(before=DAY, cutoff_at=NOW), ())
            with self.assertRaisesRegex(ValueError, "earlier cutoff"):
                ledger.upsert(entries[-1])

    def test_anchor_uses_vix_five_session_change_and_excludes_stale_or_late_components(self):
        bars = tuple(DailyBar(day, 20, None) for day in prior_sessions(5))
        vix = MarketSeries("^VIX", US_CAPTURE, (*bars, DailyBar(DAY, 25, None)))
        stale = MarketSeries("QQQ", US_CAPTURE, bars)
        late = MarketSeries("SPY", NOW, (*bars, DailyBar(DAY, 1000, None)))
        cnn = MarketAnchor(Market.US, DAY, NOW, 100)
        anchor, = build_market_anchors(Market.US, DAY, US_CAPTURE, (vix, stale, late), cnn)
        self.assertAlmostEqual(anchor.anchor_score, 15.0)
        self.assertEqual(anchor.available_at, US_CAPTURE)
        self.assertEqual(build_market_anchors(Market.US, DAY, US_CAPTURE, (stale, late), cnn), ())

    def test_cn_anchor_uses_twenty_day_mean_volume_to_confirm_price_direction(self):
        bars = tuple(DailyBar(day, 100, 100) for day in prior_sessions(20))
        ordinary = MarketSeries("sh000300", CN_CAPTURE, (*bars, DailyBar(DAY, 102, 100)))
        busy = replace(ordinary, bars=(*bars, DailyBar(DAY, 102, 160)))
        mean_anchor, = build_market_anchors(Market.CN, DAY, CN_CAPTURE, (ordinary,))
        busy_anchor, = build_market_anchors(Market.CN, DAY, CN_CAPTURE, (busy,))
        self.assertAlmostEqual(mean_anchor.anchor_score, 70)
        self.assertAlmostEqual(busy_anchor.anchor_score, 80)


class SystemdTemplateTests(unittest.TestCase):
    def test_calendar_and_service_commands_match_market_sessions(self):
        directory = Path(__file__).resolve().parents[1] / "deploy" / "systemd"
        for service, calendar, commands in (
            ("cn-capture", "16:00:00 Asia/Shanghai", ("capture --market CN --current-session",)),
            ("cn-report", "18:05:00 Asia/Shanghai",
             ("report --market CN --current-session --notify --sync --prune-raw",)),
            ("us-daily", "17:30:00 America/New_York",
             ("capture --market US --current-session", "report --market US --current-session --notify --sync --prune-raw")),
        ):
            with self.subTest(service=service):
                timer = (directory / f"emotion-index-{service}.timer").read_text()
                self.assertIn(f"OnCalendar=Mon..Fri *-*-* {calendar}", timer)
                self.assertIn("Persistent=false", timer)
                unit = (directory / f"emotion-index-{service}.service").read_text()
                self.assertIn("EnvironmentFile=/etc/emotion-index/emotion-index.env", unit)
                actual = [line.split("/.venv/bin/emotion-index ", 1)[1] for line in unit.splitlines()
                          if line.startswith("ExecStart=")]
                self.assertEqual(tuple(actual), commands)
                self.assertNotIn("--allow-peak", unit)


if __name__ == "__main__":
    unittest.main()
