import asyncio

from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.telegram import (
    _call, build_priority_alert, build_action_alert, build_stock_messages, signal_line,
    split_message, send, collect_output, _highlight_leading_verdict,
)
from app.valuation import ValuationResult, HistoricalBand


def _result(rev: list[int], trend: list[int] = (-1, -1)) -> IndicatorResult:
    signals = [
        ("EMA50", "50 EMA", SignalResult(signal=trend[0], display="ema50 display", kind="trend", value=100.0 + trend[0])),
        ("EMA", "200 EMA", SignalResult(signal=trend[1], display="ema200 display", kind="trend", value=100.0)),
        ("BB", "Bollinger", SignalResult(signal=rev[0], display="bb display")),
        ("RSI", "RSI", SignalResult(signal=rev[1], display="rsi display")),
        ("Stoch", "Stochastic", SignalResult(signal=rev[2], display="stoch display")),
    ]
    return IndicatorResult(ticker="TEST", price=100.0, prev_close=99.0, signals=signals)


class TestCall:
    def test_full_mapping(self):
        assert _call(3) == "Strong Buy"
        assert _call(2) == "Buy"
        assert _call(1) == "Lean Buy"
        assert _call(0) == "Hold"
        assert _call(-1) == "Lean Sell"
        assert _call(-2) == "Sell"
        assert _call(-3) == "Strong Sell"


class TestPriorityAlert:
    def test_label_matches_call_not_hardcoded_strong(self):
        r = _result([1, 1, 0])  # score +2 → "Buy", previously mislabeled "Strong Buy"
        alert = build_priority_alert(r)
        assert "TEST  Buy" in alert
        assert "Strong Buy" not in alert

    def test_includes_trend_context(self):
        alert = build_priority_alert(_result([1, 1, 0]))
        assert "downtrend" in alert


class TestActionAlert:
    def test_header_shows_ticker(self):
        r = _result([0, 0, 0])
        r.valuation = ValuationResult(ticker="TEST", verdict="cheap", score=15.0, score_label="very cheap")
        alert = build_action_alert(r)
        assert "ACTION ALERT" in alert
        assert "TEST" in alert

    def test_includes_price_and_technical_block(self):
        r = _result([0, 0, 0])
        r.valuation = ValuationResult(ticker="TEST", verdict="cheap", score=15.0, score_label="very cheap")
        alert = build_action_alert(r)
        assert "$100.00" in alert
        assert "Valuation" in alert  # from the shared _block() technical rows

    def test_ai_reason_appended_and_verdict_bolded(self):
        r = _result([0, 0, 0])
        r.valuation = ValuationResult(ticker="TEST", verdict="cheap", score=15.0, score_label="very cheap")
        alert = build_action_alert(r, ai_reason="BUY\n\nStrong setup across the board.")
        assert "<b>BUY</b>" in alert
        assert "Strong setup across the board." in alert

    def test_no_ai_reason_omits_trailing_section(self):
        r = _result([0, 0, 0])
        r.valuation = ValuationResult(ticker="TEST", verdict="cheap", score=15.0, score_label="very cheap")
        alert = build_action_alert(r, ai_reason="")
        assert "Signal:" in alert.splitlines()[-1]


class TestStockMessages:
    def test_rating_ignores_trend_votes(self):
        r = _result([1, 1, 0], trend=[-1, -1])
        body = build_stock_messages([r], "now")[1]
        assert "TEST</b>  $100.00  Buy" in body  # trend sells not netted in

    def test_all_five_indicator_rows_still_shown(self):
        msgs = build_stock_messages([_result([1, 1, 0])], "now")
        body = msgs[1]
        for display in ("ema50 display", "ema200 display", "bb display", "rsi display", "stoch display"):
            assert display in body

    def test_non_applicable_rules_hidden(self):
        r = _result([1, 1, 0])
        r.rule_results = [
            ("price_structure", True, "higher close and higher low"),
            ("volume_confirmation", True, ""),  # not applicable to buys
        ]
        body = build_stock_messages([r], "now")[1]
        assert "Structure" in body
        assert "Volume" not in body

    def test_valuation_row_shown_when_present(self):
        r = _result([1, 1, 0])
        r.valuation = ValuationResult(
            ticker="TEST", peg=0.57, peg_label="cheap",
            pe_band=HistoricalBand(low=39, high=112, median=46, n=4, label="cheap"),
            verdict="cheap",
        )
        body = build_stock_messages([r], "now")[1]
        assert "Valuation" in body
        assert "cheap  (PE cheap · PEG 0.57 cheap)" in body

    def test_valuation_row_shows_insufficient_data_when_absent(self):
        r = _result([1, 1, 0])  # r.valuation defaults to None
        body = build_stock_messages([r], "now")[1]
        assert "Valuation" in body
        assert "insufficient data" in body

    def test_target_row_shown_as_deterministic_data_not_ai_prose(self):
        r = _result([1, 1, 0])
        r.fundamentals = {"target_mean": 303.0, "analyst_count": 58, "recommendation": "strong_buy"}
        body = build_stock_messages([r], "now")[1]
        assert "Target" in body
        assert "$303 (+203% vs price)" in body  # r.price is 100.0 in this fixture

    def test_target_row_shows_na_when_no_analyst_target(self):
        r = _result([1, 1, 0])  # r.fundamentals defaults to {}
        body = build_stock_messages([r], "now")[1]
        assert "Target" in body
        assert "n/a" in body

    def test_pe_quality_row_shown_when_earnings_distorted(self):
        r = _result([1, 1, 0])
        r.valuation = ValuationResult(
            ticker="GOOGL", verdict="cheap", earnings_quality_label="inflated",
            pe_distortion_pct=0.26, core_pe=33.0, gaap_ttm_pe=24.4,
        )
        body = build_stock_messages([r], "now")[1]
        assert "PE Quality" in body
        assert "core P/E ~33.0 vs GAAP-TTM P/E ~24.4" in body

    def test_pe_quality_row_hidden_when_normal_or_unknown(self):
        r = _result([1, 1, 0])  # r.valuation defaults to None -> "unknown"
        body = build_stock_messages([r], "now")[1]
        assert "PE Quality" not in body

    def test_ai_summary_leading_verdict_is_bolded(self):
        r = _result([1, 1, 0])
        summaries = {"TEST": "BUY\n\nStrong technical setup with confirmed bounce."}
        body = build_stock_messages([r], "now", summaries=summaries)[1]
        assert "<b>BUY</b>" in body

    def test_ai_summary_without_leading_verdict_is_left_alone(self):
        r = _result([1, 1, 0])
        summaries = {"TEST": "Just a plain sentence with no verdict prefix."}
        body = build_stock_messages([r], "now", summaries=summaries)[1]
        assert "<b>" not in body.split("Signal:")[-1]  # no bolding introduced in the summary part


