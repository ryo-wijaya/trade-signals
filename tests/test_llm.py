from dataclasses import dataclass, field
from datetime import date

from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.llm import (
    build_prompt, build_news_prompt, build_leaps_prompt, build_wheel_prompt,
    build_deepdive_prompt, trim_incomplete, clean_response, _valuation_line,
    _fundamentals_line, build_cheap_stock_prompt, build_cheap_portfolio_prompt,
)
from app.commands.portfolio_analysis import _build_prompt as build_portfolio_prompt
from app.valuation import ValuationResult, HistoricalBand


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
    sample: list = field(default_factory=list)
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


@dataclass
class _OptionsSnapshot:
    ticker: str
    spot: float
    expiration: str
    dte: int
    hv: float | None
    atm_iv: float | None
    iv_hv: float | None
    iv_hv_label: str
    put_call: dict = field(default_factory=dict)
    next_earnings: date | None = None
    error: str | None = None


def _valuation(**overrides) -> ValuationResult:
    defaults = dict(
        ticker="NVDA", trailing_pe=31.7, forward_pe=16.5,
        pe_band=HistoricalBand(low=39.0, high=112.0, median=46.2, n=4, label="cheap"),
        peg=0.57, peg_label="cheap", price_to_sales=20.3,
        ps_band=HistoricalBand(low=17.5, high=24.4, median=21.8, n=4, label="fair"),
        verdict="cheap", score=28.0, score_label="cheap",
    )
    defaults.update(overrides)
    return ValuationResult(**defaults)


def _fundamentals(**overrides) -> dict:
    defaults = {
        "trailing_pe": 31.7, "forward_pe": 16.5, "peg": 0.57,
        "price_to_sales": 20.3, "shares_outstanding": 24_221_000_000,
        "revenue_growth": 0.852, "earnings_growth": 2.145, "profit_margin": 0.63,
        "target_mean": 303.0, "target_low": 180.0, "target_high": 500.0,
        "analyst_count": 58, "recommendation": "strong_buy",
    }
    defaults.update(overrides)
    return defaults


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

    def test_rules_require_weighing_rating_and_price_target(self):
        prompt = build_prompt(_result())
        assert "the overall technical rating shown above" in prompt
        assert "the analyst price target's upside/downside" in prompt

    def test_detailed_ask_names_price_target_and_rating_as_citable(self):
        prompt = build_prompt(_result(), detailed=True)
        assert "the analyst price target's upside/downside" in prompt
        assert "the overall technical rating" in prompt

    def test_no_valuation_line_when_valuation_is_none(self):
        r = _result()
        assert r.valuation is None
        assert "Valuation vs its own history" not in build_prompt(r)

    def test_valuation_line_included_when_present(self):
        r = _result()
        r.valuation = _valuation()
        prompt = build_prompt(r)
        assert "Valuation vs its own history" in prompt
        assert "P/E 31.7 vs its own 4yr range 39-112 (cheap)" in prompt
        assert "PEG 0.57 (cheap)" in prompt
        assert "P/S 20.3 vs its own 4yr range 17.5-24.4 (fair)" in prompt
        assert "Overall: cheap." in prompt

    def test_fundamentals_line_included_when_present(self):
        r = _result()
        r.fundamentals = _fundamentals()
        prompt = build_prompt(r)
        assert "revenue +85% y/y" in prompt
        assert "analyst mean target $303 (+77% vs price)" in prompt

    def test_detailed_ask_demands_two_numbers_and_bans_lazy_earnings_catalyst(self):
        prompt = build_prompt(_result(), detailed=True)
        assert "MUST cite at least two specific numbers" in prompt
        assert "do NOT name the next earnings date as the catalyst unless it is within 2 weeks" in prompt
        assert "say what specifically in that report will move the stock" in prompt

    def test_short_ask_unchanged_no_two_number_requirement(self):
        prompt = build_prompt(_result(), detailed=False)
        assert "MUST cite at least two specific numbers" not in prompt
        assert "citing one specific fundamental fact" in prompt


