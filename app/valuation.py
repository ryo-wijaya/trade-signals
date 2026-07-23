import logging
import math
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

# Composite 0-100 score weights — see _composite_score for the full rationale.
# P/E-vs-history is the most time-tested, realized (not estimated) signal, so
# it carries the most weight. P/S-vs-history is weighted equal to PEG because
# it's the only signal that survives for currently-unprofitable names — a
# lower weight there would quietly make the score worse exactly where it
# matters most. Forward P/E gets the least weight since it leans on analyst
# estimates rather than realized fundamentals. Renormalized over whichever
# signals are actually available for a given ticker (see _composite_score).
_SCORE_WEIGHTS = {"pe": 0.35, "forward_pe": 0.15, "peg": 0.25, "ps": 0.25}

# PEG -> 0-100 logistic curve: centered on the Lynch "fair" line (PEG 1.0 -> 50),
# calibrated so PEG 2.0 (the "expensive" line) -> ~90 and PEG 0.5 -> ~25.
_PEG_SCORE_MIDPOINT = 1.0
_PEG_SCORE_STEEPNESS = 2.2

_SCORE_BANDS = (  # (upper bound exclusive, label) — walked in order
    (20, "very cheap"), (40, "cheap"), (60, "fair"), (80, "expensive"), (101, "very expensive"),
)

_cache: dict[tuple[str, date], "ValuationResult"] = {}


@dataclass
class HistoricalBand:
    low: float
    high: float
    median: float
    n: int  # number of historical fiscal-year data points behind this band
    label: str  # "cheap" / "fair" / "expensive" / "unknown"
    # Defaulted (rather than required) so existing test fixtures that only
    # care about low/high/median/n/label don't all need updating; every real
    # band from _historical_band() below always sets these explicitly.
    mean: float = 0.0
    stdev: float = 0.0


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
    # Composite 0-100 "expensiveness" score (100 = most expensive) and its
    # plain-English band — see _composite_score for the full methodology.
    # None/"insufficient data" when not a single signal was computable.
    score: float | None = None
    score_label: str = "insufficient data"
    # Each component's own 0-100 percentile, stored so consumers (e.g. /cheap's
    # "key driver" explanation) can identify the most influential signal
    # without recomputing the z-score/logistic transforms themselves.
    pe_score: float | None = None
    forward_pe_score: float | None = None
    peg_score: float | None = None
    ps_score: float | None = None
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
        low=low, high=high, median=statistics.median(points),
        mean=statistics.mean(points), stdev=statistics.stdev(points),
        n=len(points), label=_classify_position(current, low, high, cheap_pos, expensive_pos),
    )


def _zscore_percentile(current: float, mean: float, stdev: float) -> float:
    """How many standard deviations `current` sits from its own historical
    mean, mapped through the normal CDF to a smooth 0-100 percentile. Chosen
    over a min-max [low, high] scale because a single outlier fiscal year
    (e.g. PFE's 2022 COVID-vaccine earnings spike) would otherwise compress
    the rest of the scale into a sliver; a z-score instead treats that outlier
    as widening the historical range, and degrades gracefully — rather than
    hard-clipping — for a current value outside the observed min/max."""
    if stdev <= 0:
        # A perfectly constant historical multiple: any deviation now is
        # maximally notable in whichever direction it moved.
        if current == mean:
            return 50.0
        return 100.0 if current > mean else 0.0
    z = (current - mean) / stdev
    return 100.0 * 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _peg_score(peg: float | None, midpoint: float = _PEG_SCORE_MIDPOINT,
               steepness: float = _PEG_SCORE_STEEPNESS) -> float | None:
    """PEG needs no historical band (growth already normalizes it) — a
    logistic curve centered on the standard 1.0 'fair' line gives a smooth
    0-100 read with no hard cliff at the 1.0/2.0 thresholds. None (not
    scoreable) for a non-positive PEG, which reflects declining earnings
    rather than 'cheap growth' and isn't comparable on this scale."""
    if peg is None or peg <= 0:
        return None
    return 100.0 / (1.0 + math.exp(-steepness * (peg - midpoint)))


