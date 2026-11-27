"""CLI entrypoint for emotion-index."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from emotion_index.app.scoring import DEFAULT_DAILY_LIMIT, score_market
from emotion_index.domain.market import Market
from emotion_index.domain.models import parse_utc_timestamp
from emotion_index.integrations.capture import capture_market
from emotion_index.integrations.storage import atomic_publish_html, load_snapshot_fixture
from emotion_index.reporting.html_report import render_html_report
from emotion_index.research.summary import build_research_projection
from emotion_index.sentiment.scorer import compute_emotion_index
from emotion_index.sources.filter import filter_point_in_time_posts


def _timestamp(value: str) -> datetime:
    try:
        return parse_utc_timestamp(value, "timestamp")
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def run_smoke(fixture_path: Path, output_dir: Path) -> Path:
    window, posts = load_snapshot_fixture(fixture_path)
    audit = filter_point_in_time_posts(window, posts)
    snapshot = compute_emotion_index(window, audit)
    projection = build_research_projection(snapshot)
    html_report = render_html_report(projection)
    filename = f"EMOTION_{window.market}_{window.trading_date}_{window.session}.html"
    return atomic_publish_html(output_dir / filename, html_report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="emotion-index", description="Social media stock market emotion index CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    smoke_parser = subparsers.add_parser("smoke", help="Run offline synthetic smoke verification")
    smoke_parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("configs/smoke_demo.json"),
        help="Path to synthetic snapshot fixture JSON",
    )
    smoke_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".runtime/artifacts/smoke"),
        help="Target directory for smoke HTML output",
    )
    capture_parser = subparsers.add_parser("capture", help="Freeze public source responses for one market")
    capture_parser.add_argument("--market", choices=[market.value for market in Market], required=True)
    score_parser = subparsers.add_parser(
        "score", help="Score one market's frozen social posts as JSON",
        epilog="Reads DEEPSEEK_API_KEY and optional EMOTION_INDEX_DEEPSEEK_HOLIDAYS (comma-separated ISO dates).",
    )
    score_parser.add_argument("--market", choices=[market.value for market in Market], required=True)
    score_parser.add_argument("--allow-peak", action="store_true", help="Allow DeepSeek requests during peak hours")
    score_parser.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT,
                              help="Maximum selected posts per day and market (default: %(default)s)")
    score_parser.add_argument("--capture-date", type=date.fromisoformat,
                              help="UTC capture date (default: latest captured date for this market)")
    score_parser.add_argument("--cutoff-at", type=_timestamp,
                              help="Timezone-aware decision cutoff (default: latest frozen observation)")
    score_parser.add_argument("--window-start", type=_timestamp,
                              help="Timezone-aware publication window start (default: 24 hours before cutoff)")

    args = parser.parse_args(argv)
    if args.command == "smoke":
        run_smoke(args.fixture, args.output_dir)
        return 0
    if args.command == "capture":
        receipt = capture_market(Market(args.market))
        print(receipt)
        return 0
    if args.command == "score":
        try:
            result = score_market(Market(args.market), capture_date=args.capture_date, cutoff_at=args.cutoff_at,
                                  window_start=args.window_start, daily_limit=args.daily_limit,
                                  allow_peak=args.allow_peak)
        except (OSError, ValueError) as error:
            score_parser.exit(2, f"{score_parser.prog}: {error}\n")
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
