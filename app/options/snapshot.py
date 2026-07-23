import logging
from dataclasses import dataclass, field
from datetime import date

import yfinance as yf

from app.options.chain import pick_expiration, fetch_chain, put_call_ratio
from app.options.volatility import realized_volatility, iv_hv_ratio, iv_hv_label

log = logging.getLogger(__name__)


@dataclass
class OptionsSnapshot:
    ticker: str
    spot: float
    expiration: str
    dte: int
    hv: float | None
    atm_iv: float | None
    iv_hv: float | None
    iv_hv_label: str
    put_call: dict = field(default_factory=dict)
    next_earnings: date | None = None
    error: str | None = None


def scan_snapshot(ticker: str) -> OptionsSnapshot:
    """A lightweight near-term options read (ATM IV vs realized vol, put/call
    ratio) — one input among several for /deepdive, not a full strike scan
    like the LEAPS/wheel scanners."""
    from app.config import load_config
    from app.earnings import next_earnings

    cfg = load_config().get("options", {}).get("snapshot", {})
    min_days = cfg.get("min_days", 30)
    max_days = cfg.get("max_days", 45)

    picked = pick_expiration(ticker, min_days, max_days)
    if picked is None:
        return OptionsSnapshot(ticker=ticker, spot=0, expiration="", dte=0, hv=None,
                                atm_iv=None, iv_hv=None, iv_hv_label="unknown",
                                error="no options chain available")
    expiration, dte = picked

    try:
        closes = yf.Ticker(ticker).history(period="120d", interval="1d", auto_adjust=True)["Close"]
        spot = float(closes.iloc[-1])
        hv = realized_volatility(closes, 90)
        chain = fetch_chain(ticker, expiration, spot, dte)
    except Exception as exc:
        log.warning("options snapshot failed for %s: %s", ticker, exc)
        return OptionsSnapshot(ticker=ticker, spot=0, expiration=expiration, dte=dte, hv=None,
                                atm_iv=None, iv_hv=None, iv_hv_label="unknown",
                                error="price/chain fetch failed")

    calls = chain[chain["type"] == "call"]
    atm_iv = None
    if not calls.empty:
        atm_row = calls.loc[(calls["strike"] - spot).abs().idxmin()]
        atm_iv = float(atm_row["impliedVolatility"])

    ratio = iv_hv_ratio(atm_iv, hv) if atm_iv is not None else None
    _, earnings_date = next_earnings(ticker)

    return OptionsSnapshot(
        ticker=ticker, spot=spot, expiration=expiration, dte=dte, hv=hv,
        atm_iv=atm_iv, iv_hv=ratio, iv_hv_label=iv_hv_label(ratio),
        put_call=put_call_ratio(chain), next_earnings=earnings_date,
    )
