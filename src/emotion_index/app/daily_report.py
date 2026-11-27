"""Build and freeze one market's daily report from captured inputs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from emotion_index.app.scoring import DEFAULT_DAILY_LIMIT, collect_market_scores
from emotion_index.domain.ledger import LedgerEntry
from emotion_index.domain.market import Market, Sector, VolumeFlag
from emotion_index.domain.models import parse_utc_timestamp
from emotion_index.integrations.capture import load_capture_receipts, utc_now
from emotion_index.integrations.file_lock import file_lock
from emotion_index.integrations.frozen_anchors import load_market_anchors
from emotion_index.integrations.frozen_posts import capture_dates, latest_capture_date
from emotion_index.integrations.gdrive_sync import prune_raw_snapshots, sync_to_gdrive
from emotion_index.integrations.ledger import EmotionLedger
from emotion_index.integrations.notifier import notify_report
from emotion_index.integrations.storage import (
    atomic_publish_bytes, atomic_publish_html, read_optional_bytes, remove_if_present,
)
from emotion_index.reporting.html_report import daily_summary, render_daily_html
from emotion_index.research.market_anchor import build_market_anchors
from emotion_index.research.three_track import build_market_snapshot
from emotion_index.sentiment.market import aggregate_market_text


@dataclass(frozen=True)
class DailyReport:
    html_path: Path
    summary_path: Path
    html: str
    summary: dict


def _build_summary(
    runtime: Path, market: Market, day: date, ledger: EmotionLedger,
    cutoff_at: datetime | None, window_start: datetime | None, daily_limit: int, allow_peak: bool,
) -> dict:
    now = utc_now()
    raw_market = runtime / "data" / "raw" / market.value
    receipts = load_capture_receipts(raw_market, market, day)
    if cutoff_at is None:
        cutoff_at = max((receipt.finished_at for receipt in receipts if receipt.finished_at <= now), default=None)

    series, cnn, latest_anchor, anchor_audit = load_market_anchors(raw_market, market, day, cutoff_at or now)
    scored = collect_market_scores(
        market, runtime, capture_date=day, cutoff_at=cutoff_at, window_start=window_start,
        daily_limit=daily_limit, allow_peak=allow_peak, latest_observation=latest_anchor, session="close",
    )
    cutoff_at = scored.audit.window.cutoff_at
    anchors = build_market_anchors(market, day, cutoff_at, series, cnn)
    history = ledger.read_history(before=day, cutoff_at=cutoff_at)[-20:]
    observations = tuple(observation for entry in history for observation in entry.volume_observations())
    scoring_audit = scored.document["audit"]
    volume_observed = any(
        parse_utc_timestamp(source["first_usable_at"], "first_usable_at") <= cutoff_at
        for source in scoring_audit["sources"].values() if "first_usable_at" in source
    )
    text = aggregate_market_text(scored.audit, scored.sentiments, scored.weights)
    snapshot = build_market_snapshot(text, observations, anchors, volume_observed=volume_observed)
    capture_flags = tuple(sorted({
        flag for receipt in receipts if receipt.finished_at <= cutoff_at for flag in receipt.flags
    }))
    audit_json = json.dumps({"scoring": scoring_audit, "anchor": anchor_audit}, allow_nan=False)
    return daily_summary(snapshot, history, audit_json, capture_flags)


def _ledger_entry(summary: dict) -> LedgerEntry:
    counts = ()
    if summary["volume_flag"] != VolumeFlag.NO_SOURCE_DATA.value:
        counts = ((Sector.BROAD_MARKET, summary["post_count"]),) + tuple(
            (sector, summary["sectors"][sector.value]["post_count"])
            for sector in Sector if sector is not Sector.BROAD_MARKET
        )
    anchor = summary["market_anchor"]
    return LedgerEntry(
        Market(summary["market"]), date.fromisoformat(summary["trading_date"]),
        parse_utc_timestamp(summary["cutoff_at"], "cutoff_at"), counts, summary["text_emotion_index"],
        summary["volume_zscore_20d"], anchor["anchor_score"] if anchor else None, summary["composite_score"],
    )


def report_market(
    market: Market, runtime: Path = Path(".runtime"), *, capture_date: date | None = None,
    cutoff_at: datetime | None = None, window_start: datetime | None = None,
    daily_limit: int = DEFAULT_DAILY_LIMIT, allow_peak: bool = False,
) -> DailyReport:
    raw_market = runtime / "data" / "raw" / market.value
    day = capture_date or latest_capture_date(raw_market)
    if cutoff_at is not None:
        if cutoff_at.utcoffset() is None or cutoff_at.astimezone(timezone.utc).date() < day:
            raise ValueError("--cutoff-at 必须带时区，且不得早于所选 UTC 采集日。")
    output = runtime / "artifacts" / "daily"
    html_path = output / f"EMOTION_{market.value}_{day}_close.html"
    summary_path = output / f"emotion_summary_{market.value}_{day}.json"
    pending = runtime / "tmp" / "daily" / summary_path.name
    ledger = EmotionLedger(market, runtime)
    with file_lock(runtime / "tmp" / f"report-{market.value}.lock"):
        content = read_optional_bytes(summary_path)
        if content is None:
            content = read_optional_bytes(pending)
        if content is None:
            if day not in capture_dates(raw_market):
                raise ValueError(f"{market.value} {day} 暂无冻结采集记录。")
            summary = _build_summary(runtime, market, day, ledger, cutoff_at, window_start, daily_limit, allow_peak)
            content = (json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
            # Freeze the exact computation across interruptions before making public files visible.
            atomic_publish_bytes(pending, content)
        else:
            summary = json.loads(content)
            if (summary["schema_version"], summary["market"], summary["trading_date"], summary["session"]) != (
                2, market.value, day.isoformat(), "close",
            ):
                raise ValueError("日报快照身份不匹配。")
            for name, requested in (("cutoff_at", cutoff_at), ("window_start", window_start)):
                if requested is not None and parse_utc_timestamp(summary[name], name) != requested:
                    raise ValueError(f"日报已冻结，{name} 与现有快照冲突。")
        html = render_daily_html(content.decode("utf-8"))
        atomic_publish_html(html_path, html)
        ledger.upsert(_ledger_entry(summary))
        # Consumers use the immutable summary as the publication marker.
        atomic_publish_bytes(summary_path, content)
        remove_if_present(pending)
    return DailyReport(html_path, summary_path, html, summary)


def deliver_report(
    report: DailyReport, runtime: Path = Path(".runtime"), *, notify: bool = False,
    sync: bool = False, dry_run: bool = False, prune_raw: bool = False,
) -> None:
    summary = report.summary
    failures = []
    if notify and not dry_run:
        flags = list(summary["status_flags"]) + list(summary["divergence_flags"])
        for sector in summary["sectors"].values():
            flags.extend(sector["divergence_flags"])
        subject = f'{summary["market"]} 股市情绪日报 · {summary["trading_date"]}'
        score = summary["composite_score"]
        headline = f"大盘合成总分 {score:.2f} / 100" if score is not None else "大盘合成总分缺少可用数据"
        body = "\n".join((subject, headline, f'有效帖子 {summary["post_count"]} 条', *sorted(set(flags))))
        try:
            notify_report(subject, body, report.html, report.html_path.name, tuple(flags))
        except OSError as error:
            failures.append(str(error))
    if sync:
        try:
            sync_to_gdrive(runtime, dry_run=dry_run)
            if prune_raw and not dry_run:
                prune_raw_snapshots(runtime, today=utc_now().date())
        except (OSError, ValueError) as error:
            failures.append(str(error))
    if failures:
        raise OSError("；".join(failures))
