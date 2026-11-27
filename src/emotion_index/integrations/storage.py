"""Fixture loader and atomic file publisher inside .runtime/."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from emotion_index.domain.models import ObservationWindow, SocialPost, parse_utc_timestamp


def load_snapshot_fixture(path: Path) -> tuple[ObservationWindow, tuple[SocialPost, ...]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    window = ObservationWindow(
        snapshot_id=str(raw["snapshot_id"]),
        market=str(raw["market"]),
        trading_date=str(raw["trading_date"]),
        session=str(raw["session"]),
        window_start=parse_utc_timestamp(str(raw["window_start"]), "window_start"),
        cutoff_at=parse_utc_timestamp(str(raw["cutoff_at"]), "cutoff_at"),
        allowed_platforms=tuple(str(p) for p in raw["allowed_platforms"]),
    )
    posts: list[SocialPost] = []
    for item in raw.get("posts", []):
        posts.append(
            SocialPost(
                platform=str(item["platform"]),
                post_id=str(item["post_id"]),
                author_id=str(item["author_id"]),
                published_at=parse_utc_timestamp(str(item["published_at"]), "published_at"),
                available_at=parse_utc_timestamp(str(item["available_at"]), "available_at"),
                tickers=tuple(str(t) for t in item.get("tickers", [])),
                text=str(item["text"]),
                likes=int(item.get("likes", 0)),
                replies=int(item.get("replies", 0)),
                reposts=int(item.get("reposts", 0)),
            )
        )
    return window, tuple(posts)


def atomic_publish_html(target_path: Path, html_content: str) -> Path:
    encoded = html_content.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        existing_digest = hashlib.sha256(target_path.read_bytes()).hexdigest()
        if existing_digest == digest:
            return target_path
        raise FileExistsError(
            f"Artifact identity conflict at {target_path}: existing SHA-256 {existing_digest} != new {digest}"
        )

    tmp_path = target_path.with_name(f".{target_path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_bytes(encoded)
        os.replace(tmp_path, target_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return target_path
