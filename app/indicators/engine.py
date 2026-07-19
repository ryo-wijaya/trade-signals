import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import yfinance as yf

from app.indicators.base import BaseIndicator, SignalResult
from app.rules import apply_rules

log = logging.getLogger(__name__)
_INDICATORS: list[BaseIndicator] = []


def register(indicator: BaseIndicator) -> None:
    _INDICATORS.append(indicator)


@dataclass
class IndicatorResult:
    ticker: str
    price: float
    prev_close: float
    signals: list[tuple[str, str, SignalResult]]  # (name, label, result)
    rules_passed: bool = field(default=True)
    rule_results: list[tuple[str, bool, str]] = field(default_factory=list)  # (name, passed, reason)

    @property
    def reversion_signals(self) -> list[tuple[str, str, SignalResult]]:
        return [(n, l, s) for n, l, s in self.signals if s.kind == "reversion"]

    @property
    def score(self) -> int:
        # Trigger score: mean-reversion votes only (oversold = +, overbought = -).
        # Trend indicators are context and deliberately excluded — netting them in
        # cancels the buy-low/sell-high signal this score exists to surface.
        return sum(s.signal for _, _, s in self.reversion_signals)

    @property
    def max_score(self) -> int:
        return len(self.reversion_signals)

    @property
    def trend_score(self) -> int:
        return sum(s.signal for _, _, s in self.signals if s.kind == "trend")

    @property
    def trend_label(self) -> str:
        trend = {n: s for n, _, s in self.signals if s.kind == "trend"}
        if not trend:
            return ""
        if any(s.display == "insufficient history" for s in trend.values()):
            return "trend unknown"
        if all(s.signal == 1 for s in trend.values()):
            label = "uptrend"
        elif all(s.signal == -1 for s in trend.values()):
            label = "downtrend"
        else:
            label = "mixed trend"
        ema50 = trend.get("EMA50")
        ema200 = trend.get("EMA")
        if ema50 and ema200 and ema50.value is not None and ema200.value is not None:
            label += " (golden cross)" if ema50.value > ema200.value else " (death cross)"
        return label


def _fetch_ohlcv(ticker: str) -> pd.DataFrame:
    from app.config import load_config
    cfg = load_config()
    dcfg = cfg.get("data", {})
    period = dcfg.get("history_period", "200d")
    interval = dcfg.get("bar_interval", "1h")
    rth_start = dcfg.get("rth_start", "09:30")
    rth_end = dcfg.get("rth_end", "16:00")
    resample = dcfg.get("resample", "2h")
    retries = dcfg.get("fetch_retries", 3)
    exchange_tz = cfg.get("scheduler", {}).get("exchange_timezone", "America/New_York")

    for attempt in range(retries):
        try:
            df = yf.Ticker(ticker).history(
                period=period, interval=interval, prepost=False, auto_adjust=True
            )
        except Exception:
            df = pd.DataFrame()
        if not df.empty:
            break
        if attempt < retries - 1:
            time.sleep(2 ** attempt)

    if df.empty:
        raise ValueError(f"No data returned for {ticker}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(exchange_tz)

    if resample.endswith("d") or interval.endswith("d"):
        return df.dropna()

    rth = df.between_time(rth_start, rth_end)
    return rth.resample(resample, label="right", closed="right").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()


def analyze(ticker: str) -> IndicatorResult:
    df = _fetch_ohlcv(ticker)
    price = float(df["Close"].iloc[-1])
    prev_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else price
    signals = [(ind.name, ind.label, ind.compute(df)) for ind in _INDICATORS]
    result = IndicatorResult(ticker=ticker, price=price, prev_close=prev_close, signals=signals)
    result.rules_passed, result.rule_results = apply_rules(df, result)
    return result


def is_priority(r: IndicatorResult, min_signals: int) -> bool:
    return abs(r.score) >= min_signals and r.rules_passed


def analyze_tickers(tickers: list[str]) -> tuple[list[IndicatorResult], list[IndicatorResult]]:
    from app.config import load_config
    cfg = load_config()
    sleep_secs = cfg.get("data", {}).get("ticker_sleep_seconds", 0.5)
    default_min = sum(1 for ind in _INDICATORS if ind.kind == "reversion")
    min_signals = cfg.get("scheduler", {}).get("priority_min_signals", default_min)
    results, alerts = [], []
    for i, ticker in enumerate(tickers):
        if i > 0:
            time.sleep(sleep_secs)
        try:
            r = analyze(ticker)
            results.append(r)
            if _INDICATORS and is_priority(r, min_signals):
                alerts.append(r)
        except Exception as exc:
            log.error("analyze failed for %s: %s", ticker, exc)
    return results, alerts
