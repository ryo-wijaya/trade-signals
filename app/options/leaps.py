import logging
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import yfinance as yf

from app.options.chain import pick_expirations, fetch_chain, put_call_ratio, liquid_mask
from app.options.volatility import realized_volatility, iv_hv_ratio, iv_hv_label

if TYPE_CHECKING:
    from app.indicators.engine import IndicatorResult

log = logging.getLogger(__name__)


@dataclass
class LeapsCandidate:
    expiration: str
    dte: int
    strike: float
    mid: float
    iv: float
    delta: float
    iv_hv: float | None
    iv_hv_label: str
    open_interest: int
    spread_pct: float
    breakeven: float  # strike + premium paid — price the stock must reach to break even at expiration


@dataclass
class LeapsScan:
    ticker: str
    spot: float
    hv: float | None
    delta_min: float = 0.35
    delta_max: float = 0.70
    candidates: list[LeapsCandidate] = field(default_factory=list)  # ascending by (expiration, distance from spot)
    put_call: dict = field(default_factory=dict)
    next_earnings: date | None = None
    indicator: "IndicatorResult | None" = None
    error: str | None = None


def scan_leaps(ticker: str) -> LeapsScan:
    from app.config import load_config
    from app.earnings import next_earnings

    cfg = load_config().get("options", {}).get("leaps", {})
    min_days = cfg.get("min_days", 365)
    max_days = cfg.get("max_days", 730)
    delta_min = cfg.get("delta_min", 0.35)
    delta_max = cfg.get("delta_max", 0.70)
    min_oi = cfg.get("min_open_interest", 10)
    max_spread = cfg.get("max_spread_pct", 0.15)
    hv_window = cfg.get("hv_window_days", 90)
    max_expirations = cfg.get("max_expirations", 4)
    per_expiration = cfg.get("candidates_per_expiration", 3)

    expirations = pick_expirations(ticker, min_days, max_days, max_expirations)
    if not expirations:
        return LeapsScan(ticker=ticker, spot=0, hv=None,
                          delta_min=delta_min, delta_max=delta_max,
                          error="no options chain available")

    try:
        # Last completed daily close, not a live quote — consistent with how
        # "price" is defined everywhere else in the app (app/indicators/engine.py),
        # and avoids a second separate yfinance round trip for a live quote.
        closes = yf.Ticker(ticker).history(period="120d", interval="1d", auto_adjust=True)["Close"]
        spot = float(closes.iloc[-1])
        hv = realized_volatility(closes, hv_window)
    except Exception as exc:
        log.warning("leaps scan failed for %s: %s", ticker, exc)
        return LeapsScan(ticker=ticker, spot=0, hv=None,
                          delta_min=delta_min, delta_max=delta_max,
                          error="price fetch failed")

    candidates: list[LeapsCandidate] = []
    put_call = {}
    any_chain_fetched = False
    for expiration, dte in expirations:
        try:
            chain = fetch_chain(ticker, expiration, spot, dte)
        except Exception as exc:
            log.warning("chain fetch failed for %s %s: %s", ticker, expiration, exc)
            continue
        any_chain_fetched = True
        if not put_call:
            # Sentiment from the soonest *successfully fetched* expiration, not
            # necessarily the first one in the list (that one may have failed).
            put_call = put_call_ratio(chain)

        calls = chain[chain["type"] == "call"].copy()
        calls = calls[
            calls["delta"].notna()
            & calls["delta"].between(delta_min, delta_max)
            & (calls["openInterest"].fillna(0) >= min_oi)
            & liquid_mask(calls, max_spread)
        ]

        rows = []
        for _, row in calls.iterrows():
            ratio = iv_hv_ratio(row["impliedVolatility"], hv)
            rows.append(LeapsCandidate(
                expiration=expiration, dte=dte,
                strike=float(row["strike"]),
                mid=float(row["mid"]),
                iv=float(row["impliedVolatility"]),
                delta=float(row["delta"]),
                iv_hv=ratio,
                iv_hv_label=iv_hv_label(ratio),
                open_interest=int(row["openInterest"]),
                spread_pct=float(row["spread_pct"]),
                breakeven=float(row["strike"]) + float(row["mid"]),
            ))
        # Nearest-the-money first within this expiration — the point is capturing
        # gamma-driven price movement over a multi-month hold, not stock-replacement
        # leverage or far-OTM cheapness. IV/HV cheapness is the tiebreak among
        # similarly-ATM strikes, not the primary sort.
        rows.sort(key=lambda c: (abs(c.strike - spot), c.iv_hv is None, c.iv_hv if c.iv_hv is not None else 0))
        candidates.extend(rows[:per_expiration])

    if not any_chain_fetched:
        return LeapsScan(ticker=ticker, spot=spot, hv=hv,
                          delta_min=delta_min, delta_max=delta_max,
                          error="options chain fetch failed")

    _, earnings_date = next_earnings(ticker)

    indicator = None
    try:
        from app.indicators import analyze
        indicator = analyze(ticker)
    except Exception as exc:
        log.warning("indicator context fetch failed for %s: %s", ticker, exc)

    return LeapsScan(
        ticker=ticker, spot=spot, hv=hv,
        delta_min=delta_min, delta_max=delta_max,
        candidates=candidates, put_call=put_call, next_earnings=earnings_date,
        indicator=indicator,
    )
