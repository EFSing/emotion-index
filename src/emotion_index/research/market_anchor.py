"""Normalize point-in-time market inputs to the 0–100 sentiment scale."""

from datetime import date, datetime
from statistics import fmean

from emotion_index.domain.anchor import MarketSeries
from emotion_index.domain.market import Market, MarketAnchor, Sector

US_STYLES = {
    Sector.TECH_GROWTH: ("QQQ",), Sector.CYCLICAL_FINANCE: ("XLF",),
    Sector.DEFENSIVE_DIVIDEND: ("XLU",), Sector.HIGH_BETA_THEME: ("IWM",),
}
CN_STYLES = {
    Sector.TECH_GROWTH: ("sz399006", "sh000688"), Sector.CYCLICAL_FINANCE: ("sh512800",),
    Sector.DEFENSIVE_DIVIDEND: ("sh510880",), Sector.HIGH_BETA_THEME: ("sh563300",),
}


def _clamp(score: float) -> float:
    return min(100.0, max(0.0, score))


def _changes(series: MarketSeries, market: Market) -> tuple[float, datetime]:
    today, previous = series.bars[-1], series.bars[-2]
    change = today.close / previous.close - 1
    baseline = [bar.volume for bar in series.bars[-21:-1]]
    if market is Market.CN and today.volume is not None and len(baseline) == 20 and None not in baseline:
        typical = fmean(baseline)
        if typical > 0:
            change *= min(1.5, max(0.5, today.volume / typical))
    return change, series.available_at


def build_market_anchors(
    market: Market, trading_date: date, cutoff_at: datetime, series: tuple[MarketSeries, ...],
    cnn: MarketAnchor | None = None,
) -> tuple[MarketAnchor, ...]:
    eligible = {}
    for item in series:
        if item.available_at <= cutoff_at:
            bars = tuple(bar for bar in item.bars if bar.trading_date <= trading_date)
            if bars and bars[-1].trading_date == trading_date:
                eligible[item.symbol] = MarketSeries(item.symbol, item.available_at, bars)

    changes = {
        symbol: _changes(item, market) for symbol, item in eligible.items()
        if symbol != "^VIX" and len(item.bars) >= 2
    }
    broad_symbols = ("SPY",) if market is Market.US else ("sh000001", "sh000300")
    broad = [changes[symbol] for symbol in broad_symbols if symbol in changes]
    benchmark = fmean(change for change, _ in broad) if broad else None

    broad_components = []
    if benchmark is not None:
        broad_components.append((_clamp(50 + 1000 * benchmark), max(stamp for _, stamp in broad)))
    if market is Market.US:
        if (cnn is not None and cnn.market is market and cnn.trading_date == trading_date
                and cnn.available_at <= cutoff_at):
            broad_components.append((cnn.anchor_score, cnn.available_at))
        vix = eligible.get("^VIX")
        if vix is not None:
            close = vix.bars[-1].close
            change_5d = close / vix.bars[-6].close - 1 if len(vix.bars) >= 6 else 0
            broad_components.append((_clamp(50 - 2 * (close - 20) - 100 * change_5d), vix.available_at))

    anchors = []
    relative_styles = []
    styles = US_STYLES if market is Market.US else CN_STYLES
    for sector, symbols in styles.items():
        observations = [changes[symbol] for symbol in symbols if symbol in changes]
        if not observations:
            continue
        change = fmean(value for value, _ in observations)
        stamp = max(at for _, at in observations)
        if benchmark is not None:
            relative = change - benchmark
            stamp = max(stamp, *(at for _, at in broad))
            risk_direction = -relative if sector is Sector.DEFENSIVE_DIVIDEND else relative
            relative_styles.append((risk_direction, stamp))
            change = (change + relative) / 2
        anchors.append(MarketAnchor(market, trading_date, stamp, _clamp(50 + 1000 * change), sector))

    if relative_styles:
        relative = fmean(value for value, _ in relative_styles)
        broad_components.append((_clamp(50 + 1000 * relative), max(at for _, at in relative_styles)))
    if broad_components:
        anchors.insert(0, MarketAnchor(
            market, trading_date, max(stamp for _, stamp in broad_components),
            fmean(score for score, _ in broad_components),
        ))
    return tuple(anchors)
