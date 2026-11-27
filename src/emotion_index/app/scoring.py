"""Orchestrate point-in-time scoring from PR-2 snapshots."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterator

from emotion_index.domain.market import Market, MarketPost, MarketWeights
from emotion_index.domain.models import ObservationWindow, SocialPost
from emotion_index.integrations.capture import utc_now
from emotion_index.integrations.deepseek_client import (
    MAX_BATCH_SIZE, MIN_BATCH_SIZE, MODEL_NAME, PROMPT_VERSION, DeepSeekClient,
    DeepSeekScore, DeepSeekUnavailable,
)
from emotion_index.integrations.frozen_posts import latest_capture_date, load_market_config, load_market_posts
from emotion_index.integrations.score_cache import ScoreCache, content_hash, normalize_text
from emotion_index.sentiment.bilingual_lexicon import MODEL_NAME as LEXICON_MODEL
from emotion_index.sentiment.bilingual_lexicon import SCORING_MODE as LEXICON_MODE
from emotion_index.sentiment.bilingual_lexicon import score_post
from emotion_index.sentiment.scorer import post_engagement_weight
from emotion_index.sources.filter import route_market_post, select_market_posts

DEFAULT_DAILY_LIMIT = 100
DEEPSEEK_MODE = "DEEPSEEK"
QUOTA_UNAVAILABLE = "DEEPSEEK_DAILY_QUOTA_EXHAUSTED_OR_ALREADY_SUBMITTED"


def _rank_key(item: MarketPost) -> tuple[float, float, str, str]:
    return (
        -post_engagement_weight(item.post),
        -item.post.published_at.timestamp(),
        item.post.platform,
        item.post.post_id,
    )


def _active_platform_weights(platforms: set[str], weights: MarketWeights) -> dict[str, float]:
    automatic = [(name, weight) for name, weight in weights.automatic if name in platforms]
    auto_total = sum(weight for _, weight in automatic)
    has_xhs = "xhs" in platforms
    auto_share = 1.0 - weights.xhs_weight if has_xhs else 1.0
    effective = {name: weight / auto_total * auto_share for name, weight in automatic} if auto_total else {}
    if has_xhs:
        effective["xhs"] = weights.xhs_weight if automatic else 1.0
    return effective


def _select_top_posts(
    posts: tuple[MarketPost, ...], weights: MarketWeights, daily_limit: int,
) -> tuple[MarketPost, ...]:
    """Allocate daily_limit across active platforms by effective weight so zero-engagement RSS feeds are not starved."""
    if len(posts) <= daily_limit:
        return tuple(sorted(posts, key=_rank_key))

    groups: dict[str, list[MarketPost]] = {}
    for item in posts:
        groups.setdefault(item.post.platform.lower(), []).append(item)
    for items in groups.values():
        items.sort(key=_rank_key)

    effective = _active_platform_weights(set(groups), weights)
    quotas = {platform: 0 for platform in groups}
    active = set(groups)
    remaining = daily_limit
    while remaining > 0 and active:
        total_weight = sum(effective[platform] for platform in active)
        exact = {platform: remaining * (effective[platform] / total_weight) for platform in active}
        allocated = 0
        for platform in list(active):
            capacity = len(groups[platform]) - quotas[platform]
            take = min(capacity, int(exact[platform]))
            quotas[platform] += take
            allocated += take
            if quotas[platform] == len(groups[platform]):
                active.remove(platform)
        remaining -= allocated
        if remaining > 0 and allocated == 0:
            order = sorted(
                active,
                key=lambda platform: (
                    -(exact[platform] - int(exact[platform])),
                    -effective[platform],
                    _rank_key(groups[platform][quotas[platform]]),
                ),
            )
            for platform in order[:remaining]:
                quotas[platform] += 1
                remaining -= 1
                if quotas[platform] == len(groups[platform]):
                    active.remove(platform)

    chosen = [item for platform, count in quotas.items() for item in groups[platform][:count]]
    return tuple(sorted(chosen, key=_rank_key))


def _deduplicate(posts: tuple[SocialPost, ...]) -> tuple[SocialPost, ...]:
    by_identity = {}
    for post in sorted(posts, key=lambda post: post.available_at):
        by_identity.setdefault((post.platform, post.post_id), post)
    return tuple(by_identity.values())


def _batch_sizes(count: int) -> Iterator[int]:
    """Use 20–30 items when possible, with a short tail only when needed."""
    batch_count = (count + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE
    for remaining_batches in range(batch_count, 0, -1):
        size = max(MIN_BATCH_SIZE, (count + remaining_batches - 1) // remaining_batches)
        size = min(size, count)
        yield size
        count -= size


def _deepseek_scores(
    cache: ScoreCache, client: DeepSeekClient, market: Market, requests: dict[str, tuple[str, str]],
    daily_limit: int, allow_peak: bool,
) -> tuple[dict[str, DeepSeekScore], frozenset[str], int, str | None]:
    scores = {}
    for key in requests:
        score = cache.get(key)
        if score is not None:
            scores[key] = score
    cache_hits = set(scores)
    misses = tuple(key for key in requests if key not in scores)
    if not misses:
        return scores, frozenset(cache_hits), 0, None

    offset = 0
    submitted = 0
    unavailable = None
    try:
        client.request_day(allow_peak=allow_peak)
        client.require_api_key()
        for size in _batch_sizes(len(misses)):
            batch = misses[offset:offset + size]
            offset += size
            day = client.request_day(allow_peak=allow_peak)
            claimed = cache.reserve(market, day, batch, daily_limit)
            if claimed:
                submitted += len(claimed)
                result = client.score_batch(
                    tuple(requests[key] for key in claimed), allow_peak=allow_peak, reserved_day=day,
                )
                cache.store(dict(zip(claimed, result, strict=True)))
                scores.update(zip(claimed, result, strict=True))
    except DeepSeekUnavailable as error:
        unavailable = str(error)
    # Another worker may have cached a score while this run was processing its own batches.
    for key in misses:
        if key not in scores:
            score = cache.get(key)
            if score is not None:
                scores[key] = score
                cache_hits.add(key)
    if len(scores) < len(requests) and unavailable is None:
        unavailable = QUOTA_UNAVAILABLE
    return scores, frozenset(cache_hits), submitted, unavailable


def score_market(
    market: Market, runtime: Path = Path(".runtime"), *, capture_date: date | None = None,
    cutoff_at: datetime | None = None, window_start: datetime | None = None,
    daily_limit: int = DEFAULT_DAILY_LIMIT, allow_peak: bool = False,
    client: DeepSeekClient | None = None,
) -> dict:
    if type(daily_limit) is not int or daily_limit <= 0:
        raise ValueError("--daily-limit 必须为正整数。")
    weights, sectors = load_market_config(market)
    raw_market = runtime / "data" / "raw" / market.value
    day = capture_date or latest_capture_date(raw_market)
    now = utc_now()
    if day > now.date():
        raise ValueError("--capture-date 晚于当前 UTC 日期。")
    posts, latest, sources = load_market_posts(raw_market, market, day, weights.platforms)
    if cutoff_at is None:
        cutoff_at = min(now, latest or datetime.combine(day, time.max, timezone.utc))
    if cutoff_at.utcoffset() is None or cutoff_at > now:
        raise ValueError("--cutoff-at 必须带时区且不得晚于当前时间。")
    if window_start is None:
        window_start = cutoff_at - timedelta(days=1)
    window = ObservationWindow(
        f"{market.value}-{day.isoformat()}", market.value, day.isoformat(), "capture",
        window_start, cutoff_at, tuple(sorted(weights.platforms)),
    )
    unique_posts = _deduplicate(posts)
    audit = select_market_posts(window, tuple(route_market_post(post, sectors) for post in unique_posts), weights)
    chosen = _select_top_posts(audit.eligible_posts, weights, daily_limit)
    selected: list[tuple[MarketPost, str]] = []
    requests: dict[str, tuple[str, str]] = {}
    for item in chosen:
        platform, text = item.post.platform, normalize_text(item.post.text)
        key = content_hash(platform, text)
        selected.append((item, key))
        requests[key] = (platform, text)

    scores, cache_hits, submitted, unavailable = {}, frozenset(), 0, None
    if requests:
        with ScoreCache(runtime / "cache" / "llm_scores.sqlite") as cache:
            scores, cache_hits, submitted, unavailable = _deepseek_scores(
                cache, client or DeepSeekClient(), market, requests, daily_limit, allow_peak,
            )
    records = []
    for item, key in selected:
        score = scores.get(key)
        sentiment = score.for_post(item) if score is not None else score_post(item)
        records.append({
            "platform": item.post.platform, "post_id": item.post.post_id,
            "published_at": item.post.published_at.isoformat(), "available_at": item.post.available_at.isoformat(),
            "routed_sector": item.sector.value,
            "scoring_mode": DEEPSEEK_MODE if score is not None else LEXICON_MODE,
            "cache_hit": key in cache_hits, "polarity": sentiment.polarity,
            "emotion_state": sentiment.emotion_state.value,
            "sector": sentiment.sector.value if sentiment.sector is not None else None,
            "is_sarcasm": sentiment.is_sarcasm, "confidence": sentiment.confidence,
            "reason_short": sentiment.reason_short,
        })
    degraded = sum(record["scoring_mode"] == LEXICON_MODE for record in records)
    mode = LEXICON_MODE if degraded else DEEPSEEK_MODE if records else "NO_POSTS"
    return {
        "schema_version": 1, "market": market.value, "capture_date": day.isoformat(),
        "window_start": window.window_start.isoformat(), "cutoff_at": window.cutoff_at.isoformat(),
        "scoring_mode": mode, "scores": records,
        "audit": {
            "scoring_mode": mode, "model_name": MODEL_NAME, "prompt_version": PROMPT_VERSION,
            "lexicon_model": LEXICON_MODEL if degraded else None,
            "fallback_reason": unavailable, "daily_limit": daily_limit, "eligible_posts": len(audit.eligible_posts),
            "alert_flags": ["DEEPSEEK_FALLBACK"] if degraded and unavailable != QUOTA_UNAVAILABLE else [],
            "allow_peak": allow_peak,
            "selected_posts": len(selected), "unselected_posts": len(audit.eligible_posts) - len(selected),
            "cache_hits": sum(record["cache_hit"] for record in records), "submitted_texts": submitted,
            "degraded_posts": degraded, "late_unavailable_count": audit.late_unavailable_count,
            "out_of_window_count": audit.out_of_window_count, "duplicate_posts": len(posts) - len(unique_posts),
            "sources": sources,
        },
    }
