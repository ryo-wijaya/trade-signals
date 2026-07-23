import logging
import statistics
from dataclasses import dataclass
from datetime import date

import yfinance as yf

log = logging.getLogger(__name__)

# Margin over the ~5 annual periods yfinance's free income_stmt provides —
# enough runway to find a price on/before the oldest fiscal year-end.
_HIST_PERIOD = "6y"

# Standard PEG heuristic (Peter Lynch's <1 "undervalued relative to growth"
# rule of thumb): <1 cheap, 1-2 fair, >2 expensive.
_PEG_CHEAP = 1.0
_PEG_EXPENSIVE = 2.0

# Tertile split of where the current multiple sits within its own historical
# [low, high] range — bottom third cheap, top third expensive, middle fair.
_BAND_CHEAP_POSITION = 1 / 3
_BAND_EXPENSIVE_POSITION = 2 / 3

_cache: dict[tuple[str, date], "ValuationResult"] = {}


@dataclass
class HistoricalBand:
    low: float
    high: float
    median: float
    n: int  # number of historical fiscal-year data points behind this band
    label: str  # "cheap" / "fair" / "expensive" / "unknown"


@dataclass
class ValuationResult:
    ticker: str
    trailing_pe: float | None = None
    forward_pe: float | None = None
    pe_band: HistoricalBand | None = None
    # Forward P/E judged against the SAME historical trailing-PE band — a
    # forward multiple below the band means the market expects earnings growth
    # to make today's price cheap by historical standards. Context only; not a
    # vote in the overall verdict (keeps the 3-signal verdict stable).
    forward_pe_label: str = "unknown"
    peg: float | None = None
    peg_label: str = "unknown"
    price_to_sales: float | None = None
    ps_band: HistoricalBand | None = None
    verdict: str = "insufficient data"  # "cheap" / "fair" / "expensive" / "insufficient data"
    error: str | None = None


def _peg_label(peg: float | None, cheap: float = _PEG_CHEAP, expensive: float = _PEG_EXPENSIVE) -> str:
    if peg is None or peg <= 0:
        return "unknown"
    if peg < cheap:
        return "cheap"
    if peg <= expensive:
        return "fair"
    return "expensive"


def _classify_position(
    current: float | None, low: float, high: float,
    cheap_pos: float = _BAND_CHEAP_POSITION, expensive_pos: float = _BAND_EXPENSIVE_POSITION,
) -> str:
    if current is None or high <= low:
        return "unknown"
    position = (current - low) / (high - low)
    if position < cheap_pos:
        return "cheap"
    if position > expensive_pos:
        return "expensive"
    return "fair"


def _historical_band(
    points: list[float], current: float | None,
    cheap_pos: float = _BAND_CHEAP_POSITION, expensive_pos: float = _BAND_EXPENSIVE_POSITION,
) -> HistoricalBand | None:
    """points: historical (price / per-share metric) values, one per fiscal
    year. Needs >=2 to have an actual range to compare the current multiple
    against — a single data point isn't a "usual trading range"."""
    if len(points) < 2:
        return None
    low, high = min(points), max(points)
    return HistoricalBand(
        low=low, high=high, median=statistics.median(points), n=len(points),
        label=_classify_position(current, low, high, cheap_pos, expensive_pos),
    )


def _price_on_or_before(closes, target_date) -> float | None:
    eligible = closes[closes.index <= target_date]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1])


def _overall_verdict(labels: list[str]) -> str:
    computed = [l for l in labels if l not in (None, "unknown")]
    if not computed:
        return "insufficient data"
    cheap_n = computed.count("cheap")
    expensive_n = computed.count("expensive")
    if cheap_n > expensive_n:
        return "cheap"
    if expensive_n > cheap_n:
        return "expensive"
    return "fair"


