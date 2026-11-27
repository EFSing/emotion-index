"""Capture providers into immutable daily snapshots and per-run receipts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping
from uuid import uuid4

from emotion_index.domain.market import Market
from emotion_index.domain.models import parse_utc_timestamp
from emotion_index.integrations.providers.apewisdom import feeds as apewisdom_feeds
from emotion_index.integrations.providers.bluesky import feeds as bluesky_feeds
from emotion_index.integrations.providers.cn_anchor import feeds as cn_anchor_feeds
from emotion_index.integrations.providers.http import Feed, Response
from emotion_index.integrations.providers.rss_feeds import feeds as rss_feeds
from emotion_index.integrations.providers.stocktwits import feeds as stocktwits_feeds
from emotion_index.integrations.providers.us_anchor import feeds as us_anchor_feeds
from emotion_index.integrations.providers.xhs_inbox import collect as collect_xhs
from emotion_index.integrations.storage import atomic_publish_bytes


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _encode_response(response: Response) -> dict:
    observed = response.fetched_at
    timestamp = observed.isoformat() if observed is not None else None
    item = {
        "source": response.source, "reason": response.reason, "http_status": response.http_status,
        "fetched_at": timestamp, "available_at": timestamp,
    }
    if response.body is not None:
        item.update(
            sha256=hashlib.sha256(response.body).hexdigest(),
            payload_base64=base64.b64encode(response.body).decode("ascii"),
        )
    return item


def _write_json(path: Path, document: dict) -> Path:
    return atomic_publish_bytes(path, (json.dumps(document, ensure_ascii=True, indent=2) + "\n").encode("utf-8"))


def _receipt_entries(entries: list[dict]) -> list[dict]:
    return [{key: value for key, value in entry.items() if key != "payload_base64"} for entry in entries]


def _verified_payloads(document: dict) -> Iterator[Response]:
    snapshot_time = parse_utc_timestamp(document["available_at"], "available_at")
    if snapshot_time != parse_utc_timestamp(document["fetched_at"], "fetched_at"):
        raise ValueError("inconsistent snapshot timestamps")
    entries = document["responses"]
    if not isinstance(entries, list) or not any("payload_base64" in entry for entry in entries):
        raise ValueError("snapshot must contain a raw response")
    for entry in entries:
        if "payload_base64" in entry:
            available = parse_utc_timestamp(entry["available_at"], "available_at")
            fetched = parse_utc_timestamp(entry["fetched_at"], "fetched_at")
            if not fetched == available <= snapshot_time:
                raise ValueError("inconsistent response timestamps")
            body = base64.b64decode(entry["payload_base64"], validate=True)
            if hashlib.sha256(body).hexdigest() != entry["sha256"]:
                raise ValueError("frozen response SHA-256 mismatch")
            yield Response(entry["source"], entry["reason"], body, fetched, entry["http_status"])


def load_available_responses(path: Path, cutoff_at: datetime) -> tuple[Response, ...]:
    """Verify raw bytes and return the successful observations available by the replay cutoff."""
    if cutoff_at.utcoffset() is None:
        raise ValueError("cutoff_at must be timezone-aware")
    document = json.loads(path.read_bytes())
    return tuple(response for response in _verified_payloads(document)
                 if response.reason is None and response.fetched_at <= cutoff_at)


def _existing_snapshot(target: Path, identity: dict) -> list[dict] | None:
    try:
        document = json.loads(target.read_bytes())
    except FileNotFoundError:
        return None
    if any(document[field] != value for field, value in identity.items()):
        raise ValueError("frozen snapshot identity mismatch")
    tuple(_verified_payloads(document))
    return _receipt_entries(document["responses"])


def capture_market(
    market: Market, runtime: Path = Path(".runtime"), *, env: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> Path:
    config = os.environ if env is None else env

    def now() -> datetime:
        timestamp = clock()
        if timestamp.utcoffset() is None:
            raise ValueError("capture clock must be timezone-aware")
        return timestamp.astimezone(timezone.utc)

    started_at = now()
    capture_date = started_at.date().isoformat()
    output = runtime / "data" / "raw" / market.value
    feeds = rss_feeds(market, config)
    if market is Market.US:
        feeds = (*apewisdom_feeds(), *bluesky_feeds(), *stocktwits_feeds(), *feeds, *us_anchor_feeds(config))
    else:
        feeds = (*feeds, *cn_anchor_feeds())
    by_platform: dict[str, list[Feed]] = {}
    for feed in feeds:
        by_platform.setdefault(feed.platform, []).append(feed)
    by_platform["xhs"] = []

    platforms = []
    for platform, inputs in by_platform.items():
        relative = Path(platform) / f"{capture_date}.json"
        target = output / relative
        identity = {"schema_version": 1, "market": market.value, "platform": platform, "capture_date": capture_date}
        summary = {"platform": platform, "snapshot": None, "responses": []}
        platforms.append(summary)
        try:
            existing = _existing_snapshot(target, identity)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            summary.update(status="FAILED", reason="SNAPSHOT_UNREADABLE_OR_INVALID")
            continue
        if existing is not None:
            summary.update(status="REUSED", snapshot=relative.as_posix(), responses=existing)
            continue

        responses = (collect_xhs(runtime / "inbox" / "xhs", market, now) if platform == "xhs"
                     else tuple(feed.collect(now) for feed in inputs))
        entries = [_encode_response(response) for response in responses]
        summary["responses"] = _receipt_entries(entries)
        successes = sum(response.reason is None for response in responses)
        observed = [response.fetched_at for response in responses if response.body is not None]
        if successes and observed:
            available_at = max(observed).isoformat()
            try:
                _write_json(target, {
                    **identity, "fetched_at": available_at, "available_at": available_at, "responses": entries,
                })
            except FileExistsError:
                summary.update(status="FAILED", reason="SNAPSHOT_IDENTITY_CONFLICT")
                continue
            except OSError:
                summary.update(status="FAILED", reason="SNAPSHOT_WRITE_FAILED")
                continue
            summary["snapshot"] = relative.as_posix()
        summary["status"] = ("UNAVAILABLE" if not successes else
                             "CAPTURED" if successes == len(responses) else "PARTIAL")

    return _write_json(output / "_receipts" / capture_date / f"{uuid4().hex}.json", {
        "schema_version": 1, "market": market.value, "capture_date": capture_date,
        "started_at": started_at.isoformat(), "finished_at": now().isoformat(), "platforms": platforms,
    })
