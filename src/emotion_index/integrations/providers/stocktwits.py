"""Stocktwits public v2 symbol streams, one page per symbol."""

from emotion_index.integrations.providers.http import Feed, json_list

SYMBOLS = ("SPY", "QQQ", "XLK", "XLF", "XLU", "IWM")


def feeds() -> tuple[Feed, ...]:
    validate = json_list("messages")
    return tuple(
        Feed("stocktwits", symbol, f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json", validate)
        for symbol in SYMBOLS
    )