class TestFundamentalsLine:
    def test_full_line_with_all_fields(self):
        r = _result()
        r.fundamentals = _fundamentals()
        line = _fundamentals_line(r)
        assert "revenue +85% y/y" in line
        assert "earnings +214% y/y" in line
        assert "profit margin 63%" in line
        assert "analyst mean target $303 (+77% vs price) from 58 analysts" in line
        assert "consensus strong buy" in line

    def test_empty_fundamentals_is_empty_line(self):
        assert _fundamentals_line(_result()) == ""

    def test_all_none_values_is_empty_line(self):
        r = _result()
        r.fundamentals = {k: None for k in _fundamentals()}
        assert _fundamentals_line(r) == ""

    def test_negative_growth_shows_sign(self):
        r = _result()
        r.fundamentals = _fundamentals(revenue_growth=-0.561, earnings_growth=None,
                                        profit_margin=None, target_mean=None)
        line = _fundamentals_line(r)
        assert "revenue -56% y/y" in line
        assert "earnings" not in line

    def test_target_omitted_when_price_zero(self):
        r = _result()
        r.price = 0
        r.fundamentals = _fundamentals(revenue_growth=None, earnings_growth=None, profit_margin=None)
        assert _fundamentals_line(r) == ""


class TestValuationLine:
    def test_none_valuation_is_empty(self):
        assert _valuation_line(None) == ""

    def test_insufficient_data_is_empty(self):
        assert _valuation_line(_valuation(verdict="insufficient data", pe_band=None,
                                           peg=None, peg_label="unknown", ps_band=None)) == ""

    def test_only_peg_available(self):
        v = _valuation(pe_band=None, ps_band=None, verdict="cheap")
        line = _valuation_line(v)
        assert "PEG 0.57 (cheap)" in line
        assert "P/E" not in line
        assert "P/S" not in line

    def test_no_computable_parts_but_verdict_not_insufficient_is_still_empty(self):
        # defensive: if verdict somehow isn't "insufficient data" but nothing
        # was actually computable, still shouldn't emit an empty claim
        v = _valuation(pe_band=None, peg=None, peg_label="unknown", ps_band=None, verdict="fair")
        assert _valuation_line(v) == ""

    def test_negative_peg_with_unknown_label_is_omitted(self):
        v = _valuation(pe_band=None, peg=-0.5, peg_label="unknown", verdict="fair")
        line = _valuation_line(v)
        assert "PEG" not in line


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
        sample = [
            _LeapsCandidate(expiration="2027-06-17", dte=330, strike=205.0, mid=38.20,
                            iv=0.47, delta=0.61, iv_hv=1.19, iv_hv_label="fair", breakeven=243.20),
            _LeapsCandidate(expiration="2027-12-17", dte=513, strike=205.0, mid=47.22,
                            iv=0.46, delta=0.66, iv_hv=1.18, iv_hv_label="fair", breakeven=252.22),
        ]
        defaults = dict(
            ticker="NVDA", spot=206.0, hv=0.39,
            sample=sample,
            put_call={"volume_ratio": 1.86}, next_earnings=date(2026, 8, 27),
        )
        defaults.update(overrides)
        return _LeapsScan(**defaults)

    def test_includes_ticker_and_sample(self):
        prompt = build_leaps_prompt(self._scan())
        assert "NVDA" in prompt
        assert "$205C" in prompt
        assert "IV/HV 1.18 (fair)" in prompt
        assert "breakeven $252.22" in prompt

    def test_shows_moneyness_pct_vs_spot(self):
        prompt = build_leaps_prompt(self._scan())
        assert "2027-06-17 (11mo out, -0% vs spot)" in prompt
        assert "2027-12-17 (17mo out, -0% vs spot)" in prompt

    def test_instructs_intelligent_selection_not_just_cheapest(self):
        prompt = build_leaps_prompt(self._scan())
        assert "do NOT simply pick whichever has the lowest IV/HV" in prompt

    def test_asks_for_long_reasoning_not_one_liner(self):
        prompt = build_leaps_prompt(self._scan())
        assert "not a one-line pick" in prompt

    def test_asks_for_up_to_3_strikes_named(self):
        prompt = build_leaps_prompt(self._scan())
        assert "up to 3 of the best strikes" in prompt
        assert "name the exact strike and expiration for each" in prompt

    def test_asks_to_weigh_risk_of_distance_from_spot(self):
        prompt = build_leaps_prompt(self._scan())
        assert "how big a move would actually be needed to pay off" in prompt

    def test_closing_verdict_format_and_meaning(self):
        prompt = build_leaps_prompt(self._scan())
        assert '"TRADE — one-sentence summary"' in prompt
        assert '"HOLD — one-sentence summary"' in prompt
        assert '"NO TRADE — one-sentence summary"' in prompt

    def test_permits_no_trade_when_nothing_attractive(self):
        prompt = build_leaps_prompt(self._scan())
        assert "If nothing here looks attractive, say so plainly" in prompt

    def test_every_strike_analyzed_rationale_included(self):
        prompt = build_leaps_prompt(self._scan())
        assert "Every near-the-money strike" in prompt
        assert "every expiration 1-2yr out was analyzed" in prompt

    def test_empty_says_so_with_actual_delta_band(self):
        scan = self._scan(sample=[], delta_min=0.35, delta_max=0.70)
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

    def test_ai_is_constrained_to_choose_from_candidates_shown(self):
        prompt = build_wheel_prompt(self._scan())
        assert "do not name a strike that isn't listed here" in prompt