class TestHighlightLeadingVerdict:
    def test_bolds_buy_at_start(self):
        assert _highlight_leading_verdict("BUY\n\nreason here") == "<b>BUY</b>\n\nreason here"

    def test_bolds_verdict_with_dash_reason_on_same_line(self):
        assert _highlight_leading_verdict("SELL — reason here") == "<b>SELL</b> — reason here"

    def test_bolds_hold(self):
        assert _highlight_leading_verdict("HOLD\n\nreason") == "<b>HOLD</b>\n\nreason"

    def test_no_match_returns_text_unchanged(self):
        text = "Not a verdict-first reply at all."
        assert _highlight_leading_verdict(text) == text

    def test_does_not_match_verdict_word_mid_text(self):
        text = "Technicals are neutral but a BUY case could emerge."
        assert _highlight_leading_verdict(text) == text

    def test_does_not_partially_match_longer_word(self):
        # "BUYER" starts with "BUY" but \b after "BUY" must not match mid-word
        assert _highlight_leading_verdict("BUYER BEWARE") == "BUYER BEWARE"


class TestSignalLine:
    def test_confirmed_buy_trigger_is_entry(self):
        r = _result([1, 1, 0])
        r.rules_passed = True
        line = signal_line(r)
        assert "BUY ENTRY" in line
        assert "10–20 days" in line

    def test_unconfirmed_buy_trigger_is_setup(self):
        r = _result([1, 1, 0])
        r.rules_passed = False
        line = signal_line(r)
        assert "BUY setup" in line
        assert "Wait" in line

    def test_confirmed_sell_mentions_volume(self):
        r = _result([-1, -1, -1])
        r.rules_passed = True
        line = signal_line(r)
        assert "SELL ENTRY" in line
        assert "volume" in line

    def test_single_vote_is_no_action(self):
        assert "No action" in signal_line(_result([1, 0, 0]))
        assert "No action" in signal_line(_result([-1, 0, 0]))

    def test_neutral_is_no_signal(self):
        assert "Signal: none" in signal_line(_result([0, 0, 0]))

    def test_signal_line_in_stock_messages_and_alerts(self):
        r = _result([1, 1, 0])
        assert "Signal:" in build_stock_messages([r], "now")[1]
        assert "Signal:" in build_priority_alert(r)


class TestSplitMessage:
    def test_short_text_single_chunk(self):
        assert split_message("hello") == ["hello"]

    def test_long_text_splits_on_paragraphs_within_limit(self):
        paras = [f"paragraph {i} " + "x" * 500 for i in range(20)]
        text = "\n\n".join(paras)
        chunks = split_message(text)
        assert len(chunks) > 1
        assert all(len(c) <= 4000 for c in chunks)
        assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


class TestCollectOutput:
    def test_default_behavior_unchanged_no_credentials_no_collector(self, monkeypatch):
        # No collect_output() active, and no Telegram credentials configured
        # -- send() must take its existing early-return path, not append
        # anywhere or raise. Proves the refactor is a no-op by default.
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        asyncio.run(send("hello"))  # must not raise

    def test_messages_collected_instead_of_sent(self):
        async def _run():
            with collect_output() as collected:
                await send("first message")
                await send("second message", chat_id="123")
            return collected

        collected = asyncio.run(_run())
        assert collected == ["first message", "second message"]

    def test_collector_does_not_leak_outside_its_context(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

        async def _run():
            with collect_output() as collected:
                await send("inside")
            await send("outside")  # collector context has exited
            return collected

        collected = asyncio.run(_run())
        assert collected == ["inside"]  # "outside" call didn't append here

    def test_nested_collectors_are_independent(self):
        async def _run():
            with collect_output() as outer:
                await send("outer message")
                with collect_output() as inner:
                    await send("inner message")
                await send("outer again")
            return outer, inner

        outer, inner = asyncio.run(_run())
        assert outer == ["outer message", "outer again"]
        assert inner == ["inner message"]

    def test_collector_propagates_into_spawned_task(self):
        async def _run():
            with collect_output() as collected:
                async def _child():
                    await send("from a spawned task")
                await asyncio.create_task(_child())
            return collected

        collected = asyncio.run(_run())
        assert collected == ["from a spawned task"]

    def test_returns_html_formatted_text_unescaped_a_second_time(self):
        # The collected string is exactly what would have been posted to
        # Telegram (already html.escape()'d where needed by the caller) --
        # collect_output() must not re-escape or otherwise transform it.
        async def _run():
            with collect_output() as collected:
                await send("<b>NVDA</b> &amp; friends")
            return collected

        assert asyncio.run(_run()) == ["<b>NVDA</b> &amp; friends"]
