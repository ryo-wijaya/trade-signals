import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import yfinance as yf

from app.options.chain import black_scholes_price, fetch_chain
from app.options.volatility import realized_volatility, iv_hv_ratio, iv_hv_label

log = logging.getLogger(__name__)

_DEFAULT_PRICE_RANGE_PCTS = [-0.15, -0.075, 0.0, 0.075, 0.15]
_DEFAULT_NUM_CHECKPOINTS = 4
_DEFAULT_RISK_FREE_RATE = 0.045
_DEFAULT_HV_WINDOW_DAYS = 90


@dataclass
class OpcCell:
    value: float          # theoretical per-share option value at this cell
    pl_per_contract: float  # (value - premium) * 100 * contracts
    pl_pct: float          # (value - premium) / premium


@dataclass
class OpcResult:
    ticker: str
    spot: float = 0.0
    strike: float = 0.0
    option_type: str = "call"  # "call" / "put"
    expiration: str = ""
    dte: int = 0
    iv: float | None = None
    hv: float | None = None
    iv_hv: float | None = None
    iv_hv_label: str = "unknown"
    premium: float = 0.0
    contracts: int = 1
    breakeven: float = 0.0
    max_loss_per_contract: float = 0.0
    checkpoints: list[int] = field(default_factory=list)   # days-out values, ascending, 0=today
    price_pcts: list[float] = field(default_factory=list)  # % moves from today's spot, ascending
    grid: dict[tuple[int, float], OpcCell] = field(default_factory=dict)  # (days_out, pct) -> cell
    error: str | None = None


def _nearest_expiration(ticker: str, target: date) -> tuple[str, date] | None:
    try:
        listed = yf.Ticker(ticker).options
    except Exception as exc:
        log.warning("opc expirations fetch failed for %s: %s", ticker, exc)
        return None
    if not listed:
        return None
    dated = [(e, datetime.strptime(e, "%Y-%m-%d").date()) for e in listed]
    return min(dated, key=lambda ed: abs((ed[1] - target).days))


def compute_opc(
    ticker: str, strike_target: float, option_type: str, expiration_target: str,
    premium_override: float | None = None, contracts: int = 1,
) -> OpcResult:
    """Fetches the real chain (nearest listed expiration to what was asked
    for, nearest listed strike to what was asked for) and builds a grid of
    theoretical P/L across a range of hypothetical future stock prices and
    time checkpoints between today and expiration -- "at what price does
    this need to be by when to make or lose money". IV is held constant at
    whatever the chain shows today; a real IV move (e.g. a post-earnings
    crush) isn't modeled, same disclosed simplification as the rest of the
    options module (app.options.chain.black_scholes_delta)."""
    from app.config import load_config
    cfg = load_config().get("options", {}).get("opc", {})
    price_pcts = cfg.get("price_range_pcts", _DEFAULT_PRICE_RANGE_PCTS)
    num_checkpoints = cfg.get("num_checkpoints", _DEFAULT_NUM_CHECKPOINTS)
    r = cfg.get("risk_free_rate", _DEFAULT_RISK_FREE_RATE)
    hv_window = cfg.get("hv_window_days", _DEFAULT_HV_WINDOW_DAYS)

    is_call = option_type.lower().startswith("c")

    try:
        target_date = datetime.strptime(expiration_target, "%Y-%m-%d").date()
    except ValueError:
        return OpcResult(ticker=ticker, error=f"invalid expiration '{expiration_target}' — use YYYY-MM-DD")

    picked = _nearest_expiration(ticker, target_date)
    if picked is None:
        return OpcResult(ticker=ticker, error="no options chain available")
    expiration_str, expiration_date = picked

    today = date.today()
    dte = (expiration_date - today).days
    if dte <= 0:
        return OpcResult(ticker=ticker, error=f"nearest listed expiration {expiration_str} has already passed")

    try:
        closes = yf.Ticker(ticker).history(period="120d", interval="1d", auto_adjust=True)["Close"]
        spot = float(closes.iloc[-1])
        hv = realized_volatility(closes, hv_window)
        chain = fetch_chain(ticker, expiration_str, spot, dte)
    except Exception as exc:
        log.warning("opc chain fetch failed for %s: %s", ticker, exc)
        return OpcResult(ticker=ticker, error="price/chain fetch failed")

    side = chain[chain["type"] == ("call" if is_call else "put")]
    if side.empty:
        return OpcResult(ticker=ticker, spot=spot, expiration=expiration_str, dte=dte,
                          error=f"no {'call' if is_call else 'put'} strikes available for {expiration_str}")

    row = side.loc[(side["strike"] - strike_target).abs().idxmin()]
    strike = float(row["strike"])
    iv = float(row["impliedVolatility"])
    premium = premium_override if premium_override is not None else float(row["mid"])
    if premium <= 0:
        return OpcResult(ticker=ticker, spot=spot, strike=strike, expiration=expiration_str, dte=dte,
                          error="premium must be greater than 0")

    ratio = iv_hv_ratio(iv, hv)
    breakeven = strike + premium if is_call else strike - premium
    max_loss = premium * 100 * contracts

    if num_checkpoints < 2:
        num_checkpoints = 2
    checkpoints = sorted({round(dte * i / (num_checkpoints - 1)) for i in range(num_checkpoints)})

    grid: dict[tuple[int, float], OpcCell] = {}
    for days_out in checkpoints:
        remaining = dte - days_out
        for pct in price_pcts:
            hypothetical_spot = spot * (1 + pct)
            value = black_scholes_price(hypothetical_spot, strike, remaining, iv, is_call, r)
            pl_per_contract = (value - premium) * 100 * contracts
            pl_pct = (value - premium) / premium
            grid[(days_out, pct)] = OpcCell(value=value, pl_per_contract=pl_per_contract, pl_pct=pl_pct)

    return OpcResult(
        ticker=ticker, spot=spot, strike=strike, option_type="call" if is_call else "put",
        expiration=expiration_str, dte=dte, iv=iv, hv=hv, iv_hv=ratio, iv_hv_label=iv_hv_label(ratio),
        premium=premium, contracts=contracts,
        breakeven=breakeven, max_loss_per_contract=max_loss,
        checkpoints=checkpoints, price_pcts=price_pcts, grid=grid,
    )
