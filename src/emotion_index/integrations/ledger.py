"""Point-in-time JSONL ledger with one atomically updatable row per trading day."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from emotion_index.domain.ledger import LedgerEntry
from emotion_index.domain.market import Market, Sector
from emotion_index.domain.models import parse_utc_timestamp
from emotion_index.integrations.file_lock import file_lock
from emotion_index.integrations.storage import atomic_replace_bytes, read_optional_bytes


def _encode(entry: LedgerEntry) -> str:
    return json.dumps({
        "schema_version": 1,
        "market": entry.market.value,
        "trading_date": entry.trading_date.isoformat(),
        "cutoff_at": entry.cutoff_at.isoformat(),
        "post_counts": {sector.value: count for sector, count in entry.post_counts},
        "text_emotion_index": entry.text_emotion_index,
        "volume_zscore_20d": entry.volume_zscore_20d,
        "anchor_score": entry.anchor_score,
        "composite_score": entry.composite_score,
    }, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _decode(line: bytes, market: Market) -> LedgerEntry:
    row = json.loads(line)
    if row["schema_version"] != 1 or row["market"] != market.value:
        raise ValueError("ledger schema or market mismatch")
    counts = row["post_counts"]
    if not isinstance(counts, dict) or (counts and set(counts) != {sector.value for sector in Sector}):
        raise ValueError("invalid ledger sectors")
    return LedgerEntry(
        market, date.fromisoformat(row["trading_date"]), parse_utc_timestamp(row["cutoff_at"], "cutoff_at"),
        tuple((sector, counts[sector.value]) for sector in Sector if sector.value in counts),
        row["text_emotion_index"], row["volume_zscore_20d"], row["anchor_score"], row["composite_score"],
    )


class EmotionLedger:
    def __init__(self, market: Market, runtime: Path = Path(".runtime")) -> None:
        self.market = market
        self.path = runtime / "data" / "ledger" / f"emotion_ledger_{market.value}.jsonl"
        self.lock = runtime / "tmp" / f"ledger-{market.value}.lock"

    def _read(self) -> dict[date, LedgerEntry]:
        rows = {}
        for number, line in enumerate((read_optional_bytes(self.path) or b"").splitlines(), start=1):
            try:
                row = _decode(line, self.market)
                if row.trading_date in rows:
                    raise ValueError("duplicate trading date")
            except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
                raise ValueError(f"invalid ledger row {number} in {self.path}: {error}") from None
            rows[row.trading_date] = row
        return rows

    def read_history(self, *, before: date, cutoff_at: datetime) -> tuple[LedgerEntry, ...]:
        if cutoff_at.utcoffset() is None:
            raise ValueError("ledger cutoff must be timezone-aware")
        with file_lock(self.lock):
            rows = self._read()
        history = (row for row in rows.values() if row.trading_date < before and row.cutoff_at <= cutoff_at)
        return tuple(sorted(history, key=lambda row: row.trading_date))

    def upsert(self, entry: LedgerEntry) -> None:
        if entry.market is not self.market:
            raise ValueError("ledger entry belongs to another market")
        with file_lock(self.lock):
            rows = self._read()
            previous = rows.get(entry.trading_date)
            if previous == entry:
                return
            if previous is not None and entry.cutoff_at < previous.cutoff_at:
                raise ValueError("cannot overwrite a ledger entry with an earlier cutoff")
            rows[entry.trading_date] = entry
            content = "".join(_encode(rows[day]) + "\n" for day in sorted(rows))
            atomic_replace_bytes(self.path, content.encode("utf-8"))
