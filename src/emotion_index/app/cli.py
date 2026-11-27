"""CLI entrypoint for emotion-index."""

from __future__ import annotations

import argparse
from pathlib import Path

from emotion_index.domain.market import Market
from emotion_index.integrations.capture import capture_market
from emotion_index.integrations.storage import atomic_publish_html, load_snapshot_fixture
from emotion_index.reporting.html_report import render_html_report
from emotion_index.research.summary import build_research_projection
from emotion_index.sentiment.scorer import compute_emotion_index
from emotion_index.sources.filter import filter_point_in_time_posts


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

    args = parser.parse_args(argv)
    if args.command == "smoke":
        run_smoke(args.fixture, args.output_dir)
        return 0
    if args.command == "capture":
        receipt = capture_market(Market(args.market))
        print(receipt)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
