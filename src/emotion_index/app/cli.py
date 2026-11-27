"""CLI entrypoint for emotion-index."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from emotion_index.app.daily_report import deliver_report, report_market
from emotion_index.app.scoring import DEFAULT_DAILY_LIMIT, score_market
from emotion_index.domain.market import Market
from emotion_index.domain.models import parse_utc_timestamp
from emotion_index.integrations.capture import capture_market, scheduled_session_date
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


def _scoring_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--allow-peak", action="store_true", help="Allow DeepSeek requests during peak hours")
    parser.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT,
                        help="Maximum selected posts per day and market (default: %(default)s)")
    parser.add_argument("--capture-date", type=date.fromisoformat,
                        help="UTC capture date (default: latest captured date for this market)")
    parser.add_argument("--cutoff-at", type=_timestamp,
                        help="Timezone-aware decision cutoff (default: latest frozen observation)")
    parser.add_argument("--window-start", type=_timestamp,
                        help="Timezone-aware publication window start (default: 24 hours before cutoff)")


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
    capture_parser.add_argument("--current-session", action="store_true", help="Skip weekends and configured holidays")
    score_parser = subparsers.add_parser(
        "score", help="Score one market's frozen social posts as JSON",
        epilog="Reads DEEPSEEK_API_KEY and optional EMOTION_INDEX_DEEPSEEK_HOLIDAYS (comma-separated ISO dates).",
    )
    score_parser.add_argument("--market", choices=[market.value for market in Market], required=True)
    _scoring_options(score_parser)
    report_parser = subparsers.add_parser("report", help="Freeze the HTML daily report and read-only JSON summary")
    report_parser.add_argument("--market", choices=[market.value for market in Market], required=True)
    _scoring_options(report_parser)
    report_parser.add_argument("--current-session", action="store_true",
                               help="Report today's capture only and skip configured market holidays")
    report_parser.add_argument("--notify", action="store_true", help="Use the configured SMTP and Bark channels")
    report_parser.add_argument("--sync", action="store_true", help="Archive runtime data with rclone copy")
    report_parser.add_argument("--dry-run", action="store_true", help="Preview rclone transfers and skip notifications")
    report_parser.add_argument("--prune-raw", action="store_true", help="Keep 60 days of raw data after successful sync")

    args = parser.parse_args(argv)
    if args.command == "smoke":
        run_smoke(args.fixture, args.output_dir)
        return 0
    if args.command == "capture":
        market = Market(args.market)
        try:
            if args.current_session and scheduled_session_date(market) is None:
                return 0
            receipt = capture_market(market)
        except (OSError, ValueError) as error:
            capture_parser.exit(2, f"{capture_parser.prog}: {error}\n")
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
    if args.command == "report":
        if (args.dry_run or args.prune_raw) and not args.sync:
            report_parser.error("--dry-run and --prune-raw require --sync")
        if args.current_session and args.capture_date is not None:
            report_parser.error("--current-session and --capture-date are mutually exclusive")
        market = Market(args.market)
        try:
            day = scheduled_session_date(market) if args.current_session else args.capture_date
            if args.current_session and day is None:
                return 0
            report = report_market(
                market, capture_date=day, cutoff_at=args.cutoff_at, window_start=args.window_start,
                daily_limit=args.daily_limit, allow_peak=args.allow_peak,
            )
            print(report.html_path)
            print(report.summary_path)
            deliver_report(report, notify=args.notify, sync=args.sync, dry_run=args.dry_run, prune_raw=args.prune_raw)
        except (OSError, ValueError) as error:
            report_parser.exit(2, f"{report_parser.prog}: {error}\n")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
