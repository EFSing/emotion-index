"""Source-weighted text sentiment from eligible, independently scored posts."""

from __future__ import annotations

from math import fsum
from statistics import fmean

from emotion_index.domain.market import (
    EmotionState,
    MarketPost,
    MarketSourceAudit,
    MarketTextSnapshot,
    MarketWeights,
    PostSentiment,
    Sector,
    TextEmotion,
    TextFlag,
)
from emotion_index.sentiment.scorer import post_engagement_weight


def _summarize_text(
    sector: Sector,
    posts: tuple[MarketPost, ...],
    scores: dict[MarketPost, PostSentiment],
    weights: MarketWeights,
) -> TextEmotion:
    by_platform: dict[str, list[PostSentiment]] = {}
    states: list[EmotionState] = []
    for item in posts:
        score = scores.get(item)
        if score is not None:
            by_platform.setdefault(item.post.platform.lower(), []).append(score)
            states.append(score.emotion_state)

    automatic = tuple(
        (platform, weight) for platform, weight in weights.automatic if platform in by_platform
    )
    xhs_scored = "xhs" in by_platform
    automatic_total = fsum(weight for _, weight in automatic)
    automatic_share = 1.0 - weights.xhs_weight if xhs_scored else 1.0
    effective = tuple(
        (platform, weight / automatic_total * automatic_share) for platform, weight in automatic
    )
    if xhs_scored:
        effective += (("xhs", weights.xhs_weight if automatic else 1.0),)

    flags = ()
    if not any(item.post.platform.lower() == "xhs" for item in posts):
        flags = (TextFlag.XHS_NOT_PROVIDED,)
    elif not xhs_scored:
        flags = (TextFlag.XHS_NOT_SCORED,)

    index = None
    if effective:
        platform_polarities = [
            fmean(
                (score.polarity for score in by_platform[platform]),
                weights=(post_engagement_weight(score.market_post.post) for score in by_platform[platform]),
            )
            for platform, _ in effective
        ]
        polarity = fmean(platform_polarities, weights=(weight for _, weight in effective))
        index = (max(-1.0, min(1.0, polarity)) + 1.0) * 50.0

    count = len(states)
    return TextEmotion(
        sector=sector,
        post_count=len(posts),
        scored_post_count=count,
        text_emotion_index=index,
        euphoria_ratio=states.count(EmotionState.EUPHORIA_FOMO) / count if count else None,
        panic_ratio=states.count(EmotionState.PANIC_CAPITULATION) / count if count else None,
        effective_weights=effective,
        missing_automatic_platforms=tuple(
            platform for platform, _ in weights.automatic if platform not in by_platform
        ),
        flags=flags,
    )


def _effective_sector(item: MarketPost, eligible_scores: dict[MarketPost, PostSentiment]) -> Sector:
    score = eligible_scores.get(item)
    if score is not None and score.sector is not None:
        return score.sector
    return item.sector


def aggregate_market_text(
    audit: MarketSourceAudit, scores: tuple[PostSentiment, ...], weights: MarketWeights,
) -> MarketTextSnapshot:
    """Aggregate all eligible posts for the market and each exclusive style bucket."""
    if audit.market != weights.market:
        raise ValueError("weights must belong to the observation market")
    platforms = weights.platforms
    if any(item.post.platform.lower() not in platforms for item in audit.eligible_posts):
        raise ValueError("eligible posts must use configured platforms")

    eligible = set(audit.eligible_posts)
    eligible_scores: dict[MarketPost, PostSentiment] = {}
    for score in scores:
        item = score.market_post
        if item in eligible:
            if item in eligible_scores:
                raise ValueError(f"Duplicate score for {item.post.platform}:{item.post.post_id}")
            eligible_scores[item] = score

    buckets = (_summarize_text(Sector.BROAD_MARKET, audit.eligible_posts, eligible_scores, weights),)
    buckets += tuple(
        _summarize_text(
            sector,
            tuple(item for item in audit.eligible_posts if _effective_sector(item, eligible_scores) == sector),
            eligible_scores,
            weights,
        )
        for sector in Sector if sector != Sector.BROAD_MARKET
    )
    return MarketTextSnapshot(audit, buckets)
