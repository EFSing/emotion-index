"""A-share anchor inputs from Tencent Finance public daily bars."""

import json
from functools import partial
from urllib.parse import urlencode

from emotion_index.integrations.providers.http import Feed

# Broad indices, tech growth, cyclicals/finance proxy, dividend ETF and high-beta small-cap ETF.
SYMBOLS = ("sh000001", "sh000300", "sz399006", "sh000688", "sh512800", "sh510880", "sh563300")


def _validate(symbol: str, body: bytes) -> str | None:
    payload = json.loads(body)
    if payload.get("code") != 0 or not payload.get("data"):
        return "ANCHOR_DATA_UNAVAILABLE"
    series = payload["data"].get(symbol, {})
    if not (isinstance(series, dict) and (series.get("day") or series.get("qfqday"))):
        return "ANCHOR_DATA_UNAVAILABLE"
    return None


def feeds() -> tuple[Feed, ...]:
    base = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
    return tuple(
        Feed("cn_anchor", f"tencent:{symbol}", base + urlencode({"param": f"{symbol},day,,,40"}),
             partial(_validate, symbol))
        for symbol in SYMBOLS
    )
