"""Self-contained HTML report generator for Emotion Index snapshots."""

from __future__ import annotations

import html
import json

from emotion_index.domain.ledger import LedgerEntry
from emotion_index.domain.market import MarketSnapshot, ThreeTrackReading
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

SECTOR_NAMES = {
    "TECH_GROWTH": "科技成长",
    "CYCLICAL_FINANCE": "顺周期与金融",
    "DEFENSIVE_DIVIDEND": "防御红利",
    "HIGH_BETA_THEME": "高弹性题材与小盘",
}
FLAG_NAMES = {
    "DEGRADED_LEXICON": "部分或全部帖子使用双语词典打分",
    "XHS_NOT_PROVIDED": "本日无可用小红书校准样本",
    "XHS_NOT_SCORED": "有小红书样本，但本日未参与打分",
    "XUEQIU_COOKIE_EXPIRED_OR_UNAUTHORIZED": "雪球 Cookie 过期或未授权",
    "SOCIAL_OVERHEATED_VS_MARKET_WEAK": "社交情绪过热，盘面偏弱",
    "SOCIAL_PANIC_VS_MARKET_RESILIENT": "社交情绪恐慌，盘面仍有韧性",
    "EXTREME_EUPHORIA_WARNING": "社交情绪极度狂热",
    "EXTREME_CAPITULATION_ALERT": "社交情绪极度恐慌",
    "WARMUP": "历史样本不足 5 日",
    "ZERO_VARIANCE": "历史声量无方差",
    "VOLUME_SPIKE": "讨论声量显著升高",
    "VOLUME_COLD": "讨论声量偏低",
    "NO_SOURCE_DATA": "缺少可用的声量观测",
}


def _number(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}" if signed else f"{value:.2f}"


def _flag(code: str) -> str:
    return f"{html.escape(FLAG_NAMES.get(code, code))} <code>{html.escape(code)}</code>"


def _track_payload(reading: ThreeTrackReading) -> dict:
    anchor = reading.market_anchor
    return {
        "text_emotion_index": reading.text.text_emotion_index,
        "euphoria_ratio": reading.text.euphoria_ratio,
        "panic_ratio": reading.text.panic_ratio,
        "volume_zscore_20d": reading.volume.volume_zscore_20d,
        "volume_flag": reading.volume.flag.value if reading.volume.flag else None,
        "volume_baseline_days": reading.volume.baseline_days,
        "market_anchor": {"anchor_score": anchor.anchor_score, "available_at": anchor.available_at.isoformat()}
                         if anchor else None,
        "composite_score": reading.composite_score,
        "divergence_flags": [flag.value for flag in reading.divergence_flags],
        "post_count": reading.text.post_count,
        "scored_post_count": reading.text.scored_post_count,
        "effective_weights": dict(reading.text.effective_weights),
        "missing_automatic_platforms": list(reading.text.missing_automatic_platforms),
        "text_flags": [flag.value for flag in reading.text.flags],
    }


def _trend_point(entry: LedgerEntry) -> dict:
    return {
        "trading_date": entry.trading_date.isoformat(),
        "text_emotion_index": entry.text_emotion_index,
        "volume_zscore_20d": entry.volume_zscore_20d,
        "anchor_score": entry.anchor_score,
        "composite_score": entry.composite_score,
    }


def daily_summary(
    snapshot: MarketSnapshot, history: tuple[LedgerEntry, ...], audit_json: str, capture_flags: tuple[str, ...],
) -> dict:
    window = snapshot.audit.window
    broad = _track_payload(snapshot.broad_market)
    audit = json.loads(audit_json)
    scoring_mode = audit["scoring"]["scoring_mode"]
    status = list(broad["text_flags"]) + list(capture_flags)
    if scoring_mode == "DEGRADED_LEXICON":
        status.insert(0, scoring_mode)
    return {
        "schema_version": 2,
        "snapshot_id": window.snapshot_id,
        "market": window.market,
        "trading_date": window.trading_date,
        "session": window.session,
        "window_start": window.window_start.isoformat(),
        "cutoff_at": window.cutoff_at.isoformat(),
        "scoring_mode": scoring_mode,
        **broad,
        "sectors": {reading.text.sector.value: _track_payload(reading) for reading in snapshot.sectors},
        "status_flags": status,
        "history": [_trend_point(entry) for entry in history],
        "audit": audit,
    }


