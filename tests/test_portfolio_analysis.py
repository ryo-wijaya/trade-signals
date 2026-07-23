from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.commands.portfolio_analysis import _build_sizing_section, _build_cheap_section
import app.commands.portfolio_analysis as portfolio_mod
from app.valuation import ValuationResult


def _result(ticker: str, score: int, price: float = 100.0, valuation=None) -> IndicatorResult:
    signals = [(f"R{i}", f"R{i}", SignalResult(signal=1 if score > 0 else -1, display="x"))
               for i in range(abs(score))]
    return IndicatorResult(ticker=ticker, price=price, prev_close=price - 1, signals=signals, valuation=valuation)


def _valuation(verdict="cheap", **overrides):
    defaults = dict(ticker="X", peg=0.5, peg_label="cheap", verdict=verdict)
    defaults.update(overrides)
    return ValuationResult(**defaults)


def _cfg(**overrides):
    defaults = {"account_size": 10000, "risk_per_trade_pct": 0.01, "stop_vol_multiple": 2.0}
    defaults.update(overrides)
    return defaults


class TestBuildSizingSection:
    def test_only_includes_oversold_tickers(self, monkeypatch):
        monkeypatch.setattr(
            portfolio_mod, "suggest_position_size",
            lambda ticker, price, account_size, risk_pct, stop_multiple: {
                "shares": 10, "stop_distance": 5.0, "position_value": 1000.0, "risk_dollars": 100.0,
            },
        )
        results = [_result("NVDA", 2), _result("META", -2), _result("PFE", 0)]
        section = _build_sizing_section(results, _cfg())
        assert "NVDA" in section
        assert "META" not in section
        assert "PFE" not in section

    def test_skips_ticker_when_sizing_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            portfolio_mod, "suggest_position_size",
            lambda ticker, price, account_size, risk_pct, stop_multiple: None,
        )
        section = _build_sizing_section([_result("NVDA", 2)], _cfg())
        assert section == ""

    def test_skips_ticker_when_shares_is_zero(self, monkeypatch):
        monkeypatch.setattr(
            portfolio_mod, "suggest_position_size",
            lambda ticker, price, account_size, risk_pct, stop_multiple: {
                "shares": 0, "stop_distance": 5.0, "position_value": 0.0, "risk_dollars": 100.0,
            },
        )
        section = _build_sizing_section([_result("NVDA", 2)], _cfg())
        assert section == ""

    def test_empty_results_returns_empty_string(self):
        assert _build_sizing_section([], _cfg()) == ""

    def test_header_reflects_configured_account_size_and_risk(self, monkeypatch):
        monkeypatch.setattr(
            portfolio_mod, "suggest_position_size",
            lambda ticker, price, account_size, risk_pct, stop_multiple: {
                "shares": 10, "stop_distance": 5.0, "position_value": 1000.0, "risk_dollars": 100.0,
            },
        )
        section = _build_sizing_section([_result("NVDA", 1)], _cfg(account_size=25000, risk_per_trade_pct=0.02))
        assert "$25,000" in section
        assert "2.0%" in section

    def test_row_shows_shares_value_and_stop(self, monkeypatch):
        monkeypatch.setattr(
            portfolio_mod, "suggest_position_size",
            lambda ticker, price, account_size, risk_pct, stop_multiple: {
                "shares": 12, "stop_distance": 4.25, "position_value": 2556.0, "risk_dollars": 100.0,
            },
        )
        section = _build_sizing_section([_result("NVDA", 1, price=213.0)], _cfg())
        assert "NVDA: 12 sh (~$2,556)" in section
        assert "$4.25" in section

    def test_passes_configured_stop_multiple_through(self, monkeypatch):
        captured = {}

        def _fake(ticker, price, account_size, risk_pct, stop_multiple):
            captured["stop_multiple"] = stop_multiple
            return {"shares": 1, "stop_distance": 1.0, "position_value": price, "risk_dollars": 100.0}

        monkeypatch.setattr(portfolio_mod, "suggest_position_size", _fake)
        _build_sizing_section([_result("NVDA", 1)], _cfg(stop_vol_multiple=3.5))
        assert captured["stop_multiple"] == 3.5


class TestBuildCheapSection:
    def test_lists_only_cheap_verdict_tickers(self):
        results = [
            _result("NVDA", 1, valuation=_valuation(verdict="cheap")),
            _result("PFE", 1, valuation=_valuation(verdict="fair")),
            _result("CRM", 1, valuation=_valuation(verdict="expensive")),
        ]
        section = _build_cheap_section(results)
        assert "NVDA" in section
        assert "PFE" not in section
        assert "CRM" not in section

    def test_no_cheap_tickers_returns_empty_string(self):
        results = [_result("PFE", 1, valuation=_valuation(verdict="fair"))]
        assert _build_cheap_section(results) == ""

    def test_ticker_without_valuation_is_skipped(self):
        results = [_result("NVDA", 1, valuation=None)]
        assert _build_cheap_section(results) == ""

    def test_empty_results_returns_empty_string(self):
        assert _build_cheap_section([]) == ""

    def test_row_includes_formatted_valuation(self):
        results = [_result("NVDA", 1, valuation=_valuation(verdict="cheap", peg=0.57, peg_label="cheap"))]
        section = _build_cheap_section(results)
        assert "NVDA: cheap  (PEG 0.57 cheap)" in section
