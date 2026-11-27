"""US anchor inputs from CNN, Yahoo Finance and optional Finnhub."""

import json
from typing import Mapping
from urllib.parse import quote, urlencode

from emotion_index.integrations.providers.http import Feed

SYMBOLS = ("^VIX", "SPY", "QQQ", "XLF", "IWM", "XLU")


def _cnn(body: bytes) -> str | None:
    score = json.loads(body)["fear_and_greed"]["score"]
    if type(score) not in {int, float} or not 0 <= score <= 100:
        return "INVALID_ANCHOR"
    return None


def _yahoo(body: bytes) -> str | None:
    chart = json.loads(body)["chart"]
    if chart.get("error") or not chart.get("result"):
        return "ANCHOR_DATA_UNAVAILABLE"
    result = chart["result"][0]
    if not result["timestamp"] or not result["indicators"]["quote"][0]["close"]:
        return "ANCHOR_DATA_UNAVAILABLE"
    return None


def _finnhub(body: bytes) -> str | None:
    if not isinstance(json.loads(body)["sentiment"], dict):
        return "INVALID_ANCHOR"
    return None


def feeds(env: Mapping[str, str]) -> tuple[Feed, ...]:
    base = "https://query1.finance.yahoo.com/v8/finance/chart/"
    yahoo = tuple(
        Feed("us_anchor", f"yahoo:{symbol}", base + quote(symbol, safe="") + "?range=3mo&interval=1d", _yahoo)
        for symbol in SYMBOLS
    )
    token = env.get("EMOTION_INDEX_FINNHUB_TOKEN", "").strip()
    finnhub_url = "https://finnhub.io/api/v1/news-sentiment?" + urlencode({"symbol": "SPY"})
    return (
        Feed("us_anchor", "cnn_fear_and_greed", "https://production.dataviz.cnn.io/index/fearandgreed/graphdata", _cnn),
        *yahoo,
        Feed("us_anchor", "finnhub:SPY", finnhub_url if token else "", _finnhub,
             (("X-Finnhub-Token", token),) if token else ()),
    )
