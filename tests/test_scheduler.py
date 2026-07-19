from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.scheduler import dedupe_alerts, _alerted


def _result(ticker: str, score: int) -> IndicatorResult:
    signals = [(f"R{i}", f"R{i}", SignalResult(signal=1 if score > 0 else -1, display="x"))
               for i in range(abs(score))]
    return IndicatorResult(ticker=ticker, price=100.0, prev_close=99.0, signals=signals)


class TestDedupeAlerts:
    def setup_method(self):
        _alerted.clear()

    def test_first_alert_passes_repeat_suppressed(self):
        a = _result("NVDA", 2)
        assert dedupe_alerts([a], "2026-07-20") == [a]
        assert dedupe_alerts([a], "2026-07-20") == []

    def test_new_day_alerts_again(self):
        a = _result("NVDA", 2)
        dedupe_alerts([a], "2026-07-20")
        assert dedupe_alerts([a], "2026-07-21") == [a]

    def test_opposite_side_not_suppressed(self):
        dedupe_alerts([_result("NVDA", 2)], "2026-07-20")
        sell = _result("NVDA", -2)
        assert dedupe_alerts([sell], "2026-07-20") == [sell]

    def test_different_tickers_independent(self):
        dedupe_alerts([_result("NVDA", 2)], "2026-07-20")
        other = _result("META", 2)
        assert dedupe_alerts([other], "2026-07-20") == [other]
