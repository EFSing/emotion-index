"""Point-in-time discussion volume, market anchors, and divergence signals."""

from __future__ import annotations

from datetime import date
from statistics import mean, pstdev

from emotion_index.domain.market import (
    DivergenceFlag,
    MarketAnchor,
    MarketSnapshot,
    MarketTextSnapshot,
    Sector,
    ThreeTrackReading,
    VolumeFlag,
    VolumeObservation,
    VolumeReading,
)


def _volume_reading(post_count: int, by_day: dict[date, int]) -> VolumeReading:
    days = sorted(by_day)[-20:]
    if len(days) < 5:
        return VolumeReading(None, len(days), VolumeFlag.WARMUP)

    counts = [by_day[day] for day in days]
    deviation = pstdev(counts)
    if deviation == 0.0:
        return VolumeReading(None, len(days), VolumeFlag.ZERO_VARIANCE)

    zscore = (post_count - mean(counts)) / deviation
    flag = None
    if zscore >= 1.5:
        flag = VolumeFlag.VOLUME_SPIKE
    elif zscore <= -1.0:
        flag = VolumeFlag.VOLUME_COLD
    return VolumeReading(zscore, len(days), flag)


def classify_divergences(
    text_emotion_index: float | None,
    volume_zscore_20d: float | None,
    anchor_score: float | None,
) -> tuple[DivergenceFlag, ...]:
    if text_emotion_index is None:
        return ()

    flags = []
    if anchor_score is not None:
        if (
            text_emotion_index >= 70.0 and anchor_score <= 45.0
            and volume_zscore_20d is not None and volume_zscore_20d >= 1.0
        ):
            flags.append(DivergenceFlag.SOCIAL_OVERHEATED_VS_MARKET_WEAK)
        if text_emotion_index <= 30.0 and anchor_score >= 55.0:
            flags.append(DivergenceFlag.SOCIAL_PANIC_VS_MARKET_RESILIENT)

    if text_emotion_index >= 80.0:
        flags.append(DivergenceFlag.EXTREME_EUPHORIA_WARNING)
    elif text_emotion_index <= 20.0:
        flags.append(DivergenceFlag.EXTREME_CAPITULATION_ALERT)
    return tuple(flags)


def build_market_snapshot(
    text: MarketTextSnapshot,
    history: tuple[VolumeObservation, ...] = (),
    anchors: tuple[MarketAnchor, ...] = (),
    *,
    volume_observed: bool = True,
) -> MarketSnapshot:
    """Build the three tracks from same-market, cutoff-eligible observations."""
    market = text.audit.market
    window = text.audit.window
    trading_date = date.fromisoformat(window.trading_date)

    history_by_sector: dict[Sector, dict[date, int]] = {}
    for item in history:
        if item.market != market or item.trading_date >= trading_date or item.available_at > window.cutoff_at:
            continue
        by_day = history_by_sector.setdefault(item.sector, {})
        if item.trading_date in by_day:
            raise ValueError(f"Duplicate volume observation: {market.value}/{item.sector.value}/{item.trading_date}")
        by_day[item.trading_date] = item.post_count

    anchor_by_sector: dict[Sector, MarketAnchor] = {}
    for anchor in anchors:
        if anchor.market != market or anchor.trading_date != trading_date or anchor.available_at > window.cutoff_at:
            continue
        if anchor.sector in anchor_by_sector:
            raise ValueError(f"Duplicate market anchor: {market.value}/{anchor.sector.value}/{trading_date}")
        anchor_by_sector[anchor.sector] = anchor

    readings: dict[Sector, ThreeTrackReading] = {}
    for bucket in text.buckets:
        by_day = history_by_sector.get(bucket.sector, {})
        volume = (_volume_reading(bucket.post_count, by_day) if volume_observed else
                  VolumeReading(None, min(20, len(by_day)), VolumeFlag.NO_SOURCE_DATA))
        anchor = anchor_by_sector.get(bucket.sector)
        anchor_score = anchor.anchor_score if anchor is not None else None
        text_score = bucket.text_emotion_index
        # Volume measures attention and has no bullish or bearish direction.
        composite = (text_score + anchor_score) / 2 if text_score is not None and anchor_score is not None else None
        readings[bucket.sector] = ThreeTrackReading(
            text=bucket,
            volume=volume,
            market_anchor=anchor,
            composite_score=composite,
            divergence_flags=classify_divergences(text_score, volume.volume_zscore_20d, anchor_score),
        )
    return MarketSnapshot(
        audit=text.audit,
        broad_market=readings[Sector.BROAD_MARKET],
        sectors=tuple(readings[sector] for sector in Sector if sector != Sector.BROAD_MARKET),
    )
