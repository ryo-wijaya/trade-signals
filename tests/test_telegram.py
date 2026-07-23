import asyncio

from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.telegram import (
    _call, build_priority_alert, build_stock_messages, signal_line, split_message,
    send, collect_output,
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