def _composite_score(components: list[tuple[float, float]]) -> float | None:
    """Weighted average of whichever (score, weight) pairs are available.
    Missing signals are simply absent from this list — their weight is
    redistributed proportionally among what's left, rather than defaulting
    to a neutral 50 that would bias the composite toward 'average' for
    exactly the names with the least data (e.g. unprofitable companies)."""
    total_weight = sum(w for _, w in components)
    if total_weight <= 0:
        return None
    return sum(s * w for s, w in components) / total_weight


def _score_label(score: float | None) -> str:
    if score is None:
        return "insufficient data"
    for upper, label in _SCORE_BANDS:
        if score < upper:
            return label
    return _SCORE_BANDS[-1][1]


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
    Also combined into a single 0-100 `score` (100 = most expensive, see
    _composite_score) via a weighted average of z-score/CDF percentiles (P/E,
    forward P/E, P/S each vs their own historical band) and a logistic curve
    (PEG) — missing signals are excluded and the rest reweighted, never
    defaulted to a neutral midpoint. `verdict`/`score` are both context; never
    fed into the mean-reversion trigger score.
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

    weights = cfg.get("score_weights", _SCORE_WEIGHTS)
    peg_mid = cfg.get("peg_score_midpoint", _PEG_SCORE_MIDPOINT)
    peg_steep = cfg.get("peg_score_steepness", _PEG_SCORE_STEEPNESS)

    fund = get_fundamentals(ticker)
    result = ValuationResult(
        ticker=ticker,
        trailing_pe=fund["trailing_pe"], forward_pe=fund["forward_pe"],
        peg=fund["peg"], peg_label=_peg_label(fund["peg"], peg_cheap, peg_expensive),
        price_to_sales=fund["price_to_sales"],
    )
    peg_score = _peg_score(result.peg, peg_mid, peg_steep)

    currency, financial_currency = fund["currency"], fund["financial_currency"]
    if currency and financial_currency and currency != financial_currency:
        # income_stmt's EPS/revenue are reported in the FILING currency (e.g.
        # BABA files in CNY, NVO in DKK) while the traded price (and .info's
        # own trailingPE/priceToSales) is in the ADR's USD currency — confirmed
        # live: dividing a USD price by a CNY EPS produced a "historical P/E"
        # of ~2.6 for BABA against an actual trailingPE of 18.2. Rather than
        # attempt FX conversion, the historical band is simply skipped for
        # these tickers; current P/E, P/S, and PEG (already currency-correct
        # from .info) are unaffected and still scored.
        result.error = f"financials reported in {financial_currency}, price in {currency} — historical band skipped"
        if peg_score is not None:
            result.peg_score = peg_score
            result.score = _composite_score([(peg_score, weights.get("peg", _SCORE_WEIGHTS["peg"]))])
            result.score_label = _score_label(result.score)
            result.verdict = _overall_verdict([result.peg_label])
        _cache[key] = result
        return result

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
        # PEG alone doesn't need price/financial history, so it can still be
        # scored/verdicted here (e.g. a ticker whose income_stmt fetch is thin
        # but whose .info still carries a usable PEG).
        if peg_score is not None:
            result.peg_score = peg_score
            result.score = _composite_score([(peg_score, weights.get("peg", _SCORE_WEIGHTS["peg"]))])
            result.score_label = _score_label(result.score)
            result.verdict = _overall_verdict([result.peg_label])
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

    components = []
    if result.pe_band and result.trailing_pe is not None:
        result.pe_score = _zscore_percentile(result.trailing_pe, result.pe_band.mean, result.pe_band.stdev)
        components.append((result.pe_score, weights.get("pe", _SCORE_WEIGHTS["pe"])))
    if result.pe_band and result.forward_pe is not None and result.forward_pe > 0:
        result.forward_pe_score = _zscore_percentile(result.forward_pe, result.pe_band.mean, result.pe_band.stdev)
        components.append((result.forward_pe_score, weights.get("forward_pe", _SCORE_WEIGHTS["forward_pe"])))
    if peg_score is not None:
        result.peg_score = peg_score
        components.append((peg_score, weights.get("peg", _SCORE_WEIGHTS["peg"])))
    if result.ps_band and result.price_to_sales is not None:
        result.ps_score = _zscore_percentile(result.price_to_sales, result.ps_band.mean, result.ps_band.stdev)
        components.append((result.ps_score, weights.get("ps", _SCORE_WEIGHTS["ps"])))

    result.score = _composite_score(components)
    result.score_label = _score_label(result.score)

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
