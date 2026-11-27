"""Market-scoped standard RSS/Atom and self-hosted RSSHub routes."""

from typing import Mapping
from xml.etree import ElementTree

from emotion_index.domain.market import Market
from emotion_index.integrations.providers.http import Feed

COMMON_ROUTES = (
    ("xueqiu", "xueqiu"),
    ("xueqiu", "xueqiu_user"),
    ("chinese_news", "wallstreetcn"),
    ("chinese_news", "jin10"),
)


def _validate(body: bytes) -> str | None:
    # Standard feeds do not need a DTD or entity declarations.
    if b"\x00" in body or b"<!DOCTYPE" in body.upper():
        return "INVALID_FEED"
    try:
        root = ElementTree.fromstring(body)
    except (ElementTree.ParseError, LookupError):
        return "INVALID_FEED"
    if root.tag == "rss" and root.find("channel") is not None:
        return None
    if root.tag in {"{http://www.w3.org/2005/Atom}feed", "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF"}:
        return None
    return "INVALID_FEED"


def feeds(market: Market, env: Mapping[str, str]) -> tuple[Feed, ...]:
    routes = COMMON_ROUTES
    if market is Market.US:
        routes += (("chinese_news", "cls"),)
    else:
        routes += (("cls", "cls"), ("chinese_news", "sina"), ("jisilu", "jisilu"))
    result = []
    for platform, source in routes:
        prefix = f"EMOTION_INDEX_RSS_{market.value}_{source.upper()}"
        cookie = env.get(f"{prefix}_COOKIE")
        headers = (("Cookie", cookie),) if cookie else ()
        result.append(Feed(platform, source, env.get(f"{prefix}_URL", "").strip(), _validate, headers,
                           xueqiu=platform == "xueqiu"))
    return tuple(result)
