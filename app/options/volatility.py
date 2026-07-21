import numpy as np
import pandas as pd


def realized_volatility(closes: pd.Series, window: int) -> float | None:
    """Annualized stdev of log returns over the trailing `window` bars —
    a realized-volatility proxy. NOT a substitute for true historical IV
    (yfinance has no IV time series without a paid feed); this compares
    current option IV against how much the stock has actually moved."""
    log_ret = np.log(closes / closes.shift(1)).dropna()
    if len(log_ret) < window:
        return None
    return float(log_ret.tail(window).std() * np.sqrt(252))


def iv_hv_ratio(iv: float, hv: float | None) -> float | None:
    if hv is None or hv <= 0:
        return None
    return iv / hv


def iv_hv_label(ratio: float | None) -> str:
    if ratio is None:
        return "unknown"
    if ratio < 0.7:
        return "very cheap"
    if ratio < 0.9:
        return "cheap"
    if ratio <= 1.3:
        return "fair"
    if ratio <= 1.6:
        return "rich"
    return "very rich"
