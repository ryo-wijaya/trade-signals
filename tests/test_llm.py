from dataclasses import dataclass, field
from datetime import date

from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.llm import (
    build_prompt, build_news_prompt, build_leaps_prompt, build_wheel_prompt,
    trim_incomplete, clean_response,
)
from app.commands.portfolio_analysis import _build_prompt as build_portfolio_prompt


@dataclass
class _LeapsCandidate:
    expiration: str
    dte: int
    strike: float
    mid: float
    iv: float
    delta: float
    iv_hv: float | None
    iv_hv_label: str
    breakeven: float
    open_interest: int = 100
    spread_pct: float = 0.02


@dataclass
class _LeapsScan:
    ticker: str
    spot: float
    hv: float | None
    delta_min: float = 0.35
    delta_max: float = 0.70
    candidates: list = field(default_factory=list)
    put_call: dict = field(default_factory=dict)
    next_earnings: date | None = None
    indicator: object = None
    error: str | None = None


@dataclass
class _WheelCandidate:
    strike: float
    mid: float
    iv: float
    delta: float
    iv_hv: float | None
    iv_hv_label: str
    open_interest: int = 100
    spread_pct: float = 0.02
    annualized_yield: float = 0.0
    earnings_risk: bool = False


@dataclass
class _WheelScan:
    ticker: str
    spot: float
    expiration: str
    dte: int
    hv: float | None
    delta_min: float = 0.15
    delta_max: float = 0.30
    candidates: list = field(default_factory=list)
    put_call: dict = field(default_factory=dict)
    next_earnings: date | None = None
    indicator: object = None
    error: str | None = None


def _result() -> IndicatorResult:
    signals = [
        ("EMA50", "50 EMA", SignalResult(signal=-1, display="downtrend  below EMA $180.50", kind="trend", value=180.50)),
        ("EMA", "200 EMA", SignalResult(signal=-1, display="downtrend  below EMA $185.20", kind="trend", value=185.20)),
        ("BB", "Bollinger", SignalResult(signal=1, display="oversold  near lower band $168.10")),
        ("RSI", "RSI", SignalResult(signal=1, display="oversold  28.4 (falling vs MA 35.1)", value=28.4)),
        ("Stoch", "Stochastic", SignalResult(signal=1, display="oversold  %K 12.3")),
    ]
    return IndicatorResult(ticker="NVDA", price=171.30, prev_close=173.05, signals=signals)


class TestBuildPrompt:
    def test_includes_all_indicator_displays_and_trend(self):
        r = _result()
        prompt = build_prompt(r)
        for _, _, sig in r.signals:
            assert sig.display in prompt
        assert "downtrend (death cross)" in prompt
        assert "NVDA at $171.30" in prompt
        assert "trigger score +3/3" in prompt

    def test_verdict_first_format_and_trading_rules(self):
        prompt = build_prompt(_result())
        assert '"BUY — reason"' in prompt
        assert "buy low, sell high" in prompt

    def test_signal_state_and_horizon_in_prompt(self):
        prompt = build_prompt(_result())
        assert "Signal:" in prompt
        assert "10-20 trading days" in prompt

    def test_hold_bias_instructions_removed(self):
        for detailed in (False, True):
            prompt = build_prompt(_result(), detailed=detailed)
            assert "otherwise say hold" not in prompt
            assert "Do NOT restate" not in prompt

    def test_detailed_asks_for_catalyst(self):
        assert "catalyst" in build_prompt(_result(), detailed=True)
        assert "catalyst" not in build_prompt(_result(), detailed=False)


class TestTrimIncomplete:
    def test_verdict_survives_truncated_tail(self):
        assert trim_incomplete("BUY — earnings beat estimates. It also has") == "BUY — earnings beat estimates."

    def test_complete_text_unchanged(self):
        assert trim_incomplete("SELL — valuation stretched.") == "SELL — valuation stretched."

    def test_no_terminal_punctuation_returned_intact(self):
        assert trim_incomplete("HOLD — no clear edge either way") == "HOLD — no clear edge either way"

    def test_decimal_points_not_treated_as_sentence_end(self):
        text = "BUY — trades at 12.5x forward earnings. Next catalyst is"
        assert trim_incomplete(text) == "BUY — trades at 12.5x forward earnings."


class TestCleanResponse:
    def test_strips_citations_markdown_and_headers(self):
        raw = "## Verdict\n**BUY** — *strong* quarter[1][2]."
        assert clean_response(raw) == "Verdict\nBUY — strong quarter."


class TestBuildNewsPrompt:
    def test_includes_all_tickers(self):
        prompt = build_news_prompt(["AMZN", "META", "NVO"])
        assert "AMZN, META, NVO" in prompt

    def test_asks_for_materiality_filter(self):
        prompt = build_news_prompt(["AMZN"])
        assert "materially move the price" in prompt
        assert "Exclude routine analyst price-target tweaks" in prompt

    def test_instructs_omission_over_padding(self):
        prompt = build_news_prompt(["AMZN"])
        assert "do not pad" in prompt
        assert "omit it entirely" in prompt

    def test_asks_for_sector_macro_grouping(self):
        assert "Sector/Macro" in build_news_prompt(["AMZN", "META"])


