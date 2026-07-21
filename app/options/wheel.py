import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

import pytz
import yfinance as yf

from app.options.chain import pick_expiration, fetch_chain, put_call_ratio, liquid_mask
from app.options.volatility import realized_volatility, iv_hv_ratio, iv_hv_label

if TYPE_CHECKING:
    from app.indicators.engine import IndicatorResult

log = logging.getLogger(__name__)


@dataclass
class WheelCandidate:
    strike: float
    mid: float
    iv: float
    delta: float
    iv_hv: float | None
    iv_hv_label: str
    open_interest: int
    spread_pct: float
    annualized_yield: float
    earnings_risk: bool  # earnings falls within the option's life — gap risk if assigned/held


@dataclass
class WheelScan:
    ticker: str
    spot: float
    expiration: str
    dte: int
    hv: float | None
    delta_min: float = 0.15
    delta_max: float = 0.30
    candidates: list[WheelCandidate] = field(default_factory=list)
    put_call: dict = field(default_factory=dict)
    next_earnings: date | None = None
    indicator: "IndicatorResult | None" = None
    error: str | None = None


def scan_wheel(ticker: str) -> WheelScan:
    from app.config import load_config
    from app.earnings import next_earnings

    full_cfg = load_config()
    cfg = full_cfg.get("options", {}).get("wheel", {})
    min_days = cfg.get("min_days", 25)
    max_days = cfg.get("max_days", 50)
    delta_min = cfg.get("delta_min", 0.15)
    delta_max = cfg.get("delta_max", 0.30)
    min_oi = cfg.get("min_open_interest", 10)
    max_spread = cfg.get("max_spread_pct", 0.15)
    exchange_tz = pytz.timezone(full_cfg.get("scheduler", {}).get("exchange_timezone", "America/New_York"))

    picked = pick_expiration(ticker, min_days, max_days)
    if picked is None:
        return WheelScan(ticker=ticker, spot=0, expiration="", dte=0, hv=None,
                          delta_min=delta_min, delta_max=delta_max,
                          error="no options chain available")
    expiration, dte = picked

    try:
        # Last completed daily close, not a live quote — consistent with how
        # "price" is defined everywhere else in the app (app/indicators/engine.py),
        # and avoids a second separate yfinance round trip for a live quote.
        closes = yf.Ticker(ticker).history(period="120d", interval="1d", auto_adjust=True)["Close"]
        spot = float(closes.iloc[-1])
        hv = realized_volatility(closes, 90)
        chain = fetch_chain(ticker, expiration, spot, dte)
    except Exception as exc:
        log.warning("wheel scan failed for %s: %s", ticker, exc)
        return WheelScan(ticker=ticker, spot=0, expiration=expiration, dte=dte, hv=None,
                          delta_min=delta_min, delta_max=delta_max,
                          error="price/chain fetch failed")

    _, earnings_date = next_earnings(ticker)
    exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
    # Uses the exchange's local date, not the server's — the server may run in
    # a different timezone (Fly deploys to `sin`), and a naive date.today()
    # could be off by a day right around midnight, shifting the earnings-risk
    # boundary incorrectly.
    today = datetime.now(exchange_tz).date()
    holds_through_earnings = bool(
        earnings_date is not None and today < earnings_date <= exp_date
    )

    puts = chain[chain["type"] == "put"].copy()
    puts["abs_delta"] = puts["delta"].abs()
    puts = puts[
        puts["delta"].notna()
        & puts["abs_delta"].between(delta_min, delta_max)
        & (puts["openInterest"].fillna(0) >= min_oi)
        & liquid_mask(puts, max_spread)
    ]

    candidates = []
    for _, row in puts.iterrows():
        ratio = iv_hv_ratio(row["impliedVolatility"], hv)
        ann_yield = (row["mid"] / row["strike"]) * (365.0 / dte)
        candidates.append(WheelCandidate(
            strike=float(row["strike"]),
            mid=float(row["mid"]),
            iv=float(row["impliedVolatility"]),
            delta=float(row["delta"]),
            iv_hv=ratio,
            iv_hv_label=iv_hv_label(ratio),
            open_interest=int(row["openInterest"]),
            spread_pct=float(row["spread_pct"]),
            annualized_yield=ann_yield,
            earnings_risk=holds_through_earnings,
        ))
    # Richest annualized premium first — the point of the wheel is collecting it.
    candidates.sort(key=lambda c: c.annualized_yield, reverse=True)
    candidates = candidates[:5]

    indicator = None
    try:
        from app.indicators import analyze
        indicator = analyze(ticker)
    except Exception as exc:
        log.warning("indicator context fetch failed for %s: %s", ticker, exc)

    return WheelScan(
        ticker=ticker, spot=spot, expiration=expiration, dte=dte, hv=hv,
        delta_min=delta_min, delta_max=delta_max,
        candidates=candidates, put_call=put_call_ratio(chain), next_earnings=earnings_date,
        indicator=indicator,
    )
