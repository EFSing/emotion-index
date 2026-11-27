"""Persistent content scores and daily DeepSeek submission accounting."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import asdict
from datetime import date
from hashlib import sha256
from pathlib import Path

from emotion_index.domain.market import Market
from emotion_index.integrations.deepseek_client import MODEL_NAME, PROMPT_VERSION, DeepSeekScore

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_scores (
    content_hash TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_name TEXT NOT NULL,
    score_json TEXT NOT NULL,
    PRIMARY KEY (content_hash, prompt_version, model_name)
);
CREATE TABLE IF NOT EXISTS llm_submissions (
    request_day TEXT NOT NULL,
    market TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_name TEXT NOT NULL,
    PRIMARY KEY (request_day, market, content_hash, prompt_version, model_name),
    UNIQUE (request_day, content_hash, prompt_version, model_name)
);
"""


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def content_hash(platform: str, normalized_text: str) -> str:
    return sha256((platform + normalized_text).encode("utf-8")).hexdigest()


class ScoreCache:
    def __init__(self, path: Path = Path(".runtime/cache/llm_scores.sqlite")) -> None:
        self.path = path

    def __enter__(self) -> ScoreCache:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        try:
            self.connection.executescript(SCHEMA)
        except sqlite3.Error:
            self.connection.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self.connection.close()

    def get(self, key: str) -> DeepSeekScore | None:
        row = self.connection.execute(
            "SELECT score_json FROM llm_scores WHERE content_hash=? AND prompt_version=? AND model_name=?",
            (key, PROMPT_VERSION, MODEL_NAME),
        ).fetchone()
        return DeepSeekScore.from_json(json.loads(row[0])) if row else None

    def store(self, scores: dict[str, DeepSeekScore]) -> None:
        with self.connection:
            self.connection.executemany(
                "INSERT INTO llm_scores VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                ((key, PROMPT_VERSION, MODEL_NAME, json.dumps(asdict(score))) for key, score in scores.items()),
            )

    def reserve(self, market: Market, day: date, keys: tuple[str, ...], daily_limit: int) -> tuple[str, ...]:
        """Claim uncached content before HTTP. A failure still consumes its submission slots."""
        accepted = []
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            used, = self.connection.execute(
                "SELECT count(*) FROM llm_submissions WHERE request_day=? AND market=?",
                (day.isoformat(), market.value),
            ).fetchone()
            remaining = max(0, daily_limit - used)
            for key in keys:
                if len(accepted) == remaining:
                    break
                if self.get(key) is not None:
                    continue
                cursor = self.connection.execute(
                    "INSERT INTO llm_submissions VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (day.isoformat(), market.value, key, PROMPT_VERSION, MODEL_NAME),
                )
                if cursor.rowcount:
                    accepted.append(key)
        return tuple(accepted)
