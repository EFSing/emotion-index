"""Point-in-time filtering and source deduplication for social media posts."""

from __future__ import annotations

from emotion_index.domain.models import ObservationWindow, SocialPost, SourceAuditSummary


def filter_point_in_time_posts(
    window: ObservationWindow,
    posts: tuple[SocialPost, ...],
) -> SourceAuditSummary:
    allowed = {p.lower() for p in window.allowed_platforms}
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