class TestBuildDeepdivePrompt:
    def _snapshot(self, **overrides):
        defaults = dict(
            ticker="NVDA", spot=213.11, expiration="2026-08-28", dte=36, hv=0.39,
            atm_iv=0.46, iv_hv=1.18, iv_hv_label="fair",
            put_call={"volume_ratio": 0.27}, next_earnings=date(2026, 8, 27),
        )
        defaults.update(overrides)
        return _OptionsSnapshot(**defaults)

    def test_includes_technical_and_pe_context(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert "NVDA at $171.30" in prompt
        assert "oversold  %K 12.3" in prompt
        assert "downtrend (death cross)" in prompt
        assert "P/E:" in prompt

    def test_includes_options_snapshot(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert "2026-08-28, 36d out" in prompt
        assert "ATM IV 46%" in prompt
        assert "39% 90-day realized volatility" in prompt
        assert "IV/HV 1.18 (fair)" in prompt
        assert "Put/call volume ratio (near-term): 0.27" in prompt
        assert "Next earnings: 2026-08-27" in prompt

    def test_missing_iv_hv_data_says_so(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot(atm_iv=None, iv_hv=None, iv_hv_label="unknown"))
        assert "insufficient data for an IV/HV read" in prompt

    def test_snapshot_error_omits_options_section(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot(error="no options chain available"))
        assert "Options snapshot" not in prompt

    def test_none_snapshot_omits_options_section(self):
        prompt = build_deepdive_prompt(_result(), None)
        assert "Options snapshot" not in prompt

    def test_valuation_included_when_present(self):
        r = _result()
        r.valuation = _valuation()
        prompt = build_deepdive_prompt(r, self._snapshot())
        assert "Valuation vs its own history" in prompt
        assert "PEG 0.57 (cheap)" in prompt

    def test_no_valuation_line_when_absent(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert "Valuation vs its own history" not in prompt

    def test_asks_for_all_five_sections(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        for section in ("Technical Setup", "Fundamentals & Valuation", "Options & Sentiment",
                        "News, Catalysts & Competition", "Risks & Macro"):
            assert section in prompt

    def test_sections_are_shorter_and_data_anchored(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert "1-3 sentences" in prompt
        assert "anchored to a number or named fact" in prompt

    def test_asks_for_overall_rating_and_price_target(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert "confirmation gates, and overall rating" in prompt
        assert "the analyst price target's upside/downside versus the current price" in prompt

    def test_trade_plan_is_entry_zone_only(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert '"Trade Plan:"' in prompt
        assert "entry zone as a specific price or range" in prompt
        assert "no target or stop" in prompt

    def test_bans_lazy_earnings_catalyst(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert "not just 'next earnings' unless it's within 2 weeks" in prompt

    def test_includes_day_change_vs_prev_close(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert "-1.0% vs prev close $173.05" in prompt

    def test_includes_confirmation_gates_when_applicable(self):
        r = _result()
        r.rule_results = [
            ("price_structure", False, "no higher low yet"),
            ("volume_confirmation", True, ""),  # not applicable — hidden
        ]
        prompt = build_deepdive_prompt(r, self._snapshot())
        assert "Confirmation gates: price_structure FAILED (no higher low yet)." in prompt
        assert "volume_confirmation" not in prompt

    def test_no_gates_line_when_none_applicable(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert "Confirmation gates" not in prompt

    def test_includes_fundamentals_line_when_present(self):
        r = _result()
        r.fundamentals = _fundamentals()
        prompt = build_deepdive_prompt(r, self._snapshot())
        assert "analyst mean target $303" in prompt

    def test_instructs_live_search_for_news_competitors_macro(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert "use real web search" in prompt

    def test_closing_verdict_format_is_buy_sell_hold(self):
        prompt = build_deepdive_prompt(_result(), self._snapshot())
        assert '"BUY — one-sentence summary"' in prompt
        assert '"SELL — one-sentence summary"' in prompt
        assert '"HOLD — one-sentence summary"' in prompt

    def test_swing_not_day_trade_framing(self):
        assert "multi-week swing decision, not a day trade" in build_deepdive_prompt(_result(), self._snapshot())


class TestBuildCheapStockPrompt:
    def test_includes_computed_verdict_and_rating(self):
        r = _result()
        r.valuation = _valuation()
        prompt = build_cheap_stock_prompt(r)
        assert "Computed valuation verdict: 24/100 (cheap)" not in prompt  # score isn't 24 in this fixture
        assert "Computed valuation verdict:" in prompt
        assert "0=cheapest, 100=most expensive" in prompt
        assert "Overall technical rating: Strong Buy" in prompt

    def test_includes_valuation_and_fundamentals_lines(self):
        r = _result()
        r.valuation = _valuation()
        r.fundamentals = _fundamentals()
        prompt = build_cheap_stock_prompt(r)
        assert "Valuation vs its own history" in prompt
        assert "analyst mean target $303" in prompt

    def test_asks_to_weigh_all_factors_and_not_override_verdict(self):
        r = _result()
        r.valuation = _valuation()
        prompt = build_cheap_stock_prompt(r)
        assert "does an oversold/overbought read reinforce or contradict the valuation picture" in prompt
        assert "Do not invent a different cheap/fair/expensive label" in prompt
        assert "say so explicitly rather than ignoring it" in prompt

    def test_asks_for_balanced_not_one_sided_analysis(self):
        r = _result()
        r.valuation = _valuation()
        assert "not a one-sided pitch" in build_cheap_stock_prompt(r)


class TestBuildCheapPortfolioPrompt:
    def _scored_result(self, ticker="NVDA", **overrides):
        r = _result()
        r.ticker = ticker
        r.valuation = _valuation(**overrides)
        return r

    def test_includes_one_line_per_scored_ticker(self):
        results = [self._scored_result("NVDA"), self._scored_result("CRM", verdict="fair")]
        prompt = build_cheap_portfolio_prompt(results)
        assert "NVDA $171.30: valuation" in prompt
        assert "CRM $171.30: valuation" in prompt

    def test_unscored_tickers_excluded(self):
        r = _result()
        r.ticker = "IBIT"
        r.valuation = None
        prompt = build_cheap_portfolio_prompt([self._scored_result("NVDA"), r])
        assert "IBIT" not in prompt

    def test_asks_for_synthesis_not_per_ticker_recap(self):
        prompt = build_cheap_portfolio_prompt([self._scored_result("NVDA")])
        assert "not a per-ticker recap, a synthesis" in prompt

    def test_asks_to_flag_cheap_with_warning_signs(self):
        prompt = build_cheap_portfolio_prompt([self._scored_result("NVDA")])
        assert "cheap on paper but carry a warning sign" in prompt

    def test_asks_for_most_attractive_and_most_worth_avoiding(self):
        prompt = build_cheap_portfolio_prompt([self._scored_result("NVDA")])
        assert "the single most attractive name and the single one most worth trimming or avoiding" in prompt


class TestPortfolioPrompt:
    def test_includes_indicators_trend_and_bias(self):
        prompt = build_portfolio_prompt([_result()])
        assert "oversold  %K 12.3" in prompt
        assert "[downtrend (death cross)]" in prompt
        assert "Do not default to hold." in prompt
        assert "Strong Buy" in prompt  # +3/3 trigger

    def test_includes_valuation_summary_per_ticker_when_present(self):
        r = _result()
        r.valuation = _valuation()
        prompt = build_portfolio_prompt([r])
        assert "valuation: cheap  (PE cheap · PEG 0.57 cheap · P/S fair)" in prompt

    def test_no_valuation_clause_when_absent(self):
        prompt = build_portfolio_prompt([_result()])
        assert "valuation:" not in prompt

    def test_instructs_cheap_strengthens_expensive_raises_bar(self):
        prompt = build_portfolio_prompt([_result()])
        assert "'cheap' valuation read strengthens the case to add" in prompt
        assert "'expensive' raises the bar" in prompt
