from app.indicators import bollinger, rsi, stochastic, ema50, ema  # noqa: F401 — registration; voters first, trend context last
from app.indicators.engine import analyze, analyze_tickers, IndicatorResult
