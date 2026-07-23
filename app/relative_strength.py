import logging

import yfinance as yf

log = logging.getLogger(__name__)


def _pct_return(closes, window: int) -> float | None:
    if closes is None or len(closes) <= window:
        return None
    return float(closes.iloc[-1] / closes.iloc[-1 - window] - 1)


def rank_relative_strength(
    tickers: list[str], window: int = 20, benchmark: str = "SPY",
) -> list[tuple[str, float]]:
    """Ranks tickers by relative strength vs a benchmark over `window` trading
    days: (ticker's % return) - (benchmark's % return). Descending — strongest
    first. When several tickers are oversold at once, the strongest one is the
    better dip to buy first with limited capital. Tickers whose return can't
    be computed (insufficient history, fetch failure) are silently skipped."""
    try:
        bench_closes = yf.Ticker(benchmark).history(
            period="120d", interval="1d", auto_adjust=True
        )["Close"]
        bench_return = _pct_return(bench_closes, window)
    except Exception as exc:
        log.warning("relative strength benchmark (%s) fetch failed: %s", benchmark, exc)
        return []
    if bench_return is None:
        log.warning("relative strength benchmark (%s) has insufficient history", benchmark)
        return []

    ranked = []
    for ticker in tickers:
        try:
            closes = yf.Ticker(ticker).history(
                period="120d", interval="1d", auto_adjust=True
            )["Close"]
            ret = _pct_return(closes, window)
        except Exception as exc:
            log.warning("relative strength fetch failed for %s: %s", ticker, exc)
            continue
        if ret is None:
            continue
        ranked.append((ticker, ret - bench_return))

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def format_relative_strength(ranked: list[tuple[str, float]], window: int, benchmark: str = "SPY") -> str:
    if not ranked:
        return ""
    rows = [f"{ticker:<6}  {rel:+.1%}" for ticker, rel in ranked]
    return (f"<b>Relative Strength</b>  ({window}d vs {benchmark})\n"
            "<code>" + "\n".join(rows) + "</code>")
