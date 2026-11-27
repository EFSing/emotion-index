"""Validated market data available in a frozen provider response."""

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite


@dataclass(frozen=True)
class DailyBar:
    trading_date: date
    close: float
    volume: float | None

    def __post_init__(self) -> None:
        if not isfinite(self.close) or self.close <= 0:
            raise ValueError("bar close must be finite and positive")
        if self.volume is not None and (not isfinite(self.volume) or self.volume < 0):
            raise ValueError("bar volume must be finite and non-negative")


@dataclass(frozen=True)
class MarketSeries:
    symbol: str
    available_at: datetime
    bars: tuple[DailyBar, ...]

    def __post_init__(self) -> None:
        days = tuple(bar.trading_date for bar in self.bars)
        if self.available_at.utcoffset() is None or tuple(sorted(set(days))) != days:
            raise ValueError("market series requires a timezone and unique, ordered trading dates")
