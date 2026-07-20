import app.scheduler as scheduler
from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.scheduler import dedupe_alerts, _alerted, _morning_trigger, run_morning_report


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


def _cron_field(trigger, name: str) -> str:
    return str(next(f for f in trigger.fields if f.name == name))


class TestMorningTrigger:
    def test_fires_30min_after_open_weekdays_only(self):
        trigger = _morning_trigger()
        assert _cron_field(trigger, "hour") == "10"
        assert _cron_field(trigger, "minute") == "0"
        assert _cron_field(trigger, "day_of_week") == "mon-fri"


class TestRunMorningReport:
    def test_skips_on_non_trading_day(self, monkeypatch):
        monkeypatch.setattr(scheduler, "is_trading_day", lambda d: False)

        def _boom(*a, **kw):
            raise AssertionError("should not analyze on a non-trading day")
        monkeypatch.setattr(scheduler, "analyze_tickers", _boom)

        run_morning_report()  # must return before touching analyze_tickers

    def test_skips_when_no_favourites(self, monkeypatch):
        monkeypatch.setattr(scheduler, "is_trading_day", lambda d: True)
        monkeypatch.setattr(scheduler, "load_favourites", lambda: [])

        def _boom(*a, **kw):
            raise AssertionError("should not analyze with no favourites set")
        monkeypatch.setattr(scheduler, "analyze_tickers", _boom)

        run_morning_report()
