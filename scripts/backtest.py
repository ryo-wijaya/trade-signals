"""Replay the mean-reversion trigger over historical daily bars.

For every day in the configured history window, computes the 3-voter trigger
score exactly as the app would have (Bollinger, RSI level, Stochastic), then
measures forward returns 5/10/20 trading days later for two variants:

  raw          |score| >= threshold
  + alert gate raw + the side's confirmation rule (structure for buys,
               volume for sells) — i.e. exactly what fires a priority alert

Buy triggers count a win when the forward return is positive; sell triggers
when it is negative. "Baseline" is the average outcome over every evaluated
day, so a variant is only pulling its weight if it beats baseline.

Usage:
    python scripts/backtest.py                 # full watchlist
    python scripts/backtest.py NVDA PFE        # specific tickers
    python scripts/backtest.py --threshold 3   # unanimous triggers only
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_watchlist  # noqa: E402
from app.indicators.engine import _fetch_ohlcv  # noqa: E402
from app.indicators.bollinger import BollingerBandsIndicator  # noqa: E402
from app.indicators.rsi import RSILevel  # noqa: E402
from app.indicators.stochastic import Stochastic  # noqa: E402
from app.rules.price_structure import PriceStructure  # noqa: E402
from app.rules.volume_confirmation import VolumeConfirmation  # noqa: E402

HORIZONS = [5, 10, 20]
WARMUP = 60  # bars before first evaluation; covers every indicator window

_VOTERS = [BollingerBandsIndicator(), RSILevel(), Stochastic()]
_STRUCTURE = PriceStructure()
_VOLUME = VolumeConfirmation()


@dataclass
class _Stub:  # minimal IndicatorResult stand-in for rule checks
    ticker: str
    price: float
    prev_close: float
    score: int


@dataclass
class Event:
    ticker: str
    date: str
    side: str  # "buy" | "sell"
    variant_flags: tuple[bool, bool]  # raw, + alert gate
    fwd: dict[int, float]  # horizon -> forward return


def scan_ticker(ticker: str, threshold: int) -> tuple[list[Event], list[dict[int, float]]]:
    df = _fetch_ohlcv(ticker)
    close = df["Close"]
    events, baseline = [], []
    for i in range(WARMUP, len(df) - max(HORIZONS)):
        sub = df.iloc[: i + 1]
        fwd = {h: float(close.iloc[i + h] / close.iloc[i] - 1) for h in HORIZONS}
        baseline.append(fwd)

        score = sum(v.compute(sub).signal for v in _VOTERS)
        if abs(score) < threshold:
            continue

        stub = _Stub(
            ticker=ticker,
            price=float(close.iloc[i]),
            prev_close=float(close.iloc[i - 1]),
            score=score,
        )
        # Side-aware rules: structure auto-passes sells, volume auto-passes buys,
        # so ANDing both reproduces the app's asymmetric alert gate exactly.
        alert_ok = _STRUCTURE.check(sub, stub).passed and _VOLUME.check(sub, stub).passed
        events.append(Event(
            ticker=ticker,
            date=str(df.index[i].date()),
            side="buy" if score > 0 else "sell",
            variant_flags=(True, alert_ok),
            fwd=fwd,
        ))
    return events, baseline


def _stats(rows: list[dict[int, float]], side: str) -> str:
    if not rows:
        return "     0" + "".join(f"  {'—':>6} {'—':>7}" for _ in HORIZONS)
    cells = []
    for h in HORIZONS:
        vals = [r[h] for r in rows]
        wins = sum(1 for v in vals if (v > 0 if side == "buy" else v < 0))
        avg = sum(vals) / len(vals)
        cells.append(f"  {wins / len(vals):>5.0%} {avg:>+7.2%}")
    return f"{len(rows):>6}" + "".join(cells)


def report(events: list[Event], baseline: list[dict[int, float]]) -> None:
    header = f"{'variant':<22}{'n':>6}" + "".join(f"  {h}d win {h}d avg".rjust(15) for h in HORIZONS)
    variants = ["raw", "+ alert gate"]
    for side in ("buy", "sell"):
        print(f"\n{side.upper()} triggers")
        print(header)
        for vi, name in enumerate(variants):
            rows = [e.fwd for e in events if e.side == side and e.variant_flags[vi]]
            print(f"  {name:<20}{_stats(rows, side)}")
        print(f"  {'baseline (all days)':<20}{_stats(baseline, side)}")

    recent = sorted((e for e in events if e.variant_flags[1]), key=lambda e: e.date)[-8:]
    if recent:
        print("\nMost recent fully-confirmed triggers:")
        for e in recent:
            print(f"  {e.date}  {e.ticker:<6} {e.side:<4}  " +
                  "  ".join(f"{h}d {e.fwd[h]:+.1%}" for h in HORIZONS))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tickers", nargs="*", help="tickers to test (default: watchlist)")
    parser.add_argument("--threshold", type=int, default=2, help="min |trigger score| (default 2)")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] or load_watchlist()
    print(f"Backtesting {len(tickers)} tickers, trigger |score| >= {args.threshold}, horizons {HORIZONS}")

    all_events, all_baseline = [], []
    for t in tickers:
        try:
            events, baseline = scan_ticker(t, args.threshold)
        except Exception as exc:
            print(f"  {t}: skipped ({exc})")
            continue
        all_events.extend(events)
        all_baseline.extend(baseline)
        print(f"  {t}: {len(baseline)} days evaluated, {len(events)} triggers")

    report(all_events, all_baseline)


if __name__ == "__main__":
    main()
