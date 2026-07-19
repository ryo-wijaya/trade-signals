"""Backtest research harness: sweeps trigger/gate variants from one data scan.

Unlike scripts/backtest.py (which replays the app's exact code path), this
computes all indicator series vectorized — mathematically identical for these
causal (rolling/ewm) indicators — so dozens of variants can be sliced from a
single scan. Used to justify threshold/gate choices with data.

Experiments:
  1. Trigger threshold sweep (|score| >= 1/2/3)
  2. Alert gate matrix per side (none / structure / volume / both)
  3. Volume min_ratio sweep (1.0 / 1.25 / 1.5)
  4. RSI(2) washout: alone, and as extra confirmation on the current trigger
  5. Trend regime split (price vs 200 EMA) on confirmed triggers

Usage: python scripts/research.py [TICKER ...]   (default: watchlist)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from ta.momentum import RSIIndicator, StochasticOscillator  # noqa: E402
from ta.trend import EMAIndicator  # noqa: E402
from ta.volatility import BollingerBands  # noqa: E402

from app.config import load_watchlist, load_config  # noqa: E402
from app.indicators.engine import _fetch_ohlcv  # noqa: E402

HORIZONS = [5, 10, 20]
WARMUP = 60


def scan(ticker: str) -> pd.DataFrame:
    cfg = load_config().get("indicators", {})
    bcfg, rcfg, scfg = cfg.get("bollinger", {}), cfg.get("rsi", {}), cfg.get("stochastic", {})

    df = _fetch_ohlcv(ticker)
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]

    bb = BollingerBands(close=close, window=bcfg.get("window_days", 20), window_dev=bcfg.get("std_dev", 2))
    buffer = bcfg.get("buffer_pct", 0.01)
    bb_buy = close <= bb.bollinger_lband() * (1 + buffer)
    bb_sell = close >= bb.bollinger_hband() * (1 - buffer)

    rsi = RSIIndicator(close=close, window=rcfg.get("window_days", 14)).rsi()
    rsi_buy = rsi <= rcfg.get("oversold", 30)
    rsi_sell = rsi >= rcfg.get("overbought", 70)

    k = StochasticOscillator(high=high, low=low, close=close,
                             window=scfg.get("window_days", 14),
                             smooth_window=scfg.get("smooth_window", 3)).stoch()
    st_buy = k < scfg.get("oversold", 20)
    st_sell = k > scfg.get("overbought", 80)

    score = (bb_buy.astype(int) - bb_sell.astype(int)
             + rsi_buy.astype(int) - rsi_sell.astype(int)
             + st_buy.astype(int) - st_sell.astype(int))

    rec = pd.DataFrame({
        "ticker": ticker,
        "score": score,
        "rsi2": RSIIndicator(close=close, window=2).rsi(),
        "struct_buy": (close > close.shift(1)) & (low > low.shift(1)),
        "struct_sell": (close < close.shift(1)) & (high < high.shift(1)),
        "vol_ratio": vol / vol.rolling(20).mean().shift(1),
        "above_ema200": close > EMAIndicator(close=close, window=200).ema_indicator(),
    })
    for h in HORIZONS:
        rec[f"fwd{h}"] = close.shift(-h) / close - 1
    return rec.iloc[WARMUP:-max(HORIZONS)].dropna(subset=[f"fwd{h}" for h in HORIZONS])


def stats(rows: pd.DataFrame, side: str) -> str:
    if rows.empty:
        return f"{0:>6}" + "".join(f"  {'—':>6} {'—':>8}" for _ in HORIZONS)
    cells = []
    for h in HORIZONS:
        vals = rows[f"fwd{h}"]
        win = (vals > 0).mean() if side == "buy" else (vals < 0).mean()
        cells.append(f"  {win:>6.0%} {vals.mean():>+8.2%}")
    return f"{len(rows):>6}" + "".join(cells)


def table(title: str, rows: list[tuple[str, pd.DataFrame, str]]) -> None:
    print(f"\n### {title}")
    print(f"{'variant':<34}{'n':>6}" + "".join(f"  {h}d win  {h}d avg".rjust(17) for h in HORIZONS))
    for name, subset, side in rows:
        print(f"  {name:<32}{stats(subset, side)}")


def main() -> None:
    tickers = [t.upper() for t in sys.argv[1:]] or load_watchlist()
    print(f"Scanning {len(tickers)} tickers…")
    frames = []
    for t in tickers:
        try:
            frames.append(scan(t))
            print(f"  {t}: ok")
        except Exception as exc:
            print(f"  {t}: skipped ({exc})")
    d = pd.concat(frames, ignore_index=True)
    print(f"\n{len(d)} ticker-days total")

    buy = d[d.score >= 2]
    sell = d[d.score <= -2]

    table("1. Threshold sweep (raw trigger)", [
        ("buy  score >= 1", d[d.score >= 1], "buy"),
        ("buy  score >= 2", buy, "buy"),
        ("buy  score = 3", d[d.score >= 3], "buy"),
        ("sell score <= -1", d[d.score <= -1], "sell"),
        ("sell score <= -2", sell, "sell"),
        ("sell score = -3", d[d.score <= -3], "sell"),
        ("baseline all days", d, "buy"),
    ])

    table("2. Gate matrix (|score| >= 2)", [
        ("buy  raw", buy, "buy"),
        ("buy  + structure", buy[buy.struct_buy], "buy"),
        ("buy  + volume>=1.0", buy[buy.vol_ratio >= 1.0], "buy"),
        ("buy  + structure + volume", buy[buy.struct_buy & (buy.vol_ratio >= 1.0)], "buy"),
        ("sell raw", sell, "sell"),
        ("sell + structure", sell[sell.struct_sell], "sell"),
        ("sell + volume>=1.0", sell[sell.vol_ratio >= 1.0], "sell"),
        ("sell + structure + volume", sell[sell.struct_sell & (sell.vol_ratio >= 1.0)], "sell"),
    ])

    table("3. Volume min_ratio sweep (on structure-confirmed)", [
        (f"{side}  struct + vol>={rt}",
         (buy[buy.struct_buy & (buy.vol_ratio >= rt)] if side == "buy"
          else sell[sell.struct_sell & (sell.vol_ratio >= rt)]),
         side)
        for side in ("buy", "sell") for rt in (1.0, 1.25, 1.5)
    ])

    table("4. RSI(2) washout", [
        ("buy  rsi2<10 alone", d[d.rsi2 < 10], "buy"),
        ("buy  rsi2<10 + score>=2", buy[buy.rsi2 < 10], "buy"),
        ("buy  rsi2<10 + confirmed", buy[(buy.rsi2 < 10) & buy.struct_buy & (buy.vol_ratio >= 1.0)], "buy"),
        ("sell rsi2>90 alone", d[d.rsi2 > 90], "sell"),
        ("sell rsi2>90 + score<=-2", sell[sell.rsi2 > 90], "sell"),
        ("sell rsi2>90 + confirmed", sell[(sell.rsi2 > 90) & sell.struct_sell & (sell.vol_ratio >= 1.0)], "sell"),
    ])

    conf_buy = buy[buy.struct_buy & (buy.vol_ratio >= 1.0)]
    conf_sell = sell[sell.struct_sell & (sell.vol_ratio >= 1.0)]
    table("5. Trend regime split (confirmed triggers)", [
        ("buy  above 200 EMA", conf_buy[conf_buy.above_ema200], "buy"),
        ("buy  below 200 EMA", conf_buy[~conf_buy.above_ema200], "buy"),
        ("buy  raw, above 200 EMA", buy[buy.above_ema200], "buy"),
        ("buy  raw, below 200 EMA", buy[~buy.above_ema200], "buy"),
        ("sell above 200 EMA", conf_sell[conf_sell.above_ema200], "sell"),
        ("sell below 200 EMA", conf_sell[~conf_sell.above_ema200], "sell"),
    ])


if __name__ == "__main__":
    main()
