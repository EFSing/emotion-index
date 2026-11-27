"""Decode provider bars and CNN scores from verified PR-2 snapshots."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from emotion_index.domain.anchor import DailyBar, MarketSeries
from emotion_index.domain.market import Market, MarketAnchor
from emotion_index.domain.models import parse_utc_timestamp
from emotion_index.integrations.capture import load_frozen_responses
from emotion_index.integrations.providers.cn_anchor import SYMBOLS as CN_SYMBOLS
from emotion_index.integrations.providers.us_anchor import SYMBOLS as US_SYMBOLS

INVALID_DATA = (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError)


def _number(value: object) -> float:
    if type(value) not in {float, int, str}:
        raise ValueError("invalid numeric field")
    return float(value)


def _yahoo(document: dict) -> tuple[DailyBar, ...]:
    result = document["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    return tuple(
        DailyBar(datetime.fromtimestamp(stamp, timezone.utc).date(), _number(close), None)
        for stamp, close in zip(result["timestamp"], quote["close"], strict=True) if close is not None
    )


def _tencent(document: dict, symbol: str) -> tuple[DailyBar, ...]:
    if document["code"] != 0:
        raise ValueError("Tencent anchor unavailable")
    series = document["data"][symbol]
    return tuple(
        DailyBar(date.fromisoformat(row[0]), _number(row[2]),
                 _number(row[5]) if len(row) > 5 and row[5] is not None else None)
        for row in (series.get("day") or series["qfqday"])
    )


def load_market_anchors(
    raw_market: Path, market: Market, day: date, cutoff_at: datetime | None,
) -> tuple[tuple[MarketSeries, ...], MarketAnchor | None, datetime | None, dict]:
    platform = "us_anchor" if market is Market.US else "cn_anchor"
    identity = {"schema_version": 1, "market": market.value, "platform": platform, "capture_date": day.isoformat()}
    diagnostics = {"eligible_observations": [], "late_responses": 0, "invalid_responses": 0}
    try:
        responses = load_frozen_responses(raw_market / platform / f"{day}.json", identity=identity)
    except FileNotFoundError:
        return (), None, None, {**diagnostics, "status": "SNAPSHOT_MISSING"}
    except (OSError, *INVALID_DATA):
        return (), None, None, {**diagnostics, "status": "SNAPSHOT_UNREADABLE_OR_INVALID"}

    series = {}
    cnn = None
    latest = None
    for response in sorted(responses, key=lambda item: item.fetched_at):
        if response.reason is not None:
            diagnostics["invalid_responses"] += 1
            continue
        if cutoff_at is not None and response.fetched_at > cutoff_at:
            diagnostics["late_responses"] += 1
            continue
        latest = response.fetched_at
        try:
            provider, _, symbol = response.source.partition(":")
            if market is Market.US and provider == "cnn_fear_and_greed":
                data = json.loads(response.body)["fear_and_greed"]
                if "timestamp" in data:
                    stamp = parse_utc_timestamp(data["timestamp"], "CNN timestamp")
                    if stamp > response.fetched_at or stamp.date() != day:
                        continue
                cnn = MarketAnchor(market, day, response.fetched_at, _number(data["score"]))
            elif market is Market.US and provider == "yahoo" and symbol in US_SYMBOLS:
                series[symbol] = MarketSeries(symbol, response.fetched_at, _yahoo(json.loads(response.body)))
            elif market is Market.CN and provider == "tencent" and symbol in CN_SYMBOLS:
                series[symbol] = MarketSeries(symbol, response.fetched_at, _tencent(json.loads(response.body), symbol))
        except (OSError, *INVALID_DATA):
            diagnostics["invalid_responses"] += 1

    diagnostics["eligible_observations"] = (
        (["cnn_fear_and_greed"] if cnn is not None else [])
        + sorted(symbol for symbol, item in series.items() if any(bar.trading_date == day for bar in item.bars))
    )
    return tuple(series.values()), cnn, latest, {**diagnostics, "status": "VERIFIED"}
