"""Read market-labeled Xiaohongshu JSON exports from the local Inbox."""

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from emotion_index.domain.market import Market, MarketPost, TextFlag
from emotion_index.domain.models import SocialPost, parse_utc_timestamp
from emotion_index.integrations.providers.http import MAX_BODY_BYTES, Response


def parse_posts(document: dict, market: Market, fetched_at: datetime) -> tuple[MarketPost, ...]:
    """Validate the export and use the actual import time for post visibility."""
    if document["market"] != market.value or not isinstance(document["posts"], list):
        raise ValueError("invalid XHS market or posts")
    posts = []
    identities = set()
    for row in document["posts"]:
        if row.get("market", market.value) != market.value:
            raise ValueError("XHS post market differs from the export")
        post_id, text = row["post_id"], row["text"]
        author_id = row.get("author_id", "")
        tickers = row.get("tickers", [])
        if (not isinstance(post_id, str) or not isinstance(text, str) or not text.strip()
                or not isinstance(author_id, str) or not isinstance(tickers, list)
                or any(not isinstance(ticker, str) or not ticker.strip() for ticker in tickers)):
            raise ValueError("invalid XHS post fields")
        if post_id in identities:
            raise ValueError("duplicate XHS post")
        identities.add(post_id)
        published = parse_utc_timestamp(row["published_at"], "published_at")
        claimed = (parse_utc_timestamp(row["available_at"], "available_at")
                   if "available_at" in row else fetched_at)
        if not published <= claimed <= fetched_at:
            raise ValueError("invalid XHS point-in-time metadata")
        engagement = [row.get(field, 0) for field in ("likes", "replies", "reposts")]
        if any(type(count) is not int for count in engagement):
            raise ValueError("invalid XHS engagement fields")
        posts.append(MarketPost(
            SocialPost("xhs", post_id, author_id, published, fetched_at, tuple(tickers), text, *engagement), market,
        ))
    return tuple(posts)


def collect(inbox: Path, market: Market, now: Callable[[], datetime]) -> tuple[Response, ...]:
    results = []
    try:
        paths = sorted(path for path in inbox.iterdir() if path.suffix.lower() == ".json")
    except FileNotFoundError:
        paths = []
    except OSError:
        return (Response("inbox", "XHS_INBOX_UNREADABLE"),)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        source = path.name
        try:
            with path.open("rb") as stream:
                body = stream.read(MAX_BODY_BYTES + 1)
            fetched_at = now()
        except OSError:
            results.append(Response(source, "XHS_FILE_UNREADABLE"))
            continue
        if len(body) > MAX_BODY_BYTES:
            results.append(Response(source, "RESPONSE_TOO_LARGE", fetched_at=fetched_at))
            continue
        try:
            document = json.loads(body)
            export_market = Market(document["market"])
        except (ValueError, TypeError, KeyError, RecursionError):
            results.append(Response(source, "XHS_INVALID_ENVELOPE", fetched_at=fetched_at))
            continue
        if export_market is not market:
            continue
        try:
            posts = parse_posts(document, market, fetched_at)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            reason = "XHS_INVALID_POST_OR_TIMESTAMP"
        else:
            reason = None if posts else TextFlag.XHS_NOT_PROVIDED.value
        results.append(Response(source, reason, body, fetched_at))
    return tuple(results) or (Response("inbox", TextFlag.XHS_NOT_PROVIDED.value),)