def get_valuation(ticker: str) -> ValuationResult:
    """Three independent 'is this cheap' reads, all context (never fed into
    the mean-reversion trigger score):
    1. Current trailing P/E vs the stock's OWN historical trailing P/E at each
       of the last ~4-5 fiscal year-ends (price at that date / that year's own
       diluted EPS — not current EPS, which would badly distort fast-growth
       names like NVDA where old EPS was a fraction of today's).
    2. PEG ratio (P/E adjusted for earnings growth) — a single absolute read,
       no historical band needed since the growth adjustment already
       normalizes it.
    3. Current price/sales vs its own historical price/sales band — covers
       currently-unprofitable names (RXRX-style) where P/E doesn't exist at
       all but revenue-based cheapness still can be read.
    Cached once per ticker per day (mirrors app.fundamentals) since this pulls
    a 6-year price history plus annual financials — meaningfully heavier than
    a plain .info call, and fundamentals don't need sub-daily freshness.
    """
    from app.config import load_config
    from app.fundamentals import get_fundamentals

    key = (ticker, date.today())
    if key in _cache:
        return _cache[key]

    cfg = load_config().get("valuation", {})
    hist_period = cfg.get("history_period", _HIST_PERIOD)
    peg_cheap = cfg.get("peg_cheap_threshold", _PEG_CHEAP)
    peg_expensive = cfg.get("peg_expensive_threshold", _PEG_EXPENSIVE)
    band_cheap_pos = cfg.get("band_cheap_position", _BAND_CHEAP_POSITION)
    band_expensive_pos = cfg.get("band_expensive_position", _BAND_EXPENSIVE_POSITION)

    fund = get_fundamentals(ticker)
    result = ValuationResult(
        ticker=ticker,
        trailing_pe=fund["trailing_pe"], forward_pe=fund["forward_pe"],
        peg=fund["peg"], peg_label=_peg_label(fund["peg"], peg_cheap, peg_expensive),
        price_to_sales=fund["price_to_sales"],
    )

    try:
        t = yf.Ticker(ticker)
        stmt = t.income_stmt
        closes = t.history(period=hist_period, interval="1d", auto_adjust=True)["Close"]
    except Exception as exc:
        log.warning("valuation history fetch failed for %s: %s", ticker, exc)
        result.error = "history fetch failed"
        return result  # don't cache a transient failure — let the next call retry

    if stmt is None or stmt.empty or closes.empty:
        # Unlike a fetch failure, this is a stable property of the ticker (e.g.
        # an ETF has no income statement) — safe, and worth caching for the day.
        # Checked before touching closes.index below: an empty Close series can
        # carry a plain RangeIndex rather than a DatetimeIndex, and .tz would
        # raise AttributeError on that instead of falling through gracefully.
        result.error = "insufficient historical data"
        _cache[key] = result
        return result

    if closes.index.tz is not None:
        closes.index = closes.index.tz_localize(None)  # income_stmt columns are tz-naive

    pe_points = []
    if "Diluted EPS" in stmt.index:
        for fiscal_date, eps in stmt.loc["Diluted EPS"].dropna().items():
            if eps is None or eps <= 0:
                continue  # a loss year has no meaningful historical PE
            price = _price_on_or_before(closes, fiscal_date)
            if price is not None:
                pe_points.append(price / eps)

    ps_points = []
    shares = fund["shares_outstanding"]
    if "Total Revenue" in stmt.index and shares:
        for fiscal_date, revenue in stmt.loc["Total Revenue"].dropna().items():
            if revenue is None or revenue <= 0:
                continue
            # Revenue-per-share uses TODAY's share count against PAST revenue —
            # a standard simplification (yfinance has no historical share-count
            # feed either); buybacks/dilution over the years aren't reflected.
            revenue_per_share = revenue / shares
            price = _price_on_or_before(closes, fiscal_date)
            if price is not None and revenue_per_share > 0:
                ps_points.append(price / revenue_per_share)

    result.pe_band = _historical_band(pe_points, result.trailing_pe, band_cheap_pos, band_expensive_pos)
    result.ps_band = _historical_band(ps_points, result.price_to_sales, band_cheap_pos, band_expensive_pos)
    if result.pe_band and result.forward_pe is not None and result.forward_pe > 0:
        result.forward_pe_label = _classify_position(
            result.forward_pe, result.pe_band.low, result.pe_band.high,
            band_cheap_pos, band_expensive_pos,
        )
    result.verdict = _overall_verdict([
        result.pe_band.label if result.pe_band else None,
        result.peg_label,
        result.ps_band.label if result.ps_band else None,
    ])

    _cache[key] = result
    return result


def format_valuation(v: "ValuationResult | None") -> str:
    """Compact one-line read for the shared per-ticker block (signals,
    signalsplus, morning report, priority alerts) — e.g.
    'cheap  (PE cheap · PEG 0.57 cheap · P/S fair)'."""
    if v is None or v.verdict == "insufficient data":
        return "insufficient data"
    parts = []
    if v.pe_band:
        pe_part = f"PE {v.pe_band.label}"
        if v.forward_pe_label != "unknown":
            pe_part += f", fwd {v.forward_pe_label}"
        parts.append(pe_part)
    if v.peg is not None and v.peg_label != "unknown":
        parts.append(f"PEG {v.peg:.2f} {v.peg_label}")
    if v.ps_band:
        parts.append(f"P/S {v.ps_band.label}")
    return f"{v.verdict}  ({' · '.join(parts)})" if parts else v.verdict
