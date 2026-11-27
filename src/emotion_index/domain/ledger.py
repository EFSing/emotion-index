"""Immutable daily inputs for volume history and trend rendering."""

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite

from emotion_index.domain.market import Market, Sector, VolumeObservation


@dataclass(frozen=True)
class LedgerEntry:
    market: Market
    trading_date: date
    cutoff_at: datetime
    post_counts: tuple[tuple[Sector, int], ...]
    text_emotion_index: float | None = None
    volume_zscore_20d: float | None = None
    anchor_score: float | None = None
    composite_score: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.market, Market) or type(self.trading_date) is not date:
            raise ValueError("invalid ledger session")
        if self.cutoff_at.utcoffset() is None:
            raise ValueError("ledger cutoff must be timezone-aware")
        sectors = tuple(sector for sector, _ in self.post_counts)
        if self.post_counts and sectors != tuple(Sector):
            raise ValueError("ledger entries with volume require counts for every sector")
        if any(type(count) is not int or count < 0 for _, count in self.post_counts):
            raise ValueError("invalid ledger post count")
        for score in (self.text_emotion_index, self.anchor_score, self.composite_score):
            if score is not None and (type(score) not in {int, float} or not 0 <= score <= 100):
                raise ValueError("invalid ledger score")
        zscore = self.volume_zscore_20d
        if zscore is not None and (type(zscore) not in {int, float} or not isfinite(zscore)):
            raise ValueError("invalid ledger z-score")

    def volume_observations(self) -> tuple[VolumeObservation, ...]:
        return tuple(VolumeObservation(self.market, self.trading_date, self.cutoff_at, count, sector)
                     for sector, count in self.post_counts)
