"""Ticker and platform breakdown aggregations for an EmotionIndexSnapshot."""

from __future__ import annotations

from emotion_index.domain.models import (
    EmotionIndexSnapshot,
    PlatformEmotionSummary,
    ResearchReportProjection,
    ScoredPost,
    TickerEmotionSummary,
)


def _weighted_net(items: list[ScoredPost]) -> tuple[float, float]:
    total_weight = sum(i.engagement_weight for i in items)
    net = sum(i.weighted_score for i in items) / total_weight if total_weight > 0 else 0.0
    clamped = max(-1.0, min(1.0, net))
    return round(clamped, 4), round((clamped + 1.0) * 50.0, 2)


def build_research_projection(snapshot: EmotionIndexSnapshot) -> ResearchReportProjection:
    by_ticker: dict[str, list[ScoredPost]] = {}
    by_platform: dict[str, list[ScoredPost]] = {}

    for item in snapshot.scored_posts:
        by_platform.setdefault(item.post.platform.lower(), []).append(item)
        for ticker in item.post.tickers:
            by_ticker.setdefault(ticker.upper(), []).append(item)

    ticker_summaries: list[TickerEmotionSummary] = []
    for ticker, group in sorted(by_ticker.items()):
        net, idx = _weighted_net(group)
        ticker_summaries.append(
            TickerEmotionSummary(
                ticker=ticker,
                mention_count=len(group),
                net_sentiment=net,
                emotion_index=idx,
            )
        )

    platform_summaries: list[PlatformEmotionSummary] = []
    for platform, group in sorted(by_platform.items()):
        net, idx = _weighted_net(group)
        platform_summaries.append(
            PlatformEmotionSummary(
                platform=platform,
                post_count=len(group),
                net_sentiment=net,
                emotion_index=idx,
            )
        )

    return ResearchReportProjection(
        snapshot=snapshot,
        ticker_summaries=tuple(ticker_summaries),
        platform_summaries=tuple(platform_summaries),
    )
