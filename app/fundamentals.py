import logging
from datetime import date

import yfinance as yf

log = logging.getLogger(__name__)

_EMPTY_FUNDAMENTALS = {
    "trailing_pe": None, "forward_pe": None, "peg": None,
    "price_to_sales": None, "shares_outstanding": None,
    "revenue_growth": None, "earnings_growth": None, "profit_margin": None,
    "target_mean": None, "target_low": None, "target_high": None,
    "analyst_count": None, "recommendation": None,
}

# (ticker, date) -> fundamentals dict. None of this needs sub-daily freshness
# for reporting purposes, and caching cuts repeated yfinance .info calls
# across /signals, /signalsplus, /deepdive, and the morning report each day.
_cache: dict[tuple[str, date], dict] = {}


def get_fundamentals(ticker: str) -> dict:
    """One .info fetch per ticker per day — shared by get_pe() and
    app.valuation, so PE/PEG/price-to-sales/shares-outstanding never require
    more than one network round trip per ticker per day between them."""
    key = (ticker, date.today())
    if key in _cache:
        return _cache[key]

    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:
        log.warning("fundamentals fetch failed for %s: %s", ticker, exc)
        return dict(_EMPTY_FUNDAMENTALS)  # don't cache a transient failure — let the next call retry

    result = {
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "peg": info.get("trailingPegRatio") or info.get("pegRatio"),
        "price_to_sales": info.get("priceToSalesTrailing12Months"),
        "shares_outstanding": info.get("sharesOutstanding"),
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),
        "profit_margin": info.get("profitMargins"),
        "target_mean": info.get("targetMeanPrice"),
        "target_low": info.get("targetLowPrice"),
        "target_high": info.get("targetHighPrice"),
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "recommendation": info.get("recommendationKey"),
    }
    _cache[key] = result
    return result


def get_pe(ticker: str) -> tuple[float | None, float | None]:
    fund = get_fundamentals(ticker)
    return fund["trailing_pe"], fund["forward_pe"]


def _fmt_leg(pe: float | None) -> str:
    if pe is None:
        return "n/a"
    if pe <= 0:
        return "n/m"
    return f"{pe:.1f}"


def format_pe(trailing: float | None, forward: float | None) -> str:
    if trailing is None and forward is None:
        return "n/a"
    return f"{_fmt_leg(trailing)} / {_fmt_leg(forward)}"
