"""Immutable inputs and outputs for the V2 dual-market index."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Mapping, Sequence

from emotion_index.domain.models import ObservationWindow, SocialPost


class Market(str, Enum):
    US = "US"
    CN = "CN"


class Sector(str, Enum):
    BROAD_MARKET = "BROAD_MARKET"
    TECH_GROWTH = "TECH_GROWTH"
    CYCLICAL_FINANCE = "CYCLICAL_FINANCE"
    DEFENSIVE_DIVIDEND = "DEFENSIVE_DIVIDEND"
    HIGH_BETA_THEME = "HIGH_BETA_THEME"


class EmotionState(str, Enum):
    EUPHORIA_FOMO = "EUPHORIA_FOMO"
    OPTIMISTIC = "OPTIMISTIC"
    NEUTRAL = "NEUTRAL"
    ANXIOUS = "ANXIOUS"
    PANIC_CAPITULATION = "PANIC_CAPITULATION"


class TextFlag(str, Enum):
    XHS_NOT_PROVIDED = "XHS_NOT_PROVIDED"
    XHS_NOT_SCORED = "XHS_NOT_SCORED"


class VolumeFlag(str, Enum):
    WARMUP = "WARMUP"
    ZERO_VARIANCE = "ZERO_VARIANCE"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    VOLUME_COLD = "VOLUME_COLD"


class DivergenceFlag(str, Enum):
    SOCIAL_OVERHEATED_VS_MARKET_WEAK = "SOCIAL_OVERHEATED_VS_MARKET_WEAK"
    SOCIAL_PANIC_VS_MARKET_RESILIENT = "SOCIAL_PANIC_VS_MARKET_RESILIENT"
    EXTREME_EUPHORIA_WARNING = "EXTREME_EUPHORIA_WARNING"
    EXTREME_CAPITULATION_ALERT = "EXTREME_CAPITULATION_ALERT"


@dataclass(frozen=True)
class MarketWeights:
    market: Market
    automatic: tuple[tuple[str, float], ...]
    xhs_weight: float

    @classmethod
    def from_mapping(
        cls, market: Market, automatic: Mapping[str, float], xhs_weight: float,
    ) -> MarketWeights:
        return cls(market, tuple(sorted(automatic.items())), xhs_weight)

    def __post_init__(self) -> None:
        if not isinstance(self.market, Market):
            raise ValueError("weights require a US or CN market")
        platforms = set()
        for platform, weight in self.automatic:
            if (
                not platform or platform != platform.strip().lower()
                or platform == "xhs" or platform in platforms
            ):
                raise ValueError(f"Invalid or duplicate automatic platform: {platform!r}")
            platforms.add(platform)
            if isinstance(weight, bool) or not 0.0 < weight <= 1.0:
                raise ValueError(f"Invalid weight for {platform}: {weight!r}")
        if not math.isclose(math.fsum(weight for _, weight in self.automatic), 1.0):
            raise ValueError("automatic weights must sum to 1")
        if isinstance(self.xhs_weight, bool) or not 0.0 < self.xhs_weight < 1.0:
            raise ValueError("xhs_weight must be between 0 and 1")

    @property
    def platforms(self) -> frozenset[str]:
        return frozenset(platform for platform, _ in self.automatic) | {"xhs"}


@dataclass(frozen=True)
class MarketSectors:
    market: Market
    by_symbol: tuple[tuple[str, Sector], ...]

    @classmethod
    def from_mapping(cls, market: Market, groups: Mapping[str, Sequence[str]]) -> MarketSectors:
        if set(groups) != {sector.value for sector in Sector}:
            raise ValueError("sector configuration must define all five sectors")
        if any(isinstance(symbols, str) for symbols in groups.values()):
            raise ValueError("each sector must use a sequence of symbols")
        return cls(market, tuple(
            (symbol.strip().upper(), sector)
            for sector in Sector
            for symbol in groups[sector.value]
        ))

    def __post_init__(self) -> None:
        if not isinstance(self.market, Market):
            raise ValueError("sector configuration requires a US or CN market")
        symbols = set()
        for symbol, sector in self.by_symbol:
            if not symbol or symbol != symbol.strip().upper() or symbol in symbols:
                raise ValueError(f"Invalid or duplicate symbol: {symbol!r}")
            if not isinstance(sector, Sector):
                raise ValueError(f"Invalid sector for {symbol}: {sector!r}")
            symbols.add(symbol)


@dataclass(frozen=True)
class MarketPost:
    post: SocialPost
    market: Market
    sector: Sector = Sector.BROAD_MARKET

    def __post_init__(self) -> None:
        if not isinstance(self.market, Market) or not isinstance(self.sector, Sector):
            raise ValueError("posts require a US or CN market and a valid sector")


@dataclass(frozen=True)
class PostSentiment:
    """A score for the exact frozen post, including its market and routing."""

    market_post: MarketPost
    polarity: float
    emotion_state: EmotionState

    def __post_init__(self) -> None:
        if isinstance(self.polarity, bool) or not -1.0 <= self.polarity <= 1.0:
            raise ValueError("polarity must be between -1 and 1")
        if not isinstance(self.emotion_state, EmotionState):
            raise ValueError("invalid emotion_state")


@dataclass(frozen=True)
class MarketSourceAudit:
    window: ObservationWindow
    eligible_posts: tuple[MarketPost, ...]
    late_unavailable_count: int
    out_of_window_count: int
    unsupported_platform_count: int

    def __post_init__(self) -> None:
        date.fromisoformat(self.window.trading_date)
        market = self.market
        allowed = {platform.lower() for platform in self.window.allowed_platforms}
        identities = set()
        for item in self.eligible_posts:
            post = item.post
            if item.market != market or not (
                self.window.window_start
                <= post.published_at
                <= post.available_at
                <= self.window.cutoff_at
            ):
                raise ValueError("eligible posts must belong to the market and observation window")
            platform = post.platform.lower()
            if platform not in allowed:
                raise ValueError(f"Platform is outside the observation allowlist: {platform}")
            identity = (platform, post.post_id)
            if identity in identities:
                raise ValueError(f"Duplicate post identity detected: {platform}:{post.post_id}")
            identities.add(identity)

    @property
    def market(self) -> Market:
        return Market(self.window.market)


@dataclass(frozen=True)
class TextEmotion:
    sector: Sector
    post_count: int
    scored_post_count: int
    text_emotion_index: float | None
    euphoria_ratio: float | None
    panic_ratio: float | None
    effective_weights: tuple[tuple[str, float], ...]
    missing_automatic_platforms: tuple[str, ...]
    flags: tuple[TextFlag, ...]


@dataclass(frozen=True)
class MarketTextSnapshot:
    audit: MarketSourceAudit
    buckets: tuple[TextEmotion, ...]

    def __post_init__(self) -> None:
        if len(self.buckets) != len(Sector) or {bucket.sector for bucket in self.buckets} != set(Sector):
            raise ValueError("text snapshot must contain one reading per sector")


def _validate_daily_input(
    market: Market, trading_date: date, sector: Sector, available_at: datetime,
) -> None:
    if not isinstance(market, Market) or not isinstance(sector, Sector):
        raise ValueError("daily inputs require a US or CN market and a valid sector")
    if type(trading_date) is not date:
        raise ValueError("trading_date must be a date")
    if available_at.utcoffset() is None:
        raise ValueError("available_at must be timezone-aware")


@dataclass(frozen=True)
class VolumeObservation:
    market: Market
    trading_date: date
    available_at: datetime
    post_count: int
    sector: Sector = Sector.BROAD_MARKET

    def __post_init__(self) -> None:
        _validate_daily_input(self.market, self.trading_date, self.sector, self.available_at)
        if type(self.post_count) is not int or self.post_count < 0:
            raise ValueError("post_count must be a non-negative integer")


@dataclass(frozen=True)
class MarketAnchor:
    """A normalized market or style score from a point-in-time observation."""

    market: Market
    trading_date: date
    available_at: datetime
    anchor_score: float
    sector: Sector = Sector.BROAD_MARKET

    def __post_init__(self) -> None:
        _validate_daily_input(self.market, self.trading_date, self.sector, self.available_at)
        if isinstance(self.anchor_score, bool) or not 0.0 <= self.anchor_score <= 100.0:
            raise ValueError("anchor_score must be between 0 and 100")


@dataclass(frozen=True)
class VolumeReading:
    volume_zscore_20d: float | None
    baseline_days: int
    flag: VolumeFlag | None


@dataclass(frozen=True)
class ThreeTrackReading:
    text: TextEmotion
    volume: VolumeReading
    market_anchor: MarketAnchor | None
    composite_score: float | None
    divergence_flags: tuple[DivergenceFlag, ...]


@dataclass(frozen=True)
class MarketSnapshot:
    audit: MarketSourceAudit
    broad_market: ThreeTrackReading
    sectors: tuple[ThreeTrackReading, ...]
