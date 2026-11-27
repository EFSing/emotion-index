"""Incremental Google Drive backups and date-based local raw retention."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

from emotion_index.domain.market import Market

SYNC_DIRECTORIES = (
    ("data/raw", "raw"),
    ("data/ledger", "ledger"),
    ("artifacts/daily", "artifacts/daily"),
    ("cache", "cache"),
)


def sync_to_gdrive(
    runtime: Path = Path(".runtime"), *, remote: str | None = None, dry_run: bool = False,
) -> None:
    name = remote if remote is not None else os.environ.get("EMOTION_INDEX_RCLONE_REMOTE", "gdrive")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. -]*", name):
        raise ValueError("EMOTION_INDEX_RCLONE_REMOTE must be an rclone remote name without a path")
    failures = []
    for local, destination in SYNC_DIRECTORIES:
        source = runtime / local
        if not source.is_dir():
            continue
        command = ["rclone", "copy", str(source), f"{name}:EmotionIndex/{destination}", "--exclude", ".*.tmp"]
        if local == "cache":
            command += ["--exclude", "*.sqlite-{journal,wal,shm}"]
        if dry_run:
            command.append("--dry-run")
        try:
            subprocess.run(command, check=True, timeout=1800)
        except (OSError, subprocess.SubprocessError):
            failures.append(destination)
    if failures:
        raise OSError("rclone 归档失败：" + "、".join(failures))


def _older_than(value: str, earliest: date) -> bool:
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return False
    return day.isoformat() == value and day < earliest


def _local_directory(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink() and not path.is_junction()


def prune_raw_snapshots(runtime: Path, *, today: date, keep_days: int = 60) -> int:
    """Keep today and the previous keep_days - 1 UTC dates, touching only known raw paths."""
    if type(keep_days) is not int or keep_days <= 0:
        raise ValueError("keep_days must be a positive integer")
    earliest = today - timedelta(days=keep_days - 1)
    data = runtime / "data"
    raw = data / "raw"
    removed = 0
    if not all(_local_directory(path) for path in (runtime, data, raw)):
        return removed
    for market in Market:
        market_dir = raw / market.value
        if not _local_directory(market_dir):
            continue
        for platform in market_dir.iterdir():
            if not _local_directory(platform):
                continue
            if platform.name == "_receipts":
                for day_dir in platform.iterdir():
                    if not _local_directory(day_dir) or not _older_than(day_dir.name, earliest):
                        continue
                    for receipt in day_dir.glob("*.json"):
                        if re.fullmatch(r"[0-9a-f]{32}", receipt.stem) and receipt.is_file() and not receipt.is_symlink():
                            receipt.unlink()
                            removed += 1
                    if not any(day_dir.iterdir()):
                        day_dir.rmdir()
            else:
                for snapshot in platform.glob("*.json"):
                    if _older_than(snapshot.stem, earliest) and snapshot.is_file() and not snapshot.is_symlink():
                        snapshot.unlink()
                        removed += 1
    return removed
