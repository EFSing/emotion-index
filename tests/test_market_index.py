import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from math import log1p
from pathlib import Path

from emotion_index.domain.market import (
    DivergenceFlag,
    EmotionState,
    Market,
    MarketAnchor,
    MarketSectors,
    MarketWeights,
    PostSentiment,
    Sector,
    TextFlag,
    VolumeFlag,
    VolumeObservation,
)
from emotion_index.domain.models import ObservationWindow, SocialPost
from emotion_index.research.three_track import build_market_snapshot, classify_divergences
from emotion_index.sentiment.market import aggregate_market_text
from emotion_index.sources.filter import filter_point_in_time_posts, route_market_post, select_market_posts

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
CUTOFF = datetime(2026, 11, 27, 8, tzinfo=timezone.utc)
TRADING_DATE = CUTOFF.date()


class MarketIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        weights = json.loads((CONFIGS / "weights.json").read_text(encoding="utf-8"))
        sectors = json.loads((CONFIGS / "sectors.json").read_text(encoding="utf-8"))
        cls.weights = {market: MarketWeights.from_mapping(market, **weights[market.value]) for market in Market}
        cls.sectors = {market: MarketSectors.from_mapping(market, sectors[market.value]) for market in Market}

    def window(self, market, platforms=None):
        return ObservationWindow(
            f"synthetic-{market.value}", market.value, TRADING_DATE.isoformat(), "close",
            CUTOFF - timedelta(days=1), CUTOFF,
            tuple(sorted(self.weights[market].platforms)) if platforms is None else platforms,
        )

    def post(self, market, platform, post_id, tickers=(), available_at=CUTOFF, likes=0):
        post = SocialPost(
            platform, post_id, "synthetic-author", CUTOFF - timedelta(hours=1), available_at,
            tickers, "synthetic post", likes, 0, 0,
        )
        return route_market_post(post, self.sectors[market])

    def text_snapshot(self, market, posts=(), scores=(), window=None):
        window = self.window(market) if window is None else window
        audit = select_market_posts(window, posts, self.weights[market])
        return aggregate_market_text(audit, scores, self.weights[market])

    def history(self, counts, market=Market.US, sector=Sector.BROAD_MARKET):
        trading_day = TRADING_DATE
        for count in counts:
            trading_day -= timedelta(days=1)
            while trading_day.weekday() > 4:
                trading_day -= timedelta(days=1)
            visible = datetime.combine(trading_day, CUTOFF.timetz())
            yield VolumeObservation(market, trading_day, visible, count, sector)

    def test_market_isolation_xhs_share_and_style_breakdown(self):
        us_post = self.post(Market.US, "Xueqiu", "shared", ("spy", "xlk"))
        cn_post = self.post(Market.CN, "xueqiu", "shared", ("000688.SH",))
        us_xhs = self.post(Market.US, "xhs", "calibration", ("XLK", "XLE"))
        cn_xhs = self.post(Market.CN, "xhs", "calibration", ("000922.CSI",))
        posts = (us_post, cn_post, us_xhs, cn_xhs)
        scores = (
            PostSentiment(us_post, 1, EmotionState.EUPHORIA_FOMO),
            PostSentiment(cn_post, 1, EmotionState.OPTIMISTIC),
            PostSentiment(us_xhs, -1, EmotionState.PANIC_CAPITULATION),
            PostSentiment(cn_xhs, -1, EmotionState.PANIC_CAPITULATION),
        )

        for market, index, xhs_share in ((Market.US, 70, 0.30), (Market.CN, 60, 0.40)):
            with self.subTest(market=market):
                snapshot = build_market_snapshot(self.text_snapshot(market, posts, scores))
                broad = snapshot.broad_market.text
                self.assertEqual(broad.post_count, 2)
                self.assertAlmostEqual(broad.text_emotion_index, index)
                self.assertEqual(dict(broad.effective_weights), {"xueqiu": 1 - xhs_share, "xhs": xhs_share})
                self.assertEqual(broad.panic_ratio, 0.5)
                self.assertEqual(broad.euphoria_ratio, 0.5 if market == Market.US else 0.0)
                self.assertEqual(broad.flags, ())
                sectors = {reading.text.sector: reading.text for reading in snapshot.sectors}
                self.assertEqual(set(sectors), set(Sector) - {Sector.BROAD_MARKET})
                self.assertEqual(sectors[Sector.TECH_GROWTH].text_emotion_index, 100)
                self.assertIsNone(sectors[Sector.CYCLICAL_FINANCE].text_emotion_index)
                self.assertEqual(sectors[Sector.DEFENSIVE_DIVIDEND].post_count, int(market == Market.CN))

        self.assertEqual(us_xhs.sector, Sector.BROAD_MARKET)
        self.assertEqual(self.post(Market.US, "xueqiu", "spy", ("QQQ",)).sector, Sector.BROAD_MARKET)
        self.assertEqual(
            self.post(Market.US, "xueqiu", "unknown", ("000688.SH",)).sector, Sector.BROAD_MARKET,
        )

    def test_missing_sources_normalize_and_do_not_invent_text_scores(self):
        primary = self.post(Market.CN, "xueqiu", "a")
        auxiliary = self.post(Market.CN, "jisilu", "b")
        xhs = self.post(Market.CN, "xhs", "c")
        scores = (
            PostSentiment(primary, 1, EmotionState.OPTIMISTIC),
            PostSentiment(auxiliary, -1, EmotionState.ANXIOUS),
        )
        text = self.text_snapshot(Market.CN, (primary, auxiliary), scores).buckets[0]
        self.assertAlmostEqual(dict(text.effective_weights)["xueqiu"], 0.35 / 0.45)
        self.assertAlmostEqual(text.text_emotion_index, 100 * 0.35 / 0.45)
        self.assertEqual(set(text.missing_automatic_platforms), {"cls", "chinese_news"})
        self.assertEqual(text.flags, (TextFlag.XHS_NOT_PROVIDED,))

        unscored = self.text_snapshot(Market.CN, (primary, auxiliary, xhs), scores).buckets[0]
        self.assertEqual(unscored.post_count, 3)
        self.assertEqual(unscored.scored_post_count, 2)
        self.assertEqual(unscored.effective_weights, text.effective_weights)
        self.assertEqual(unscored.flags, (TextFlag.XHS_NOT_SCORED,))

        xhs_score = PostSentiment(xhs, -1, EmotionState.PANIC_CAPITULATION)
        calibrated = self.text_snapshot(Market.CN, (primary, auxiliary, xhs), (*scores, xhs_score)).buckets[0]
        self.assertAlmostEqual(dict(calibrated.effective_weights)["xueqiu"], 0.6 * 0.35 / 0.45)
        self.assertAlmostEqual(dict(calibrated.effective_weights)["jisilu"], 0.6 * 0.10 / 0.45)
        self.assertEqual(dict(calibrated.effective_weights)["xhs"], 0.4)

        xhs_only = self.text_snapshot(Market.CN, (xhs,), (xhs_score,)).buckets[0]
        self.assertEqual(xhs_only.effective_weights, (("xhs", 1.0),))
        self.assertEqual(xhs_only.text_emotion_index, 0)

        empty = build_market_snapshot(self.text_snapshot(Market.CN, (primary,))).broad_market
        self.assertIsNone(empty.text.text_emotion_index)
        self.assertIsNone(empty.text.euphoria_ratio)
        self.assertIsNone(empty.text.panic_ratio)
        self.assertEqual(empty.text.effective_weights, ())
        self.assertEqual(empty.volume.flag, VolumeFlag.WARMUP)
        self.assertIsNone(empty.market_anchor)
        self.assertIsNone(empty.composite_score)
        self.assertEqual(empty.divergence_flags, ())

    def test_cutoff_and_platform_filter_keep_late_posts_and_edits_out(self):
        visible = self.post(Market.US, "bluesky", "a", ("XLK",))
        late = self.post(Market.US, "xhs", "late", available_at=CUTOFF + timedelta(seconds=1))
        outside = replace(visible, post=replace(
            visible.post, post_id="old", published_at=CUTOFF - timedelta(days=2),
        ))
        unconfigured = self.post(Market.US, "reddit", "unconfigured")
        disallowed = self.post(Market.US, "stocktwits", "disabled")
        edit = replace(visible, post=replace(
            visible.post, text="post-hoc edit", available_at=CUTOFF + timedelta(seconds=1), likes=900,
        ))
        posts = (visible, late, outside, unconfigured, disallowed)
        scores = tuple(PostSentiment(post, -1, EmotionState.PANIC_CAPITULATION) for post in (*posts[1:], edit))
        scores += (PostSentiment(visible, 0.4, EmotionState.OPTIMISTIC),)
        window = self.window(Market.US, ("bluesky", "xhs", "reddit"))

        snapshot = self.text_snapshot(Market.US, posts, scores, window)
        self.assertEqual(snapshot.audit.eligible_posts, (visible,))
        self.assertEqual(snapshot.audit.late_unavailable_count, 1)
        self.assertEqual(snapshot.audit.out_of_window_count, 1)
        self.assertEqual(snapshot.audit.unsupported_platform_count, 2)
        self.assertEqual(snapshot.buckets[0].text_emotion_index, 70.0)
        self.assertEqual(snapshot.buckets[0].flags, (TextFlag.XHS_NOT_PROVIDED,))

        mixed_case = filter_point_in_time_posts(
            window, (visible.post,), configured_platforms=frozenset({"Bluesky"}),
        )
        self.assertEqual(mixed_case.eligible_posts, (visible.post,))

    def test_engagement_weights_posts_within_their_platform_share(self):
        popular = self.post(Market.US, "bluesky", "popular", likes=100)
        quiet = self.post(Market.US, "bluesky", "quiet")
        other = self.post(Market.US, "stocktwits", "other")
        scores = (
            PostSentiment(popular, 1, EmotionState.EUPHORIA_FOMO),
            PostSentiment(quiet, -1, EmotionState.PANIC_CAPITULATION),
            PostSentiment(other, -1, EmotionState.ANXIOUS),
        )
        broad = self.text_snapshot(Market.US, (popular, quiet, other), scores).buckets[0]
        bluesky_polarity = log1p(100) / (2 + log1p(100))
        self.assertEqual(dict(broad.effective_weights), {"bluesky": 0.5, "stocktwits": 0.5})
        self.assertAlmostEqual(broad.text_emotion_index, (1 + (bluesky_polarity - 1) / 2) * 50)
        self.assertEqual(broad.euphoria_ratio, 1 / 3)
        self.assertEqual(broad.panic_ratio, 1 / 3)

    def test_volume_uses_latest_twenty_same_market_days_and_visible_anchors(self):
        posts = tuple(self.post(Market.US, "bluesky", str(i), ("XLK",)) for i in range(4))
        scores = (PostSentiment(posts[0], 0.7, EmotionState.EUPHORIA_FOMO),)
        text = self.text_snapshot(Market.US, posts, scores)
        history = tuple(self.history([0, 2] * 10 + [900]))
        sector_history = tuple(self.history([0, 2] * 3, sector=Sector.TECH_GROWTH))
        history += sector_history + (
            VolumeObservation(Market.US, TRADING_DATE, CUTOFF, 900),
            VolumeObservation(Market.US, TRADING_DATE + timedelta(days=1), CUTOFF, 900),
            replace(history[0], available_at=CUTOFF + timedelta(seconds=1), post_count=900),
            replace(history[0], market=Market.CN, post_count=900),
        )
        anchor = MarketAnchor(Market.US, TRADING_DATE, CUTOFF, 45)
        sector_anchor = replace(anchor, sector=Sector.TECH_GROWTH, anchor_score=50)
        anchors = (
            anchor, sector_anchor,
            replace(anchor, market=Market.CN, anchor_score=100),
            replace(anchor, trading_date=date(2026, 11, 26), anchor_score=100),
            replace(anchor, available_at=CUTOFF + timedelta(seconds=1), anchor_score=100),
        )
        snapshot = build_market_snapshot(text, tuple(reversed(history)), anchors)
        broad = snapshot.broad_market
        self.assertEqual(broad.volume.baseline_days, 20)
        self.assertEqual(broad.volume.volume_zscore_20d, 3.0)
        self.assertEqual(broad.volume.flag, VolumeFlag.VOLUME_SPIKE)
        self.assertEqual(broad.market_anchor, anchor)
        self.assertEqual(broad.composite_score, 65.0)
        self.assertEqual(broad.divergence_flags, (
            DivergenceFlag.SOCIAL_OVERHEATED_VS_MARKET_WEAK,
            DivergenceFlag.EXTREME_EUPHORIA_WARNING,
        ))
        tech = snapshot.sectors[0]
        self.assertEqual(tech.volume.baseline_days, 6)
        self.assertEqual(tech.volume.volume_zscore_20d, 3.0)
        self.assertEqual(tech.market_anchor, sector_anchor)
        self.assertEqual(tech.composite_score, 67.5)
        self.assertEqual(tech.divergence_flags, (DivergenceFlag.EXTREME_EUPHORIA_WARNING,))

    def test_volume_warmup_zero_variance_and_cold_boundary(self):
        text = self.text_snapshot(Market.US)
        warmup = build_market_snapshot(text, tuple(self.history([0, 2, 0, 2]))).broad_market.volume
        self.assertEqual(warmup.baseline_days, 4)
        self.assertIsNone(warmup.volume_zscore_20d)
        self.assertEqual(warmup.flag, VolumeFlag.WARMUP)

        flat = build_market_snapshot(text, tuple(self.history([2] * 5))).broad_market.volume
        self.assertEqual(flat.baseline_days, 5)
        self.assertIsNone(flat.volume_zscore_20d)
        self.assertEqual(flat.flag, VolumeFlag.ZERO_VARIANCE)

        cold = build_market_snapshot(text, tuple(self.history([0, 2] * 3))).broad_market.volume
        self.assertEqual(cold.volume_zscore_20d, -1.0)
        self.assertEqual(cold.flag, VolumeFlag.VOLUME_COLD)

        posts = tuple(self.post(Market.US, "bluesky", str(i)) for i in range(5))
        text = self.text_snapshot(Market.US, posts)
        spike = build_market_snapshot(text, tuple(self.history([0, 4] * 3))).broad_market.volume
        self.assertEqual(spike.volume_zscore_20d, 1.5)
        self.assertEqual(spike.flag, VolumeFlag.VOLUME_SPIKE)

    def test_divergence_thresholds_and_missing_optional_tracks(self):
        cases = (
            ((70, 1.0, 45), (DivergenceFlag.SOCIAL_OVERHEATED_VS_MARKET_WEAK,)),
            ((69.99, 1.0, 45), ()),
            ((70, 0.99, 45), ()),
            ((70, 1.0, 45.01), ()),
            ((70, None, 45), ()),
            ((30, None, 55), (DivergenceFlag.SOCIAL_PANIC_VS_MARKET_RESILIENT,)),
            ((30.01, 0, 55), ()),
            ((30, 0, 54.99), ()),
            ((80, None, None), (DivergenceFlag.EXTREME_EUPHORIA_WARNING,)),
            ((20, None, None), (DivergenceFlag.EXTREME_CAPITULATION_ALERT,)),
            ((20.01, None, None), ()),
            ((79.99, None, None), ()),
            ((None, 10, 0), ()),
        )
        for inputs, expected in cases:
            with self.subTest(inputs=inputs):
                self.assertEqual(classify_divergences(*inputs), expected)

    def test_rejects_duplicate_market_inputs_and_invalid_contract_values(self):
        post = self.post(Market.US, "bluesky", "duplicate")
        score = PostSentiment(post, 0, EmotionState.NEUTRAL)
        with self.assertRaisesRegex(ValueError, "Duplicate post"):
            self.text_snapshot(Market.US, (post, post))
        with self.assertRaisesRegex(ValueError, "Duplicate score"):
            self.text_snapshot(Market.US, (post,), (score, score))
        with self.assertRaisesRegex(ValueError, "weights must belong"):
            select_market_posts(self.window(Market.US), (post,), self.weights[Market.CN])
        with self.assertRaises(ValueError):
            select_market_posts(replace(self.window(Market.US), market="HK"), (), self.weights[Market.US])
        with self.assertRaises(ValueError):
            PostSentiment(post, float("nan"), EmotionState.NEUTRAL)
        with self.assertRaises(ValueError):
            MarketAnchor(Market.US, TRADING_DATE, CUTOFF, 101)
        with self.assertRaises(ValueError):
            VolumeObservation(Market.US, TRADING_DATE, CUTOFF.replace(tzinfo=None), 1)
        with self.assertRaises(ValueError):
            MarketWeights.from_mapping(Market.US, {"bluesky": 0.7}, 0.3)

        text = self.text_snapshot(Market.US)
        day = next(self.history([1]))
        with self.assertRaisesRegex(ValueError, "Duplicate volume observation"):
            build_market_snapshot(text, (day, day))
        anchor = MarketAnchor(Market.US, TRADING_DATE, CUTOFF, 55)
        with self.assertRaisesRegex(ValueError, "Duplicate market anchor"):
            build_market_snapshot(text, anchors=(anchor, anchor))
