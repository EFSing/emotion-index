"""Read PR-2 frozen public responses as timestamped social posts."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from emotion_index.domain.market import Market, MarketSectors, MarketWeights
from emotion_index.domain.models import SocialPost, parse_utc_timestamp
from emotion_index.integrations.capture import load_frozen_responses
from emotion_index.integrations.providers.http import Response
from emotion_index.integrations.providers.xhs_inbox import parse_posts as parse_xhs

ATOM = "http://www.w3.org/2005/Atom"
RSS1 = "http://purl.org/rss/1.0/"
CASHTAG = re.compile(r"\$([A-Za-z]{1,10}|\d{6}(?:\.[A-Za-z]+)?)\b")
POST_ERRORS = (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError)


def load_market_config(market: Market) -> tuple[MarketWeights, MarketSectors]:
    weights = json.loads(Path("configs/weights.json").read_bytes())[market.value]
    sectors = json.loads(Path("configs/sectors.json").read_bytes())[market.value]
    return (MarketWeights.from_mapping(market, weights["automatic"], weights["xhs_weight"]),
            MarketSectors.from_mapping(market, sectors))


def latest_capture_date(raw_market: Path) -> date:
    dates = []
    names = [path.stem for path in raw_market.glob("*/*.json")]
    names.extend(path.name for path in (raw_market / "_receipts").glob("*") if path.is_dir())
    for name in names:
        try:
            day = date.fromisoformat(name)
        except ValueError:
            continue
        if day.isoformat() == name:
            dates.append(day)
    if not dates:
        raise ValueError("该市场暂无冻结快照。请先运行 capture。")
    return max(dates)


def load_market_posts(
    raw_market: Path, market: Market, day: date, platforms: frozenset[str],
) -> tuple[tuple[SocialPost, ...], datetime | None, dict[str, dict[str, int | str]]]:
    posts = []
    latest = None
    diagnostics = {}
    for platform in sorted(platforms):
        counts = {"parsed_posts": 0, "invalid_rows": 0, "invalid_responses": 0}
        diagnostics[platform] = counts
        path = raw_market / platform / f"{day.isoformat()}.json"
        identity = {"schema_version": 1, "market": market.value, "platform": platform, "capture_date": day.isoformat()}
        try:
            responses = load_frozen_responses(path, identity=identity)
        except FileNotFoundError:
            counts["status"] = "SNAPSHOT_MISSING"
            continue
        except (OSError, *POST_ERRORS):
            counts["status"] = "SNAPSHOT_UNREADABLE_OR_INVALID"
            continue
        counts["status"] = "AGGREGATE_ONLY" if platform == "apewisdom" else "VERIFIED"
        for response in responses:
            if response.reason is not None:
                counts["invalid_responses"] += 1
                continue
            if latest is None or response.fetched_at > latest:
                latest = response.fetched_at
            if platform == "apewisdom":
                continue
            try:
                parsed, rejected = _parse(platform, market, response)
            except (ElementTree.ParseError, LookupError, *POST_ERRORS):
                counts["invalid_responses"] += 1
                continue
            counts["parsed_posts"] += len(parsed)
            counts["invalid_rows"] += rejected
            posts.extend(parsed)
    return tuple(posts), latest, diagnostics


def _parse(platform: str, market: Market, response: Response) -> tuple[tuple[SocialPost, ...], int]:
    if platform == "xhs":
        posts = tuple(item.post for item in parse_xhs(json.loads(response.body), market, response.fetched_at))
        for post in posts:
            post.text.encode("utf-8")
        return posts, 0
    if platform == "bluesky":
        rows = json.loads(response.body)["posts"]
        parse = _bluesky_post
    elif platform == "stocktwits":
        rows = json.loads(response.body)["messages"]
        parse = _stocktwits_post
    else:
        rows = _feed_rows(response.body)
        parse = _feed_post
    if not isinstance(rows, list):
        raise ValueError("invalid frozen posts")
    posts = []
    rejected = 0
    for row in rows:
        try:
            post = parse(row, platform, response)
        except POST_ERRORS:
            rejected += 1
        else:
            posts.append(post)
    return tuple(posts), rejected


def _post(
    platform: str, response: Response, post_id: str, author: str, published: datetime, text: str,
    symbols: tuple[str, ...] = (), likes: int = 0, replies: int = 0, reposts: int = 0,
) -> SocialPost:
    if not all(isinstance(value, str) for value in (post_id, author, text, *symbols)) or not text.strip():
        raise ValueError("invalid post text or identity")
    for value in (post_id, author, text, *symbols):
        value.encode("utf-8")
    if any(type(value) is not int for value in (likes, replies, reposts)):
        raise ValueError("invalid engagement counts")
    tickers = tuple(dict.fromkeys(symbol.upper() for symbol in (*symbols, *CASHTAG.findall(text))))
    return SocialPost(platform, post_id, author, published, response.fetched_at, tickers, text, likes, replies, reposts)


def _bluesky_post(row: dict, platform: str, response: Response) -> SocialPost:
    record = row["record"]
    return _post(
        platform, response, row["uri"], row.get("author", {}).get("did", ""),
        parse_utc_timestamp(record["createdAt"], "createdAt"), record["text"],
        likes=row.get("likeCount", 0), replies=row.get("replyCount", 0),
        reposts=row.get("repostCount", 0),
    )


def _stocktwits_post(row: dict, platform: str, response: Response) -> SocialPost:
    post_id = row["id"]
    if type(post_id) is not int and not isinstance(post_id, str):
        raise ValueError("invalid Stocktwits ID")
    symbols = tuple(symbol["symbol"] for symbol in (row.get("symbols") or []))
    return _post(
        platform, response, str(post_id), str((row.get("user") or {}).get("id", "")),
        parse_utc_timestamp(row["created_at"], "created_at"), row["body"], symbols or (response.source,),
        likes=(row.get("likes") or {}).get("total", 0), replies=(row.get("conversation") or {}).get("replies", 0),
        reposts=(row.get("reshares") or {}).get("reshared_count", 0),
    )


def _feed_rows(body: bytes) -> list[ElementTree.Element]:
    if b"\x00" in body or b"<!DOCTYPE" in body.upper():
        raise ValueError("invalid XML feed")
    root = ElementTree.fromstring(body)
    if root.tag == "rss":
        return root.findall("channel/item")
    if root.tag == f"{{{ATOM}}}feed":
        return root.findall(f"{{{ATOM}}}entry")
    if root.tag == "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF":
        return root.findall(f"{{{RSS1}}}item")
    raise ValueError("unknown XML feed")


class _FeedText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _feed_post(row: ElementTree.Element, platform: str, response: Response) -> SocialPost:
    fields = {}
    for child in row:
        key = child.tag.rsplit("}", 1)[-1]
        value = "".join(child.itertext()).strip() or child.get("href", "")
        if value:
            fields.setdefault(key, value)
    stamp = next((fields[key] for key in ("pubDate", "published", "date", "updated") if key in fields), "")
    try:
        published = parse_utc_timestamp(stamp, "feed publication")
    except ValueError:
        published = parsedate_to_datetime(stamp)
        if published.utcoffset() is None:
            raise ValueError("feed publication requires an explicit timezone")
        published = published.astimezone(timezone.utc)
    text_parts = []
    for field in ("title", "encoded", "content", "description", "summary"):
        parser = _FeedText()
        parser.feed(fields.get(field, ""))
        parser.close()
        text = " ".join(" ".join(parser.parts).split())
        if text and text not in text_parts:
            text_parts.append(text)
    text = " ".join(text_parts)
    # The per-market capture route provides the scope. Prefer the shared item link across overlapping feeds.
    post_id = fields.get("link") or fields.get("guid") or fields.get("id")
    if not post_id:
        post_id = sha256((published.isoformat() + "\n" + text).encode("utf-8")).hexdigest()
    return _post(platform, response, post_id, fields.get("author", fields.get("creator", "")), published, text)