class TestBuildLeapsPrompt:
    def _scan(self, **overrides):
        defaults = dict(
            ticker="NVDA", spot=206.0, hv=0.39,
            candidates=[
                _LeapsCandidate(expiration="2027-06-17", dte=330, strike=205.0, mid=38.20,
                                iv=0.47, delta=0.61, iv_hv=1.19, iv_hv_label="fair", breakeven=243.20),
                _LeapsCandidate(expiration="2027-12-17", dte=513, strike=205.0, mid=47.22,
                                iv=0.46, delta=0.66, iv_hv=1.18, iv_hv_label="fair", breakeven=252.22),
            ],
            put_call={"volume_ratio": 1.86}, next_earnings=date(2026, 8, 27),
        )
        defaults.update(overrides)
        return _LeapsScan(**defaults)

    def test_includes_ticker_and_candidates(self):
        prompt = build_leaps_prompt(self._scan())
        assert "NVDA" in prompt
        assert "$205C" in prompt
        assert "IV/HV 1.18 (fair)" in prompt
        assert "breakeven $252.22" in prompt

    def test_groups_candidates_by_expiration(self):
        prompt = build_leaps_prompt(self._scan())
        assert "Expiration 2027-06-17 (330d out)" in prompt
        assert "Expiration 2027-12-17 (513d out)" in prompt

    def test_verdict_first_trade_hold_no_trade_format(self):
        prompt = build_leaps_prompt(self._scan())
        assert '"TRADE <strike>C — reason"' in prompt
        assert '"HOLD — reason"' in prompt
        assert '"NO TRADE — reason"' in prompt

    def test_hold_and_no_trade_are_explicitly_permitted(self):
        prompt = build_leaps_prompt(self._scan())
        assert "fine and expected to say HOLD or NO TRADE" in prompt

    def test_near_atm_rationale_included_when_candidates_present(self):
        prompt = build_leaps_prompt(self._scan())
        assert "near-the-money strikes" in prompt
        assert "highest gamma" in prompt

    def test_near_atm_rationale_omitted_when_no_candidates(self):
        prompt = build_leaps_prompt(self._scan(candidates=[]))
        assert "highest gamma" not in prompt

    def test_empty_candidates_says_so_with_actual_delta_band(self):
        scan = self._scan(candidates=[], delta_min=0.35, delta_max=0.70)
        prompt = build_leaps_prompt(scan)
        assert "No call strikes met the delta (0.35-0.70)" in prompt

    def test_earnings_date_included(self):
        assert "2026-08-27" in build_leaps_prompt(self._scan())


class TestBuildWheelPrompt:
    def _scan(self, **overrides):
        defaults = dict(
            ticker="PFE", spot=24.91, expiration="2026-08-28", dte=38, hv=0.20,
            delta_min=0.15, delta_max=0.30,
            candidates=[
                _WheelCandidate(strike=23.0, mid=0.23, iv=0.29, delta=-0.17, iv_hv=1.45,
                                iv_hv_label="rich", annualized_yield=0.096, earnings_risk=True),
            ],
            put_call={"volume_ratio": 0.6}, next_earnings=date(2026, 8, 4),
        )
        defaults.update(overrides)
        return _WheelScan(**defaults)

    def test_includes_ticker_and_candidates(self):
        prompt = build_wheel_prompt(self._scan())
        assert "PFE" in prompt
        assert "$23P" in prompt
        assert "annualized yield 10%" in prompt

    def test_verdict_first_trade_hold_no_trade_format(self):
        prompt = build_wheel_prompt(self._scan())
        assert '"TRADE <strike>P — reason"' in prompt
        assert '"HOLD — reason"' in prompt
        assert '"NO TRADE — reason"' in prompt

    def test_earnings_risk_flag_shown(self):
        prompt = build_wheel_prompt(self._scan())
        assert "earnings falls before this expiration" in prompt

    def test_no_flag_when_not_at_risk(self):
        scan = self._scan()
        scan.candidates[0].earnings_risk = False
        prompt = build_wheel_prompt(scan)
        assert "earnings falls before this expiration" not in prompt

    def test_empty_candidates_says_so_with_actual_delta_band(self):
        scan = self._scan(candidates=[], delta_min=0.15, delta_max=0.30)
        prompt = build_wheel_prompt(scan)
        assert "No put strikes met the delta (0.15-0.30)" in prompt

    def test_assignment_caveat_present(self):
        assert "assigned into the stock" in build_wheel_prompt(self._scan())


class TestPortfolioPrompt:
    def test_includes_indicators_trend_and_bias(self):
        prompt = build_portfolio_prompt([_result()])
        assert "oversold  %K 12.3" in prompt
        assert "[downtrend (death cross)]" in prompt
        assert "Do not default to hold." in prompt
        assert "Strong Buy" in prompt  # +3/3 trigger
