import base64
import hashlib
import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from emotion_index.app.cli import main
from emotion_index.domain.market import Market
from emotion_index.domain.models import ObservationWindow, parse_utc_timestamp
from emotion_index.integrations.capture import capture_market, load_available_responses
from emotion_index.integrations.providers.xhs_inbox import parse_posts
from emotion_index.integrations.storage import atomic_publish_bytes
from emotion_index.sources.filter import filter_point_in_time_posts

NOW = datetime(2026, 11, 27, 22, 30, tzinfo=timezone.utc)
DAY = NOW.date().isoformat()
RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>Markets</title></channel></rss>\r\n'
ATOM = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Markets</title></feed>'
APES = b'{  "results": [{ "ticker": "SPY", "mentions": "9" }] }\r\n'
STOCKTWITS = b'{"messages":[{"id":1,"entities":{"sentiment":{"basic":"Bullish"}}}]}'


def response(body, status=200):
    stream = io.BytesIO(body)
    stream.code = status
    return stream


def public_api_stub(request, timeout):
    url = urlsplit(request.full_url)
    if url.netloc == "apewisdom.io" and url.path == "/api/v1.0/filter/all-stocks":
        return response(APES)
    if url.netloc == "public.api.bsky.app" and url.path.endswith("searchPosts"):
        if parse_qs(url.query)["sort"] != ["latest"]:
            raise AssertionError("Bluesky capture must request latest posts")
        return response(b'{"posts":[]}')
    if url.netloc == "api.stocktwits.com" and url.path.startswith("/api/2/streams/symbol/"):
        return response(STOCKTWITS)
    if url.netloc == "production.dataviz.cnn.io":
        return response(b'{"fear_and_greed":{"score":52.0}}')
    if url.netloc == "query1.finance.yahoo.com":
        if parse_qs(url.query) != {"range": ["3mo"], "interval": ["1d"]}:
            raise AssertionError("Yahoo histories must include the prior trading sessions")
        return response(b'{"chart":{"result":[{"timestamp":[1],"indicators":{"quote":[{"close":[10]}]}}],"error":null}}')
    if url.netloc == "finnhub.io" and url.path == "/api/v1/news-sentiment":
        if request.get_header("X-finnhub-token") != "synthetic-finnhub-token":
            raise AssertionError("Finnhub credential was not sent")
        return response(b'{"sentiment":{"bullishPercent":0.55}}')
    if url.netloc == "web.ifzq.gtimg.cn":
        symbol, period, _, _, count_ = parse_qs(url.query)["param"][0].split(",")
        if period != "day" or int(count_) <= 20:
            raise AssertionError("Tencent histories must include the prior trading sessions")
        key = "qfqday" if symbol in {"sh510880", "sh563300"} else "day"
        body = {"code": 0, "data": {symbol: {key: [["2026-11-27", "1", "2", "2", "1", "100"]]}}}
        return response(json.dumps(body).encode())
    if url.netloc == "feeds.invalid":
        return response(ATOM if "jin10" in url.path else RSS)
    raise AssertionError(f"Unexpected network request: {url.netloc}{url.path}")


class CaptureTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(".runtime/tmp")
        temporary_root.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=temporary_root)
        self.addCleanup(temp.cleanup)
        self.runtime = Path(temp.name)
        self.network = self.enterContext(patch(
            "emotion_index.integrations.providers.http.urlopen", side_effect=public_api_stub,
        ))
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("Live network is forbidden")))

    def capture(self, market=Market.US, env=None, clock=None):
        receipt = capture_market(market, self.runtime, env=env or {}, clock=clock or (lambda: NOW))
        return json.loads(receipt.read_bytes())

    def snapshot_path(self, market, platform):
        return self.runtime / "data" / "raw" / market.value / platform / f"{DAY}.json"

    def snapshot(self, market, platform):
        return json.loads(self.snapshot_path(market, platform).read_bytes())

    def platforms(self, receipt):
        return {item["platform"]: item for item in receipt["platforms"]}

    def inbox(self, filename, market, posts):
        path = self.runtime / "inbox" / "xhs" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"market": market, "posts": posts}, ensure_ascii=False).encode()
        path.write_bytes(body)
        return body

    def test_us_capture_freezes_exact_bytes_and_routes_all_providers(self):
        env = {f"EMOTION_INDEX_RSS_US_{name.upper()}_URL": f"https://feeds.invalid/us/{name}"
               for name in ("xueqiu", "xueqiu_user", "cls", "wallstreetcn", "jin10")}
        env["EMOTION_INDEX_FINNHUB_TOKEN"] = "synthetic-finnhub-token"
        ticks = count()
        clock = lambda: NOW.astimezone(timezone(timedelta(hours=8))) + timedelta(seconds=next(ticks))
        receipt = self.capture(env=env, clock=clock)
        platforms = self.platforms(receipt)
        self.assertEqual(set(platforms), {
            "apewisdom", "bluesky", "stocktwits", "xueqiu", "chinese_news", "us_anchor", "xhs",
        })
        self.assertTrue(all(item["status"] == "CAPTURED" for name, item in platforms.items() if name != "xhs"))
        self.assertEqual(len(platforms["bluesky"]["responses"]), 5)
        self.assertEqual(len(platforms["stocktwits"]["responses"]), 6)
        self.assertEqual(len(platforms["us_anchor"]["responses"]), 8)

        snapshot = self.snapshot(Market.US, "apewisdom")
        captured = (NOW + timedelta(seconds=1)).isoformat()
        entry, = snapshot["responses"]
        self.assertEqual(snapshot["capture_date"], DAY)
        self.assertEqual(snapshot["available_at"], captured)
        self.assertEqual(entry["available_at"], captured)
        self.assertEqual(entry["fetched_at"], captured)
        self.assertEqual(base64.b64decode(entry["payload_base64"]), APES)
        self.assertEqual(entry["sha256"], hashlib.sha256(APES).hexdigest())
        self.assertNotIn("payload_base64", platforms["apewisdom"]["responses"][0])
        news = self.snapshot(Market.US, "chinese_news")["responses"]
        self.assertEqual({item["source"]: base64.b64decode(item["payload_base64"]) for item in news},
                         {"wallstreetcn": RSS, "jin10": ATOM, "cls": RSS})
        stocktwits = self.snapshot(Market.US, "stocktwits")["responses"][0]
        self.assertEqual(base64.b64decode(stocktwits["payload_base64"]), STOCKTWITS)

        path = self.snapshot_path(Market.US, "stocktwits")
        first = parse_utc_timestamp(stocktwits["available_at"], "available_at")
        self.assertEqual(load_available_responses(path, first - timedelta(microseconds=1)), ())
        only_first, = load_available_responses(path, first)
        self.assertEqual(only_first.source, "SPY")
        self.assertEqual(only_first.body, STOCKTWITS)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            load_available_responses(path, first.replace(tzinfo=None))

    def test_cn_capture_has_all_routes_and_no_us_requests(self):
        names = ("xueqiu", "xueqiu_user", "cls", "wallstreetcn", "jin10", "sina", "jisilu")
        env = {f"EMOTION_INDEX_RSS_CN_{name.upper()}_URL": f"https://feeds.invalid/cn/{name}" for name in names}
        env["EMOTION_INDEX_RSS_US_CLS_URL"] = "https://feeds.invalid/us/cls"
        platforms = self.platforms(self.capture(Market.CN, env=env))
        self.assertEqual(set(platforms), {"xueqiu", "cls", "chinese_news", "jisilu", "cn_anchor", "xhs"})
        self.assertEqual({row["source"] for row in platforms["cn_anchor"]["responses"]}, {
            "tencent:sh000001", "tencent:sh000300", "tencent:sz399006", "tencent:sh000688",
            "tencent:sh512800", "tencent:sh510880", "tencent:sh563300",
        })
        self.assertEqual(platforms["cn_anchor"]["status"], "CAPTURED")
        rss_calls = [call.args[0].full_url for call in self.network.call_args_list if "feeds.invalid" in call.args[0].full_url]
        self.assertCountEqual(rss_calls, [f"https://feeds.invalid/cn/{name}" for name in names])
        self.assertFalse((self.runtime / "data" / "raw" / "US").exists())
        self.assertEqual(platforms["xhs"]["responses"][0]["reason"], "XHS_NOT_PROVIDED")
        self.assertFalse(self.snapshot_path(Market.CN, "xhs").exists())

    def test_failures_remain_local_and_receipts_record_safe_reasons(self):
        def failures(request, timeout):
            url = urlsplit(request.full_url)
            if url.netloc == "apewisdom.io":
                raise HTTPError(request.full_url, 429, "rate limited", {}, io.BytesIO(b'{"error":"rate limit"}'))
            if url.netloc == "public.api.bsky.app":
                raise URLError(TimeoutError("socket timeout"))
            if url.path == "/cn/hots":
                self.assertEqual(request.get_header("Cookie"), "session=synthetic-cookie-secret")
                self.assertNotIn("Cookie", request.headers)
                body = b"RSSHub upstream error 400016: synthetic-cookie-secret"
                raise HTTPError(request.full_url, 500, "upstream unavailable", {}, io.BytesIO(body))
            if url.path == "/cn/jin10":
                return response(b"<html>temporary failure</html>")
            if url.path == "/api/2/streams/symbol/QQQ.json":
                return response(b"<html>not JSON</html>")
            return public_api_stub(request, timeout)

        self.network.side_effect = failures
        us = self.platforms(self.capture())
        self.assertEqual(us["apewisdom"]["status"], "UNAVAILABLE")
        self.assertEqual(us["apewisdom"]["responses"][0]["reason"], "RATE_LIMITED")
        self.assertEqual(us["apewisdom"]["responses"][0]["http_status"], 429)
        self.assertIsNone(us["apewisdom"]["snapshot"])
        self.assertFalse(self.snapshot_path(Market.US, "apewisdom").exists())
        self.assertEqual(us["bluesky"]["responses"][0]["reason"], "TIMEOUT")
        self.assertEqual(us["stocktwits"]["status"], "PARTIAL")
        self.assertEqual(us["stocktwits"]["responses"][1]["reason"], "INVALID_RESPONSE")
        self.assertEqual(len(load_available_responses(self.snapshot_path(Market.US, "stocktwits"), NOW)), 5)
        self.assertEqual(us["us_anchor"]["responses"][-1]["reason"], "SOURCE_NOT_CONFIGURED")
        self.assertFalse(self.snapshot_path(Market.US, "bluesky").exists())

        # All-failed platforms are not frozen, so a retry after recovery succeeds.
        self.network.side_effect = public_api_stub
        retried = self.platforms(self.capture())
        self.assertEqual(retried["apewisdom"]["status"], "CAPTURED")
        self.assertEqual(retried["bluesky"]["status"], "CAPTURED")
        self.assertEqual(retried["stocktwits"]["status"], "REUSED")

        self.network.side_effect = failures
        cn = self.platforms(self.capture(Market.CN, env={
            "EMOTION_INDEX_RSS_CN_XUEQIU_URL": "https://feeds.invalid/cn/hots",
            "EMOTION_INDEX_RSS_CN_XUEQIU_COOKIE": "session=synthetic-cookie-secret",
            "EMOTION_INDEX_RSS_CN_JIN10_URL": "https://feeds.invalid/cn/jin10",
            "EMOTION_INDEX_RSS_CN_CLS_URL": "https://feeds.invalid/cn/cls",
        }))
        error = cn["xueqiu"]["responses"][0]
        self.assertEqual(error["reason"], "XUEQIU_COOKIE_EXPIRED_OR_UNAUTHORIZED")
        self.assertEqual(error["http_status"], 500)
        self.assertNotIn("sha256", error)
        self.assertEqual(cn["cls"]["status"], "CAPTURED")
        self.assertEqual(cn["chinese_news"]["responses"][1]["reason"], "INVALID_FEED")
        self.assertFalse(self.snapshot_path(Market.CN, "xueqiu").exists())

        # Short cookie flags (e.g. xq_is_login=1) must not trigger false-positive credential reflection.
        self.network.side_effect = public_api_stub
        recovered_cn = self.platforms(self.capture(Market.CN, env={
            "EMOTION_INDEX_RSS_CN_XUEQIU_URL": "https://feeds.invalid/cn/xueqiu",
            "EMOTION_INDEX_RSS_CN_XUEQIU_COOKIE": "xq_is_login=1; session=synthetic-cookie-secret",
        }))
        self.assertEqual(recovered_cn["xueqiu"]["status"], "PARTIAL")
        self.assertIsNone(recovered_cn["xueqiu"]["responses"][0]["reason"])
        self.assertTrue(self.snapshot_path(Market.CN, "xueqiu").exists())

    def test_xhs_import_market_isolation_and_point_in_time_gate(self):
        published = NOW - timedelta(hours=1)
        good = {"post_id": "local-1", "text": "收盘情绪观察", "published_at": published.isoformat(),
                "available_at": (published + timedelta(minutes=1)).isoformat(), "tickers": ["000300.SH"]}
        body = self.inbox("cn.json", "CN", [good])
        self.inbox("future.json", "CN", [{**good, "published_at": (NOW + timedelta(minutes=1)).isoformat()}])
        self.inbox("forged.json", "CN", [{**good, "available_at": (published - timedelta(minutes=1)).isoformat()}])
        self.inbox("us.json", "US", [{**good, "post_id": "us-1", "tickers": ["SPY"]}])
        (self.runtime / "inbox" / "xhs" / "malformed.json").write_bytes(b"{")
        cn = self.platforms(self.capture(Market.CN))["xhs"]
        self.assertEqual(cn["status"], "PARTIAL")
        self.assertEqual({item["source"]: item["reason"] for item in cn["responses"]}, {
            "cn.json": None, "future.json": "XHS_INVALID_POST_OR_TIMESTAMP",
            "forged.json": "XHS_INVALID_POST_OR_TIMESTAMP", "malformed.json": "XHS_INVALID_ENVELOPE",
        })
        path = self.snapshot_path(Market.CN, "xhs")
        self.assertEqual(load_available_responses(path, NOW - timedelta(seconds=1)), ())
        stored, = load_available_responses(path, NOW)
        self.assertEqual(stored.body, body)
        self.assertEqual(cn["responses"][0]["sha256"], hashlib.sha256(body).hexdigest())
        market_post, = parse_posts(json.loads(stored.body), Market.CN, stored.fetched_at)
        self.assertEqual(market_post.market, Market.CN)
        self.assertEqual(market_post.post.available_at, NOW)
        window = ObservationWindow("test", "CN", DAY, "post_close", published - timedelta(hours=1),
                                   NOW - timedelta(seconds=1), ("xhs",))
        audit = filter_point_in_time_posts(window, (market_post.post,))
        self.assertEqual(audit.eligible_posts, ())
        self.assertEqual(audit.late_unavailable_count, 1)
        us = self.platforms(self.capture(Market.US))["xhs"]
        self.assertEqual([row["source"] for row in us["responses"] if row["reason"] is None], ["us.json"])

        # An invalid XHS-only inbox must not lock the day snapshot; fixing it allows capture on retry.
        (self.runtime / "inbox" / "xhs" / "bad_only.json").unlink(missing_ok=True)
        for item in (self.runtime / "inbox" / "xhs").iterdir():
            item.unlink()
        self.snapshot_path(Market.US, "xhs").unlink()
        self.inbox("broken.json", "US", [{**good, "published_at": (NOW + timedelta(minutes=1)).isoformat()}])
        failed_xhs = self.platforms(self.capture(Market.US))["xhs"]
        self.assertEqual(failed_xhs["status"], "UNAVAILABLE")
        self.assertFalse(self.snapshot_path(Market.US, "xhs").exists())
        self.inbox("broken.json", "US", [{**good, "post_id": "us-fixed", "tickers": ["SPY"]}])
        fixed_xhs = self.platforms(self.capture(Market.US))["xhs"]
        self.assertEqual(fixed_xhs["status"], "CAPTURED")
        self.assertTrue(self.snapshot_path(Market.US, "xhs").exists())

    def test_repeat_preserves_original_and_corruption_is_visible(self):
        self.capture()
        target = self.snapshot_path(Market.US, "apewisdom")
        original = target.read_bytes()
        self.network.reset_mock()
        platforms = self.platforms(self.capture(clock=lambda: NOW + timedelta(minutes=1)))
        self.network.assert_not_called()
        self.assertEqual(platforms["apewisdom"]["status"], "REUSED")
        self.assertEqual(target.read_bytes(), original)

        document = json.loads(original)
        document["responses"][0]["payload_base64"] = base64.b64encode(b"tampered").decode()
        target.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            load_available_responses(target, NOW)
        platforms = self.platforms(self.capture())
        self.assertEqual(platforms["apewisdom"]["reason"], "SNAPSHOT_UNREADABLE_OR_INVALID")
        self.assertEqual(platforms["stocktwits"]["status"], "REUSED")

    def test_write_failure_does_not_prevent_other_platforms(self):
        def publisher(path, content):
            if path.parent.name == "apewisdom":
                raise OSError("synthetic write failure")
            return atomic_publish_bytes(path, content)

        with patch("emotion_index.integrations.capture.atomic_publish_bytes", side_effect=publisher):
            platforms = self.platforms(self.capture())
        self.assertEqual(platforms["apewisdom"]["reason"], "SNAPSHOT_WRITE_FAILED")
        self.assertEqual(platforms["stocktwits"]["status"], "CAPTURED")

    def test_atomic_publish_never_overwrites_concurrent_writer(self):
        target = self.runtime / "rawwrite" / f"{DAY}.json"

        def attempt(payload):
            try:
                atomic_publish_bytes(target, payload)
            except FileExistsError:
                return None
            return payload

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, (b"first", b"second")))
        winner, = [item for item in results if item is not None]
        self.assertEqual(target.read_bytes(), winner)
        self.assertEqual(list(target.parent.iterdir()), [target])

    def test_cli_uses_market_and_prints_receipt_path(self):
        target = self.runtime / "receipt.json"
        with patch("emotion_index.app.cli.capture_market", return_value=target) as capture, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["capture", "--market", "CN"]), 0)
        capture.assert_called_once_with(Market.CN)
        self.assertEqual(output.getvalue().strip(), str(target))
