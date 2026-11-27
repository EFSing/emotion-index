"""Lexicon and engagement-weighted baseline sentiment scorer."""

from __future__ import annotations

import math
import re

from emotion_index.domain.models import (
    EmotionIndexSnapshot,
    EmotionRegime,
    ObservationWindow,
    ScoredPost,
    SocialPost,
    SourceAuditSummary,
)

MODEL_VERSION = "lexicon_engagement_v1"

BULLISH_TERMS = frozenset({
    "breakout", "bull", "bullish", "buy", "buying", "calls", "dip",
    "long", "momentum", "optimistic", "rally", "resilient", "strong", "support", "surge",
})

BEARISH_TERMS = frozenset({
    "bear", "bearish", "breakdown", "cautious", "crash", "dump", "fear",
    "liquidating", "panic", "puts", "recession", "risk", "sell", "selling", "short", "weak",
})

TOKEN_PATTERN = re.compile(r"[a-zA-Z]+")


def classify_regime(emotion_index: float) -> EmotionRegime:
    if emotion_index < 20.0:
        return EmotionRegime.EXTREME_FEAR
    if emotion_index < 40.0:
        return EmotionRegime.FEAR
    if emotion_index <= 60.0:
        return EmotionRegime.NEUTRAL
    if emotion_index <= 80.0:
        return EmotionRegime.GREED
    return EmotionRegime.EXTREME_GREED


def score_single_post(post: SocialPost) -> ScoredPost:
    tokens = [tok.lower() for tok in TOKEN_PATTERN.findall(post.text)]
    bullish_hits = tuple(sorted({tok for tok in tokens if tok in BULLISH_TERMS}))
    bearish_hits = tuple(sorted({tok for tok in tokens if tok in BEARISH_TERMS}))
    pos_count = sum(1 for tok in tokens if tok in BULLISH_TERMS)
    neg_count = sum(1 for tok in tokens if tok in BEARISH_TERMS)
    total_hits = pos_count + neg_count
    if total_hits == 0:
        polarity = 0.0
    else:
        polarity = (pos_count - neg_count) / total_hits

    raw_engagement = post.likes + 2 * post.replies + 3 * post.reposts
    engagement_weight = 1.0 + math.log1p(raw_engagement)
    weighted_score = polarity * engagement_weight
    return ScoredPost(
        post=post,
        polarity=round(polarity, 6),
        engagement_weight=round(engagement_weight, 6),
        weighted_score=round(weighted_score, 6),
        matched_bullish=bullish_hits,
        matched_bearish=bearish_hits,
    )


def compute_emotion_index(
    window: ObservationWindow,
    audit: SourceAuditSummary,
) -> EmotionIndexSnapshot:
    scored = tuple(score_single_post(post) for post in audit.eligible_posts)
    if not scored:
        net_sentiment = 0.0
    else:
        total_weight = sum(item.engagement_weight for item in scored)
        net_sentiment = (
            sum(item.weighted_score for item in scored) / total_weight
            if total_weight > 0.0
            else 0.0
        )
    clamped_net = max(-1.0, min(1.0, net_sentiment))
    emotion_index = round((clamped_net + 1.0) * 50.0, 2)
    return EmotionIndexSnapshot(
        window=window,
        model_version=MODEL_VERSION,
        eligible_post_count=len(scored),
        late_unavailable_count=audit.late_unavailable_count,
        out_of_window_count=audit.out_of_window_count,
        net_sentiment=round(clamped_net, 4),
        emotion_index=emotion_index,
        regime=classify_regime(emotion_index),
        scored_posts=scored,
    )