def _trend_svg(points: list[dict], series: tuple[tuple[str, str, str], ...], low: float, high: float,
               title: str) -> str:
    left, top, width, height = 48, 16, 634, 112
    marks = []
    for value in (low, (low + high) / 2, high):
        y = top + (high - value) / (high - low) * height
        marks.append(f'<path d="M{left} {y:.1f}h{width}" stroke="#bdccd2"/>'
                     f'<text x="41" y="{y + 4:.1f}" text-anchor="end">{value:.0f}</text>')
    for key, color, _ in series:
        previous = None
        for index, point in enumerate(points):
            value = point[key]
            if value is None:
                previous = None
                continue
            x = left + index * width / max(len(points) - 1, 1)
            y = top + (high - value) / (high - low) * height
            if previous is not None:
                previous_x, previous_y = previous
                marks.append(f'<path d="M{previous_x:.1f} {previous_y:.1f}L{x:.1f} {y:.1f}" '
                             f'stroke="{color}" stroke-width="2.5"/>')
            marks.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="{color}"><title>'
                         f'{html.escape(point["trading_date"])} · {_number(value)}</title></circle>')
            previous = (x, y)
    marks.append(f'<text x="{left}" y="149">{html.escape(points[0]["trading_date"])}</text>')
    if len(points) > 1:
        marks.append(f'<text x="{left + width}" y="149" text-anchor="end">'
                     f'{html.escape(points[-1]["trading_date"])}</text>')
    legend = "".join(f'<span style="color:{color}">● {label}</span>' for _, color, label in series)
    return (f'<div class="legend">{legend}</div><svg viewBox="0 0 700 155" role="img" '
            f'aria-label="{html.escape(title)}">{"".join(marks)}</svg>')


