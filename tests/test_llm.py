from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.llm import build_prompt, build_news_prompt, trim_incomplete, clean_response
from app.commands.portfolio_analysis import _build_prompt as build_portfolio_prompt


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


class TestPortfolioPrompt:
    def test_includes_indicators_trend_and_bias(self):
        prompt = build_portfolio_prompt([_result()])
        assert "oversold  %K 12.3" in prompt
        assert "[downtrend (death cross)]" in prompt
        assert "Do not default to hold." in prompt
        assert "Strong Buy" in prompt  # +3/3 trigger
