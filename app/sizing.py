import logging
import math

import yfinance as yf

from app.options.volatility import realized_volatility

log = logging.getLogger(__name__)


def suggest_position_size(
    ticker: str, price: float, account_size: float, risk_pct: float, stop_vol_multiple: float = 2.0,
) -> dict | None:
    """Volatility-based position sizing: risk `risk_pct` of `account_size` on
    this trade, with the stop set `stop_vol_multiple` daily-volatility moves
    below entry. Uses the stock's own realized volatility as the volatility
    input — the same proxy already used across the options module — not a
    true ATR(14); it's a reasonable stand-in, not a precision risk tool.
    Returns None if volatility or price data isn't available."""
    if price <= 0:
        return None
    try:
        closes = yf.Ticker(ticker).history(period="120d", interval="1d", auto_adjust=True)["Close"]
        vol = realized_volatility(closes, 90)
    except Exception as exc:
        log.warning("position sizing volatility fetch failed for %s: %s", ticker, exc)
        return None
    if vol is None or vol <= 0:
        return None

    daily_vol_pct = vol / math.sqrt(252)
    stop_distance = price * daily_vol_pct * stop_vol_multiple
    if stop_distance <= 0:
        return None

    risk_dollars = account_size * risk_pct
    shares = int(risk_dollars // stop_distance)
    return {
        "shares": shares,
        "stop_distance": stop_distance,
        "position_value": shares * price,
        "risk_dollars": risk_dollars,
    }
