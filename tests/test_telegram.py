from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.telegram import _call, build_priority_alert, build_stock_messages, signal_line, split_message


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
