"""Self-contained HTML report generator for Emotion Index snapshots."""

from __future__ import annotations

import html
import json

from emotion_index.domain.models import ResearchReportProjection


def render_html_report(projection: ResearchReportProjection) -> str:
    snap = projection.snapshot
    win = snap.window

    audit_payload = {
        "snapshot_id": win.snapshot_id,
        "market": win.market,
        "trading_date": win.trading_date,
        "session": win.session,
        "window_start": win.window_start.isoformat(),
        "cutoff_at": win.cutoff_at.isoformat(),
        "model_version": snap.model_version,
        "eligible_post_count": snap.eligible_post_count,
        "late_unavailable_count": snap.late_unavailable_count,
        "out_of_window_count": snap.out_of_window_count,
        "net_sentiment": snap.net_sentiment,
        "emotion_index": snap.emotion_index,
        "regime": snap.regime.value,
        "tickers": [
            {
                "ticker": t.ticker,
                "mention_count": t.mention_count,
                "net_sentiment": t.net_sentiment,
                "emotion_index": t.emotion_index,
            }
            for t in projection.ticker_summaries
        ],
    }
    audit_json = html.escape(json.dumps(audit_payload, ensure_ascii=False, indent=2))

    ticker_rows = "".join(
        f"<tr><td>{html.escape(t.ticker)}</td><td>{t.mention_count}</td>"
        f"<td>{t.net_sentiment:+.4f}</td><td>{t.emotion_index:.2f}</td></tr>"
        for t in projection.ticker_summaries
    )
    platform_rows = "".join(
        f"<tr><td>{html.escape(p.platform)}</td><td>{p.post_count}</td>"
        f"<td>{p.net_sentiment:+.4f}</td><td>{p.emotion_index:.2f}</td></tr>"
        for p in projection.platform_summaries
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Emotion Index Report · {html.escape(win.market)} {html.escape(win.trading_date)}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; margin: 2rem auto; max-width: 920px; color: #1f2937; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 1.25rem; margin-bottom: 1.25rem; background: #fff; }}
    .index-val {{ font-size: 2.2rem; font-weight: 700; color: #111827; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 0.75rem; }}
    th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #e5e7eb; }}
    th {{ background: #f9fafb; }}
    pre {{ background: #f3f4f6; padding: 0.75rem; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; }}
  </style>
</head>
<body>
  <h1>社交媒体股市情绪指数报告（Emotion Index）</h1>
  <div class="card">
    <div><strong>快照身份：</strong>{html.escape(win.snapshot_id)}（市场：{html.escape(win.market)} · 交易日：{html.escape(win.trading_date)} · 时段：{html.escape(win.session)}）</div>
    <div><strong>观察窗口：</strong>{html.escape(win.window_start.isoformat())} 至 {html.escape(win.cutoff_at.isoformat())}</div>
    <p class="index-val">{snap.emotion_index:.2f} / 100 · {html.escape(snap.regime.value)}</p>
    <div>净情绪极性：{snap.net_sentiment:+.4f} ｜ 有效入池帖子：{snap.eligible_post_count} ｜ 晚于截止剔除（防未来泄漏）：{snap.late_unavailable_count} ｜ 窗口外剔除：{snap.out_of_window_count}</div>
  </div>
  <div class="card">
    <h2>按标的（Ticker）情绪分布</h2>
    <table>
      <thead><tr><th>标的</th><th>提及帖数</th><th>净情绪</th><th>情绪指数</th></tr></thead>
      <tbody>{ticker_rows}</tbody>
    </table>
  </div>
  <div class="card">
    <h2>按来源平台情绪分布</h2>
    <table>
      <thead><tr><th>平台</th><th>有效帖数</th><th>净情绪</th><th>情绪指数</th></tr></thead>
      <tbody>{platform_rows}</tbody>
    </table>
  </div>
  <div class="card">
    <h2>内嵌审计收据（JSON）</h2>
    <pre id="emotion-index-audit">{audit_json}</pre>
  </div>
</body>
</html>
"""
