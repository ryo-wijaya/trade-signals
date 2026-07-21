import logging
from datetime import date

import yfinance as yf

log = logging.getLogger(__name__)

# (ticker, date) -> (trailing_pe, forward_pe). PE doesn't need sub-daily
# freshness for reporting purposes, and this cuts repeated yfinance .info
# calls across /signals, /signalsplus, and the morning report each day.
_cache: dict[tuple[str, date], tuple[float | None, float | None]] = {}


def get_pe(ticker: str) -> tuple[float | None, float | None]:
    key = (ticker, date.today())
    if key in _cache:
        return _cache[key]

    try:
        info = yf.Ticker(ticker).info
        trailing = info.get("trailingPE")
        forward = info.get("forwardPE")
    except Exception as exc:
        log.warning("PE fetch failed for %s: %s", ticker, exc)
        trailing, forward = None, None

    result = (trailing, forward)
    _cache[key] = result
    return result


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
