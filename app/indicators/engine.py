import time
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import pytz
import yfinance as yf

from app.indicators.base import BaseIndicator, SignalResult

EST = pytz.timezone("America/New_York")
_INDICATORS: list[BaseIndicator] = []


def register(indicator: BaseIndicator) -> None:
    _INDICATORS.append(indicator)


@dataclass
class IndicatorResult:
    ticker: str
    price: float
    signals: list[tuple[str, str, SignalResult]]  # (name, label, result)

    @property
    def score(self) -> int:
        return sum(s.signal for _, _, s in self.signals)


def _fetch_ohlcv(ticker: str) -> pd.DataFrame:
    for attempt in range(3):
        try:
            df = yf.Ticker(ticker).history(
                period="200d", interval="1h", prepost=False, auto_adjust=True
            )
        except Exception:
            df = pd.DataFrame()
        if not df.empty:
            break
        if attempt < 2:
            time.sleep(2 ** attempt)

    if df.empty:
        raise ValueError(f"No data returned for {ticker}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("America/New_York")

    rth = df.between_time("09:30", "16:00")
    return rth.resample("2h", label="right", closed="right").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()


def analyze(ticker: str) -> IndicatorResult:
    df = _fetch_ohlcv(ticker)
    price = float(df["Close"].iloc[-1])
    signals = [(ind.name, ind.label, ind.compute(df)) for ind in _INDICATORS]
    return IndicatorResult(ticker=ticker, price=price, signals=signals)


def analyze_tickers(tickers: list[str]) -> tuple[list[IndicatorResult], list[IndicatorResult]]:
    results, alerts = [], []
    for i, ticker in enumerate(tickers):
        if i > 0:
            time.sleep(0.5)
        try:
            r = analyze(ticker)
            results.append(r)
            if _INDICATORS and abs(r.score) == len(_INDICATORS):
                alerts.append(r)
        except Exception as exc:
            print(f"[indicators] {ticker}: {exc}")
    return results, alerts
