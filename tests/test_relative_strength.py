from types import SimpleNamespace

import pandas as pd
import pytest

import app.relative_strength as rs_mod
from app.relative_strength import rank_relative_strength, format_relative_strength


def _closes(*prices: float) -> pd.Series:
    return pd.Series(list(prices))


class _FakeTicker:
    _data: dict[str, pd.Series] = {}

    def __init__(self, ticker):
        self.ticker = ticker

    def history(self, **kwargs):
        return pd.DataFrame({"Close": self._data[self.ticker]})


def _patch(monkeypatch, data: dict[str, pd.Series]):
    _FakeTicker._data = data
    monkeypatch.setattr(rs_mod, "yf", SimpleNamespace(Ticker=_FakeTicker))


class TestRankRelativeStrength:
    def test_ranks_by_return_relative_to_benchmark_descending(self, monkeypatch):
        # window=2: return computed from closes[-1] vs closes[-3]
        _patch(monkeypatch, {
            "SPY": _closes(100, 100, 100),          # benchmark flat: 0%
            "AAA": _closes(100, 110, 120),           # +20%
            "BBB": _closes(100, 95, 90),             # -10%
            "CCC": _closes(100, 102, 104),           # +4%
        })
        ranked = rank_relative_strength(["AAA", "BBB", "CCC"], window=2, benchmark="SPY")
        assert [t for t, _ in ranked] == ["AAA", "CCC", "BBB"]
        assert ranked[0][1] == pytest.approx(0.20)
        assert ranked[1][1] == pytest.approx(0.04)
        assert ranked[2][1] == pytest.approx(-0.10)

    def test_subtracts_nonzero_benchmark_return(self, monkeypatch):
        _patch(monkeypatch, {
            "SPY": _closes(100, 105, 110),  # +10%
            "AAA": _closes(100, 110, 120),  # +20% -> +10% relative
        })
        ranked = rank_relative_strength(["AAA"], window=2, benchmark="SPY")
        assert ranked == [("AAA", pytest.approx(0.10))]

    def test_ticker_with_insufficient_history_is_skipped(self, monkeypatch):
        _patch(monkeypatch, {
            "SPY": _closes(100, 100, 100),
            "AAA": _closes(100, 110, 120),
            "BBB": _closes(100),  # too short for window=2
        })
        ranked = rank_relative_strength(["AAA", "BBB"], window=2, benchmark="SPY")
        assert [t for t, _ in ranked] == ["AAA"]

    def test_ticker_fetch_failure_is_skipped(self, monkeypatch):
        _patch(monkeypatch, {
            "SPY": _closes(100, 100, 100),
            "AAA": _closes(100, 110, 120),
        })

        class _BoomTicker(_FakeTicker):
            def history(self, **kwargs):
                if self.ticker == "BBB":
                    raise RuntimeError("network down")
                return super().history(**kwargs)

        monkeypatch.setattr(rs_mod, "yf", SimpleNamespace(Ticker=_BoomTicker))
        ranked = rank_relative_strength(["AAA", "BBB"], window=2, benchmark="SPY")
        assert [t for t, _ in ranked] == ["AAA"]

    def test_benchmark_fetch_failure_returns_empty(self, monkeypatch):
        class _BoomTicker:
            def __init__(self, ticker):
                pass

            def history(self, **kwargs):
                raise RuntimeError("network down")

        monkeypatch.setattr(rs_mod, "yf", SimpleNamespace(Ticker=_BoomTicker))
        assert rank_relative_strength(["AAA"], window=2, benchmark="SPY") == []

    def test_benchmark_insufficient_history_returns_empty(self, monkeypatch):
        _patch(monkeypatch, {"SPY": _closes(100)})
        assert rank_relative_strength(["AAA"], window=2, benchmark="SPY") == []


class TestFormatRelativeStrength:
    def test_empty_ranking_returns_empty_string(self):
        assert format_relative_strength([], window=20) == ""

    def test_formats_ticker_and_signed_percent(self):
        out = format_relative_strength([("AAA", 0.052), ("BBB", -0.034)], window=20, benchmark="SPY")
        assert "Relative Strength" in out
        assert "20d vs SPY" in out
        assert "AAA" in out and "+5.2%" in out
        assert "BBB" in out and "-3.4%" in out


@pytest.mark.network
class TestLiveRelativeStrength:
    def test_live_ranking_is_sane(self):
        ranked = rank_relative_strength(["AAPL", "MSFT"], window=20, benchmark="SPY")
        tickers = [t for t, _ in ranked]
        assert set(tickers) <= {"AAPL", "MSFT"}
        for _, rel in ranked:
            assert -1.0 < rel < 1.0