def render_daily_html(summary_json: str) -> str:
    """Render a published summary as standalone HTML with a raw JSON audit payload."""
    summary = json.loads(summary_json)
    day = html.escape(summary["trading_date"])
    market = {"US": "美股", "CN": "A 股"}[summary["market"]]
    anchor = summary["market_anchor"]
    anchor_score = anchor["anchor_score"] if anchor else None
    cards = (
        ("社交文本极性", _number(summary["text_emotion_index"]), "0–100"),
        ("20 日讨论声量", _number(summary["volume_zscore_20d"], signed=True), "Z-Score"),
        ("客观盘面情绪锚", _number(anchor_score), "0–100"),
        ("合成总分", _number(summary["composite_score"]), "0–100"),
    )
    metrics = "".join(f'<div class="metric"><span>{name}</span><strong>{value}</strong><small>{unit}</small></div>'
                      for name, value, unit in cards)
    states = "".join(f'<li>{_flag(flag)}</li>' for flag in summary["status_flags"])
    status = f'<ul class="status">{states}</ul>' if states else ""
    warnings = [f'<li>大盘：{_flag(flag)}</li>' for flag in summary["divergence_flags"]]
    rows = []
    for sector, reading in summary["sectors"].items():
        name = SECTOR_NAMES[sector]
        sector_anchor = reading["market_anchor"]
        warnings.extend(f'<li>{name}：{_flag(flag)}</li>' for flag in reading["divergence_flags"])
        rows.append(
            f'<tr><th scope="row">{name}</th><td>{_number(reading["text_emotion_index"])}</td>'
            f'<td>{_number(reading["volume_zscore_20d"], signed=True)}</td>'
            f'<td>{_number(sector_anchor["anchor_score"] if sector_anchor else None)}</td>'
            f'<td>{_number(reading["composite_score"])}</td>'
            f'<td>{reading["scored_post_count"]} / {reading["post_count"]}</td></tr>'
        )
    warning_list = f'<ul>{"".join(warnings)}</ul>' if warnings else "<p>本日未触发背离或极端情绪警示。</p>"
    points = [*summary["history"], {
        "trading_date": summary["trading_date"], "text_emotion_index": summary["text_emotion_index"],
        "volume_zscore_20d": summary["volume_zscore_20d"], "anchor_score": anchor_score,
        "composite_score": summary["composite_score"],
    }]
    score_chart = _trend_svg(points, (
        ("text_emotion_index", "#126a68", "社交文本极性"),
        ("anchor_score", "#924810", "盘面锚"),
        ("composite_score", "#334ca0", "合成总分"),
    ), 0, 100, "大盘情绪指数历史趋势")
    zvalues = [abs(point["volume_zscore_20d"]) for point in points if point["volume_zscore_20d"] is not None]
    bound = max(3.0, *zvalues) if zvalues else 3.0
    volume_chart = _trend_svg(points, (("volume_zscore_20d", "#72489a", "20 日声量 Z-Score"),),
                              -bound, bound, "讨论声量历史趋势")
    volume_status = _flag(summary["volume_flag"]) if summary["volume_flag"] else "常态"
    ratios = [_number(value * 100) + "%" if value is not None else "—"
              for value in (summary["euphoria_ratio"], summary["panic_ratio"])]
    audit_json = summary_json.strip().replace("<", "\\u003c")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{market}情绪日报 · {day}</title>
  <style>
    body {{ margin: 0 auto; padding: 2rem 1rem; max-width: 900px; background: #f4f7f7;
            color: #162f39; font: 1rem/1.55 system-ui, sans-serif; }}
    h1, h2 {{ line-height: 1.15; }}
    h1 {{ margin-top: .3rem; }}
    section {{ padding: 1.2rem; margin-top: 1rem; background: #fff; border: 1px solid #dce6e7; border-radius: .55rem; }}
    .status {{ background: #fff1d7; border: 1px solid #efc678; border-radius: .45rem; padding: .8rem .8rem .8rem 2rem; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .7rem; }}
    .metric {{ background: #eaf2f2; padding: .8rem; border-radius: .35rem; }}
    strong {{ display: block; margin: .2rem 0; font-size: 1.85rem; }}
    code {{ overflow-wrap: anywhere; }}
    time, small {{ color: #3d5662; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 1rem; margin-top: .75rem; }}
    svg {{ width: 100%; height: auto; fill: #3d5662; font-size: 12px; }}
    svg path {{ fill: none; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; min-width: 560px; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: .55rem; border-bottom: 1px solid #dce6e7; }}
  </style>
</head>
<body>
  <header>
    <time datetime="{day}">{day}</time>
    <h1>{market}情绪日报</h1>
    {status}
    <p>观察窗口 UTC {html.escape(summary["window_start"])} 至 {html.escape(summary["cutoff_at"])}</p>
  </header>
  <section>
    <h2>大盘三轨与合成总分</h2>
    <div class="metrics">{metrics}</div>
    <p>狂热占比 {ratios[0]} · 恐慌占比 {ratios[1]} · 有效帖子 {summary["post_count"]} 条</p>
    <p>声量基线 {summary["volume_baseline_days"]} 日 · {volume_status}</p>
  </section>
  <section><h2>背离与极端情绪警示</h2>{warning_list}</section>
  <section>
    <h2>四大板块温度</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>板块</th><th>文本极性</th><th>声量 Z-Score</th><th>盘面锚</th><th>合成总分</th><th>打分 / 有效帖</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table></div>
  </section>
  <section><h2>近 20 个历史交易日及本日</h2>{score_chart}{volume_chart}</section>
  <script id="emotion-index-audit" type="application/json">{audit_json}</script>
</body>
</html>
"""
