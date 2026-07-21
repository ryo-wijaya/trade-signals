import logging
import math
from datetime import datetime

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


def black_scholes_delta(
    spot: float, strike: float, dte_days: float, iv: float,
    r: float = 0.045, is_call: bool = True,
) -> float | None:
    """Black-Scholes delta. No dividend yield term — acceptable approximation
    for ranking/filtering purposes, not a precision-pricing tool."""
    T = dte_days / 365.0
    if T <= 0 or iv is None or iv <= 0 or spot <= 0 or strike <= 0:
        return None
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    n = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    return n if is_call else n - 1


def _dated_expirations(ticker: str) -> list[tuple[str, int]] | None:
    try:
        expirations = yf.Ticker(ticker).options
    except Exception as exc:
        log.warning("options list fetch failed for %s: %s", ticker, exc)
        return None
    if not expirations:
        return None
    today = datetime.now().date()
    return [(e, (datetime.strptime(e, "%Y-%m-%d").date() - today).days) for e in expirations]


def pick_expiration(ticker: str, min_days: int, max_days: int) -> tuple[str, int] | None:
    """Picks the expiration closest to [min_days, max_days]. If none fall
    inside the window, widens to the nearest available expiration overall
    and returns it anyway — the caller must disclose the actual DTE used,
    since smaller-cap tickers often have only one or two long-dated
    expirations listed at all."""
    dated = _dated_expirations(ticker)
    if not dated:
        return None

    target = (min_days + max_days) / 2
    in_window = [(e, d) for e, d in dated if min_days <= d <= max_days]
    return min(in_window or dated, key=lambda x: abs(x[1] - target))


def pick_expirations(ticker: str, min_days: int, max_days: int, max_count: int) -> list[tuple[str, int]]:
    """Same window logic as pick_expiration, but returns every expiration
    inside [min_days, max_days] (ascending by DTE, capped at max_count) so a
    LEAPS scan can compare prices across the whole 1-2yr term structure
    instead of a single expiration. Falls back to the single nearest
    expiration overall if none fall inside the window (thin chains on
    smaller-cap tickers)."""
    dated = _dated_expirations(ticker)
    if not dated:
        return []

    in_window = sorted([(e, d) for e, d in dated if min_days <= d <= max_days], key=lambda x: x[1])
    if in_window:
        return in_window[:max_count]

    target = (min_days + max_days) / 2
    return [min(dated, key=lambda x: abs(x[1] - target))]


def fetch_chain(ticker: str, expiration: str, spot: float, dte_days: int) -> pd.DataFrame:
    """Combined calls+puts chain with mid price, spread %, and computed delta.
    Rows with no bid (worthless/stale/unquoted) are dropped — lastPrice on
    illiquid strikes is frequently stale and crossed with the live bid/ask,
    so mid = (bid+ask)/2 is used everywhere instead of lastPrice."""
    raw = yf.Ticker(ticker).option_chain(expiration)

    calls = raw.calls.copy()
    calls["type"] = "call"
    puts = raw.puts.copy()
    puts["type"] = "put"
    df = pd.concat([calls, puts], ignore_index=True)

    df = df[(df["bid"] > 0) & df["ask"].notna() & (df["ask"] > 0)].copy()
    df["mid"] = (df["bid"] + df["ask"]) / 2
    df["spread_pct"] = (df["ask"] - df["bid"]) / df["mid"]
    df["delta"] = df.apply(
        lambda row: black_scholes_delta(
            spot, row["strike"], dte_days, row["impliedVolatility"],
            is_call=(row["type"] == "call"),
        ),
        axis=1,
    )
    return df


def liquid_mask(chain_df: pd.DataFrame, max_spread_pct: float, min_absolute_spread: float = 0.10) -> pd.Series:
    """Spread filter: percentage spread alone unfairly excludes cheap,
    low-priced-underlying contracts (e.g. a nickel-wide spread on a $0.20
    premium is "50%" but perfectly tradeable) — a candidate passes if EITHER
    the percentage spread is tight OR the absolute spread is small in dollars."""
    absolute_spread = chain_df["ask"] - chain_df["bid"]
    return (chain_df["spread_pct"] <= max_spread_pct) | (absolute_spread <= min_absolute_spread)


def put_call_ratio(chain_df: pd.DataFrame) -> dict:
    calls = chain_df[chain_df["type"] == "call"]
    puts = chain_df[chain_df["type"] == "put"]
    call_vol = calls["volume"].fillna(0).sum()
    put_vol = puts["volume"].fillna(0).sum()
    call_oi = calls["openInterest"].fillna(0).sum()
    put_oi = puts["openInterest"].fillna(0).sum()
    return {
        "volume_ratio": (put_vol / call_vol) if call_vol else None,
        "oi_ratio": (put_oi / call_oi) if call_oi else None,
    }
