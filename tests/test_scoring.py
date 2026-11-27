import base64
import hashlib
import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from emotion_index.app.cli import main
from emotion_index.app.scoring import score_market
from emotion_index.domain.market import Market
from emotion_index.integrations.deepseek_client import (
    MODEL_NAME, PROMPT_VERSION, DeepSeekClient, DeepSeekUnavailable, PeakHoursError,
)
from emotion_index.integrations.score_cache import ScoreCache

CAPTURED = datetime(2026, 11, 27, 8, tzinfo=timezone.utc)
OFF_PEAK = datetime(2026, 11, 27, 10, 5, tzinfo=timezone.utc)
DAY = CAPTURED.date()
POST = (("bluesky", "I am bullish on $SMH"),)


def reply(content, finish_reason="stop"):
    return io.BytesIO(json.dumps({
        "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
    }).encode())


def payload(request):
    return json.loads(request.data)


def input_posts(request):
    return json.loads(payload(request)["messages"][1]["content"])["posts"]


def good_row(post_id):
    return {
        "post_id": post_id, "polarity": -0.75, "emotion_state": "PANIC_CAPITULATION",
        "sector": "TECH_GROWTH", "is_sarcasm": True, "confidence": 0.88, "reason_short": "反讽吐槽暴跌",
    }


def deepseek_stub(request, timeout):
    rows = [good_row(post["post_id"]) for post in reversed(input_posts(request))]
    return reply(json.dumps({"scores": rows}))


class ScoringTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(".runtime/tmp")
        temporary_root.mkdir(parents=True, exist_ok=True)
        temp = self.enterContext(TemporaryDirectory(dir=temporary_root))
        self.runtime = Path(temp)
        self.database = self.runtime / "cache" / "llm_scores.sqlite"
        self.enterContext(patch("emotion_index.app.scoring.utc_now", return_value=OFF_PEAK))
        self.credentials = self.enterContext(patch.dict("os.environ", {
            "DEEPSEEK_API_KEY": "synthetic-deepseek-key",
            "EMOTION_INDEX_DEEPSEEK_HOLIDAYS": "",
        }))
        self.network = self.enterContext(patch(
            "emotion_index.integrations.deepseek_client.urlopen", side_effect=deepseek_stub,
        ))
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("Live network is forbidden")))

    def freeze(self, platform, *observations, market=Market.US, day=DAY):
        path = self.runtime / "data" / "raw" / market.value / platform / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        for source, fetched, item in observations:
            body = item if isinstance(item, bytes) else json.dumps(item).encode()
            entries.append({
                "source": source, "reason": None, "http_status": 200,
                "fetched_at": fetched.isoformat(), "available_at": fetched.isoformat(),
                "payload_base64": base64.b64encode(body).decode(), "sha256": hashlib.sha256(body).hexdigest(),
            })
        snapshot_time = max(entry["available_at"] for entry in entries)
        path.write_text(json.dumps({"schema_version": 1, "market": market.value, "platform": platform,
                                    "capture_date": day.isoformat(), "responses": entries,
                                    "fetched_at": snapshot_time, "available_at": snapshot_time}), encoding="utf-8")
        return path

    @staticmethod
    def bluesky(index, text=None, published=None, likes=None):
        return {
            "uri": f"at://did:example/post/{index}",
            "record": {"text": f"bullish $SMH {index}" if text is None else text,
                       "createdAt": (published or CAPTURED - timedelta(hours=1)).isoformat()},
            "author": {"did": "did:example"}, "likeCount": index if likes is None else likes,
        }

    def score(self, market=Market.US, *, now=OFF_PEAK, **kwargs):
        return score_market(market, self.runtime, client=DeepSeekClient(clock=lambda: now), **kwargs)

    def test_beijing_peak_boundaries_weekends_and_configured_holiday(self):
        monday = datetime(2026, 11, 23, tzinfo=timezone.utc)
        for hour, minute, blocked in [(0, 59, False), (1, 0, True), (3, 59, True), (4, 0, False),
                                      (5, 59, False), (6, 0, True), (9, 59, True), (10, 0, False)]:
            with self.subTest(hour=hour, minute=minute):
                self.network.reset_mock()
                client = DeepSeekClient(clock=lambda: monday.replace(hour=hour, minute=minute))
                if blocked:
                    with self.assertRaisesRegex(PeakHoursError, "--allow-peak"):
                        client.score_batch(POST)
                    self.network.assert_not_called()
                    client.score_batch(POST, allow_peak=True)
                else:
                    client.score_batch(POST)
                self.network.assert_called_once()
        for day in (date(2026, 11, 28), date(2026, 11, 29), date(2026, 11, 30)):
            with self.subTest(day=day):
                self.network.reset_mock()
                self.credentials["EMOTION_INDEX_DEEPSEEK_HOLIDAYS"] = "2026-11-30"
                client = DeepSeekClient(clock=lambda: datetime(day.year, day.month, day.day, 2, tzinfo=timezone.utc))
                client.score_batch(POST)
                self.network.assert_called_once()

    def test_retry_rechecks_time_gate_before_resending(self):
        clock = iter([OFF_PEAK.replace(hour=0, minute=59)] * 2 + [OFF_PEAK.replace(hour=1, minute=0)])
        client = DeepSeekClient(clock=lambda: next(clock))
        self.network.side_effect = TimeoutError
        with self.assertRaises(PeakHoursError):
            client.score_batch(POST)
        self.network.assert_called_once()

    def test_cli_blocks_peak_then_explicit_override_emits_json(self):
        self.freeze("bluesky", ("tech_growth", CAPTURED, {"posts": [self.bluesky(1), self.bluesky(2)]}))

        def local_score(market, **kwargs):
            return self.score(market, now=CAPTURED, **kwargs)

        with patch("emotion_index.app.cli.score_market", side_effect=local_score):
            with redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as exit_context:
                main(["score", "--market", "US"])
            self.assertEqual(exit_context.exception.code, 2)
            self.assertIn("高峰门禁", errors.getvalue())
            self.network.assert_not_called()
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["score", "--market", "US", "--allow-peak", "--daily-limit", "1"]), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["audit"]["allow_peak"])
        self.assertEqual(result["audit"]["daily_limit"], 1)
        self.assertEqual(result["scoring_mode"], "DEEPSEEK")
        self.assertEqual(result["scores"][0]["post_id"], "at://did:example/post/2")
        self.assertEqual(result["audit"]["unselected_posts"], 1)
        self.network.assert_called_once()

    def test_top_100_batches_cache_and_persistent_daily_quota(self):
        self.freeze("bluesky", ("tech_growth", CAPTURED, {"posts": [self.bluesky(i) for i in range(105)]}))
        first = self.score()
        self.assertEqual(first["audit"]["eligible_posts"], 105)
        self.assertEqual(first["audit"]["selected_posts"], 100)
        self.assertEqual(first["audit"]["unselected_posts"], 5)
        self.assertEqual(first["audit"]["submitted_texts"], 100)
        self.assertEqual(first["audit"]["cache_hits"], 0)
        batches = []
        for call in self.network.call_args_list:
            request = call.args[0]
            self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-deepseek-key")
            self.assertEqual(payload(request)["model"], MODEL_NAME)
            self.assertEqual(payload(request)["response_format"], {"type": "json_object"})
            batches.append(input_posts(request))
        self.assertEqual([len(batch) for batch in batches], [25] * 4)
        self.assertEqual([post["text"] for batch in batches for post in batch],
                         [f"bullish $SMH {i}" for i in range(104, 4, -1)])
        first_row = first["scores"][0]
        self.assertEqual(first_row["post_id"], "at://did:example/post/104")
        for field, value in good_row(first_row["post_id"]).items():
            self.assertEqual(first_row[field], value)
        with ScoreCache(self.database) as cache:
            stored = set(cache.connection.execute("SELECT content_hash, prompt_version, model_name FROM llm_scores"))
        key = hashlib.sha256(b"blueskybullish $SMH 104").hexdigest()
        self.assertIn((key, PROMPT_VERSION, MODEL_NAME), stored)

        self.network.reset_mock()
        del self.credentials["DEEPSEEK_API_KEY"]
        second = self.score(now=CAPTURED)
        self.network.assert_not_called()
        self.assertEqual(second["audit"]["cache_hits"], 100)
        self.assertEqual(second["audit"]["submitted_texts"], 0)
        self.assertEqual(second["scores"], [{**row, "cache_hit": True} for row in first["scores"]])

        self.credentials["DEEPSEEK_API_KEY"] = "synthetic-deepseek-key"
        previous_day = DAY - timedelta(days=1)
        previous_time = CAPTURED - timedelta(days=1)
        self.freeze("bluesky", ("tech_growth", previous_time, {
            "posts": [self.bluesky(999, published=previous_time)],
        }), day=previous_day)
        exhausted = self.score(capture_date=previous_day)
        self.network.assert_not_called()
        self.assertEqual(exhausted["audit"]["submitted_texts"], 0)
        self.assertEqual(exhausted["scoring_mode"], "DEGRADED_LEXICON")
        self.freeze("xhs", ("cn.json", CAPTURED, {"market": "CN", "posts": [
            {"post_id": "cn-1", "text": "A股利好", "published_at": CAPTURED.isoformat()},
        ]}), market=Market.CN)
        self.assertEqual(self.score(Market.CN)["audit"]["submitted_texts"], 1)
        self.network.assert_called_once()

    def test_same_normalized_text_submits_only_once_and_cache_uses_each_post_id(self):
        self.freeze("bluesky", ("tech_growth", CAPTURED, {"posts": [
            self.bluesky(1, "Café  bullish\n$SMH"),
            self.bluesky(2, "Cafe\u0301 bullish $SMH"),
        ]}))
        first = self.score()
        self.assertEqual(first["audit"]["submitted_texts"], 1)
        request = self.network.call_args.args[0]
        self.assertEqual(input_posts(request), [{"post_id": "0", "platform": "bluesky", "text": "Café bullish $SMH"}])
        self.network.reset_mock()
        second = self.score()
        self.network.assert_not_called()
        self.assertEqual(second["audit"]["cache_hits"], 2)
        self.assertEqual({row["post_id"] for row in second["scores"]},
                         {"at://did:example/post/1", "at://did:example/post/2"})

    def test_invalid_json_exhausts_two_retries_and_falls_back_bilingually(self):
        self.freeze("bluesky", ("tech_growth", CAPTURED, {"posts": [
            self.bluesky(1, "芯片暴跌，恐慌割肉"),
            self.bluesky(2, "Bullish breakout, strong rally"),
        ]}))
        self.network.side_effect = lambda *args, **kwargs: reply("{")
        result = self.score()
        self.assertEqual(self.network.call_count, 3)
        self.assertEqual(result["scoring_mode"], "DEGRADED_LEXICON")
        self.assertEqual(result["audit"]["scoring_mode"], result["scoring_mode"])
        self.assertEqual(result["audit"]["fallback_reason"], "DEEPSEEK_INVALID_RESPONSE")
        self.assertEqual(result["audit"]["alert_flags"], ["DEEPSEEK_FALLBACK"])
        self.assertEqual(result["audit"]["lexicon_model"], "bilingual_lexicon_v1")
        self.assertEqual(result["audit"]["degraded_posts"], 2)
        self.assertGreater(result["scores"][0]["polarity"], 0)
        self.assertLess(result["scores"][1]["polarity"], 0)
        self.assertEqual(result["scores"][1]["emotion_state"], "PANIC_CAPITULATION")
        self.assertIsNone(result["scores"][1]["is_sarcasm"])

        self.network.reset_mock(side_effect=True)
        self.network.side_effect = deepseek_stub
        same_day = self.score()
        self.network.assert_not_called()
        self.assertEqual(same_day["scoring_mode"], "DEGRADED_LEXICON")
        self.assertEqual(same_day["audit"]["cache_hits"], 0)
        recovered = self.score(now=OFF_PEAK + timedelta(days=1))
        self.assertEqual(recovered["scoring_mode"], "DEEPSEEK")
        self.assertEqual(recovered["audit"]["cache_hits"], 0)
        self.network.assert_called_once()

    def test_partial_failure_preserves_successful_batches_for_the_next_run(self):
        self.freeze("bluesky", ("tech_growth", CAPTURED, {"posts": [self.bluesky(i) for i in range(41)]}))

        def fail_second_batch(request, timeout):
            if len(input_posts(request)) == 20:
                raise TimeoutError
            return deepseek_stub(request, timeout)

        self.network.side_effect = fail_second_batch
        first = self.score()
        self.assertEqual(first["scoring_mode"], "DEGRADED_LEXICON")
        self.assertEqual(first["audit"]["degraded_posts"], 20)
        self.assertEqual(first["audit"]["submitted_texts"], 41)
        self.assertEqual(self.network.call_count, 4)

        self.network.reset_mock()
        self.network.side_effect = deepseek_stub
        recovered = self.score(now=OFF_PEAK + timedelta(days=1))
        self.assertEqual(recovered["scoring_mode"], "DEEPSEEK")
        self.assertEqual(recovered["audit"]["cache_hits"], 21)
        self.assertEqual(recovered["audit"]["submitted_texts"], 20)
        self.network.assert_called_once()

    def test_timeout_missing_key_and_insufficient_balance_degrade(self):
        self.freeze("bluesky", ("tech_growth", CAPTURED, {"posts": [self.bluesky(1)]}))
        del self.credentials["DEEPSEEK_API_KEY"]
        self.assertEqual(self.score()["audit"]["fallback_reason"], "DEEPSEEK_API_KEY_MISSING")
        self.network.assert_not_called()
        self.credentials["DEEPSEEK_API_KEY"] = "synthetic-deepseek-key"
        self.network.side_effect = URLError(TimeoutError("synthetic timeout"))
        timed_out = self.score()
        self.assertEqual(timed_out["scoring_mode"], "DEGRADED_LEXICON")
        self.assertEqual(timed_out["audit"]["fallback_reason"], "DEEPSEEK_TIMEOUT")
        self.assertEqual(self.network.call_count, 3)

        self.network.reset_mock()
        self.network.side_effect = HTTPError("https://api.deepseek.com", 402, "sensitive body", {}, io.BytesIO(b"error"))
        balance = self.score(now=OFF_PEAK + timedelta(days=1))
        self.assertEqual(balance["scoring_mode"], "DEGRADED_LEXICON")
        self.assertEqual(balance["audit"]["fallback_reason"], "DEEPSEEK_HTTP_402")
        self.network.assert_called_once()

    def test_invalid_schema_or_batch_id_never_yields_a_score(self):
        client = DeepSeekClient(clock=lambda: OFF_PEAK)
        for invalid in [
            {"post_id": "wrong"}, {"polarity": True}, {"emotion_state": "FEAR"},
            {"sector": "QQQ"}, {"is_sarcasm": "false"}, {"confidence": 1.1}, {"reason_short": " "},
        ]:
            with self.subTest(invalid=invalid):
                self.network.reset_mock()
                row = {**good_row("0"), **invalid}
                self.network.side_effect = lambda *args, **kwargs: reply(json.dumps({"scores": [row]}))
                with self.assertRaisesRegex(DeepSeekUnavailable, "INVALID_RESPONSE"):
                    client.score_batch(POST)
                self.assertEqual(self.network.call_count, 3)

    def test_verified_snapshots_preserve_pit_and_deduplicate_first_observation(self):
        early = CAPTURED - timedelta(minutes=1)
        late = CAPTURED + timedelta(minutes=1)
        self.freeze(
            "bluesky",
            ("broad_market", early, {"posts": [self.bluesky(1, "original bullish $SMH"),
                self.bluesky(3, published=CAPTURED - timedelta(days=2)),
                self.bluesky(4, published=late)]}),
            ("tech_growth", late, {"posts": [self.bluesky(1, "later revision", likes=99999), self.bluesky(2)]}),
        )
        self.freeze("stocktwits", ("XLU", early, {"messages": [{
            "id": 200, "body": "$XLU breakout", "created_at": early.isoformat(), "likes": {"total": 20},
        }]}))
        self.freeze("xueqiu", ("xueqiu", early, b"""
          <rss version="2.0"><channel><item><title>$XLF rally</title><guid>rss-1</guid>
          <description><![CDATA[<p>strong</p>]]></description><pubDate>Fri, 27 Nov 2026 07:00:00 GMT</pubDate>
          </item></channel></rss>
        """))
        self.freeze("chinese_news", ("wallstreetcn", early, b"""
          <feed xmlns="http://www.w3.org/2005/Atom"><entry><id>atom-1</id><title>$QQQ rebound</title>
          <published>2026-11-27T07:00:00Z</published><updated>2026-11-27T07:10:00Z</updated></entry></feed>
        """))
        self.freeze("xhs", ("us.json", early, {"market": "US", "posts": [
            {"post_id": "xhs-1", "text": "小盘股看空", "tickers": ["IWM"],
             "published_at": "2026-11-27T07:10:00Z", "available_at": "2026-11-27T07:15:00Z"},
        ]}))
        self.freeze("apewisdom", ("all-stocks", early, {"results": [{"ticker": "SMH", "mentions": "80"}]}))
        result = self.score(cutoff_at=CAPTURED)
        self.assertEqual(result["audit"]["out_of_window_count"], 1)
        self.assertEqual(result["audit"]["late_unavailable_count"], 1)
        self.assertEqual(result["audit"]["duplicate_posts"], 1)
        self.assertEqual(result["audit"]["sources"]["bluesky"]["invalid_rows"], 1)
        self.assertEqual(result["audit"]["sources"]["apewisdom"]["status"], "AGGREGATE_ONLY")
        by_id = {row["post_id"]: row for row in result["scores"]}
        self.assertEqual(set(by_id), {"at://did:example/post/1", "200", "rss-1", "atom-1", "xhs-1"})
        self.assertEqual(by_id["200"]["routed_sector"], "DEFENSIVE_DIVIDEND")
        self.assertEqual(by_id["rss-1"]["routed_sector"], "CYCLICAL_FINANCE")
        self.assertEqual(by_id["xhs-1"]["routed_sector"], "HIGH_BETA_THEME")
        self.assertEqual(by_id["xhs-1"]["available_at"], early.isoformat())
        sent = {post["text"] for post in input_posts(self.network.call_args.args[0])}
        self.assertIn("original bullish $SMH", sent)
        self.assertNotIn("later revision", sent)

    def test_invalid_identity_or_hash_is_rejected_before_http(self):
        path = self.freeze("bluesky", ("tech_growth", CAPTURED, {"posts": [self.bluesky(1)]}))
        original = json.loads(path.read_bytes())
        for invalid in ("wrong_market", "bad_hash"):
            with self.subTest(invalid=invalid):
                document = json.loads(json.dumps(original))
                if invalid == "wrong_market":
                    document["market"] = "CN"
                else:
                    document["responses"][0]["sha256"] = "0" * 64
                path.write_text(json.dumps(document), encoding="utf-8")
                result = self.score()
                self.assertEqual(result["scoring_mode"], "NO_POSTS")
                self.assertEqual(result["audit"]["sources"]["bluesky"]["status"], "SNAPSHOT_UNREADABLE_OR_INVALID")
                self.network.assert_not_called()

    def test_concurrent_reservations_keep_market_under_daily_limit(self):
        def reserve(start):
            keys = tuple(str(index) for index in range(start, start + 8))
            with ScoreCache(self.database) as cache:
                return cache.reserve(Market.US, DAY, keys, 10)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.map(reserve, (0, 4))
        self.assertEqual(len(first) + len(second), 10)
        self.assertFalse(set(first) & set(second))
