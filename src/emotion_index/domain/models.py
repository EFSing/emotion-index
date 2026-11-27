"""Immutable domain models for social media posts, point-in-time windows, and sentiment snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class EmotionRegime(str, Enum):
    EXTREME_FEAR = "EXTREME_FEAR"
    FEAR = "FEAR"
    NEUTRAL = "NEUTRAL"
    GREED = "GREED"
    EXTREME_GREED = "EXTREME_GREED"


def parse_utc_timestamp(raw: str, field_name: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field_name} must be a non-empty ISO-8601 UTC string")
    normalized = raw.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        raise ValueError(f"{field_name} must include an explicit timezone offset: {raw}")
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class ObservationWindow:
    snapshot_id: str
    market: str
    trading_date: str
    session: str
    window_start: datetime
    cutoff_at: datetime
    allowed_platforms: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id cannot be empty")
        if self.market not in {"US", "CN", "HK"}:
            raise ValueError(f"Unsupported market: {self.market}")
        if not self.trading_date.strip() or not self.session.strip():
            raise ValueError("trading_date and session are required")
        if self.window_start.tzinfo is None or self.cutoff_at.tzinfo is None:
            raise ValueError("ObservationWindow timestamps must be timezone-aware")
        if self.window_start >= self.cutoff_at:
            raise ValueError("window_start must be strictly earlier than cutoff_at")
        if not self.allowed_platforms:
            raise ValueError("allowed_platforms cannot be empty")


@dataclass(frozen=True)
class SocialPost:
    platform: str
    post_id: str
    author_id: str
    published_at: datetime
    available_at: datetime
    tickers: tuple[str, ...]
    text: str
    likes: int
    replies: int
    reposts: int

    def __post_init__(self) -> None:
        if not self.platform.strip() or not self.post_id.strip():
            raise ValueError("platform and post_id cannot be empty")
        if self.published_at.tzinfo is None or self.available_at.tzinfo is None:
            raise ValueError("SocialPost timestamps must be timezone-aware")
        if self.available_at < self.published_at:
            raise ValueError(
                f"Post {self.platform}:{self.post_id} available_at precede published_at"
            )
        if self.likes < 0 or self.replies < 0 or self.reposts < 0:
            raise ValueError("Engagement counts must be non-negative")


@dataclass(frozen=True)
class SourceAuditSummary:
    total_input_posts: int
    eligible_posts: tuple[SocialPost, ...]
    late_unavailable_count: int
    out_of_window_count: int
    unsupported_platform_count: int


@dataclass(frozen=True)
class ScoredPost:
    post: SocialPost
    polarity: float
    engagement_weight: float
    weighted_score: float
    matched_bullish: tuple[str, ...]
    matched_bearish: tuple[str, ...]


@dataclass(frozen=True)
class EmotionIndexSnapshot:
    window: ObservationWindow
    model_version: str
    eligible_post_count: int
    late_unavailable_count: int
    out_of_window_count: int
    net_sentiment: float
    emotion_index: float
    regime: EmotionRegime
    scored_posts: tuple[ScoredPost, ...]


@dataclass(frozen=True)
class TickerEmotionSummary:
    ticker: str
    mention_count: int
    net_sentiment: float
    emotion_index: float


@dataclass(frozen=True)
class PlatformEmotionSummary:
    platform: str
    post_count: int
    net_sentiment: float
    emotion_index: float


@dataclass(frozen=True)
class ResearchReportProjection:
    snapshot: EmotionIndexSnapshot
    ticker_summaries: tuple[TickerEmotionSummary, ...]
    platform_summaries: tuple[PlatformEmotionSummary, ...]
