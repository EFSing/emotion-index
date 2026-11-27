"""Point-in-time filtering and source deduplication for social media posts."""

from __future__ import annotations

from emotion_index.domain.market import (
    Market,
    MarketPost,
    MarketSectors,
    MarketSourceAudit,
    MarketWeights,
    Sector,
)
from emotion_index.domain.models import ObservationWindow, SocialPost, SourceAuditSummary


def filter_point_in_time_posts(
    window: ObservationWindow,
    posts: tuple[SocialPost, ...],
    *,
    configured_platforms: frozenset[str] | None = None,
) -> SourceAuditSummary:
    allowed = {p.lower() for p in window.allowed_platforms}
    if configured_platforms is not None:
        allowed.intersection_update(p.lower() for p in configured_platforms)
    seen_keys: set[tuple[str, str]] = set()
    eligible: list[SocialPost] = []
    late_count = 0
    out_of_window_count = 0
    unsupported_count = 0

    for post in posts:
        platform_key = post.platform.lower()
        dedup_key = (platform_key, post.post_id)
        if dedup_key in seen_keys:
            raise ValueError(f"Duplicate post identity detected: {post.platform}:{post.post_id}")
        seen_keys.add(dedup_key)

        if platform_key not in allowed:
            unsupported_count += 1
            continue

        if post.published_at < window.window_start or post.published_at > window.cutoff_at:
            out_of_window_count += 1
            continue

        if post.available_at > window.cutoff_at:
            late_count += 1
            continue

        eligible.append(post)

    eligible.sort(key=lambda item: (item.published_at, item.platform, item.post_id))
    return SourceAuditSummary(
        total_input_posts=len(posts),
        eligible_posts=tuple(eligible),
        late_unavailable_count=late_count,
        out_of_window_count=out_of_window_count,
        unsupported_platform_count=unsupported_count,
    )


def route_market_post(post: SocialPost, sectors: MarketSectors) -> MarketPost:
    """Assign one style when the symbols match a single non-broad sector."""
    symbols = {ticker.strip().upper() for ticker in post.tickers}
    matches = {
        sector for symbol, sector in sectors.by_symbol
        if symbol in symbols and sector != Sector.BROAD_MARKET
    }
    sector = matches.pop() if len(matches) == 1 else Sector.BROAD_MARKET
    return MarketPost(post, sectors.market, sector)


def select_market_posts(
    window: ObservationWindow, posts: tuple[MarketPost, ...], weights: MarketWeights,
) -> MarketSourceAudit:
    market = Market(window.market)
    if weights.market != market:
        raise ValueError("weights must belong to the observation market")
    market_posts = tuple(item for item in posts if item.market == market)
    audit = filter_point_in_time_posts(
        window, tuple(item.post for item in market_posts), configured_platforms=weights.platforms,
    )
    by_identity = {(item.post.platform.lower(), item.post.post_id): item for item in market_posts}
    return MarketSourceAudit(
        window=window,
        eligible_posts=tuple(
            by_identity[(post.platform.lower(), post.post_id)] for post in audit.eligible_posts
        ),
        late_unavailable_count=audit.late_unavailable_count,
        out_of_window_count=audit.out_of_window_count,
        unsupported_platform_count=audit.unsupported_platform_count,
    )
