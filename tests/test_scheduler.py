import app.scheduler as scheduler
import app.config as config_mod
from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.scheduler import (
    dedupe_alerts, _alerted, _morning_trigger, run_morning_report,
    _leaps_alert_trigger, _leaps_alerted, _cheap_candidates, run_leaps_alert_check,
)


def _result(ticker: str, score: int) -> IndicatorResult:
    signals = [(f"R{i}", f"R{i}", SignalResult(signal=1 if score > 0 else -1, display="x"))
               for i in range(abs(score))]
    return IndicatorResult(ticker=ticker, price=100.0, prev_close=99.0, signals=signals)


async def _fake_get_summary(r, detailed=False) -> str:
    return ""


async def _fake_get_news_digest(tickers) -> str:
    return ""


def _leaps_cfg():
    return {
        "options": {"leaps_alert": {"iv_hv_threshold": 0.9}},
        "llm": {"leaps_max_tokens": 700},
        "scheduler": {"exchange_timezone": "America/New_York"},
    }


def _leaps_scan(ticker="NVDA", iv_hv=0.5, error=None, has_sample=True):
    from app.options.leaps import LeapsScan, LeapsCandidate
    sample = []
    if has_sample:
        sample = [LeapsCandidate(
            expiration="2027-12-17", dte=500, strike=200.0, mid=30.0, iv=0.40,
            delta=0.55, iv_hv=iv_hv, iv_hv_label="cheap" if iv_hv < 0.9 else "fair",
            open_interest=100, spread_pct=0.02, breakeven=230.0,
        )]
    return LeapsScan(ticker=ticker, spot=205.0, hv=0.35, sample=sample, error=error)


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


class TestRunMorningReportRelativeStrength:
    def _wire_common(self, monkeypatch, ranked):
        monkeypatch.setattr(scheduler, "is_trading_day", lambda d: True)
        monkeypatch.setattr(scheduler, "load_favourites", lambda: ["NVDA"])
        monkeypatch.setattr(scheduler, "analyze_tickers", lambda targets: ([_result("NVDA", 1)], []))
        monkeypatch.setattr(config_mod, "load_config", lambda: {"relative_strength": {"window_days": 20, "benchmark": "SPY"}})
        monkeypatch.setattr("app.llm.get_summary", _fake_get_summary)
        monkeypatch.setattr("app.llm.get_news_digest", _fake_get_news_digest)
        monkeypatch.setattr(scheduler, "rank_relative_strength", lambda targets, window, benchmark: ranked)

        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)
        monkeypatch.setattr(scheduler, "send", _fake_send)
        return sent

    def test_sends_relative_strength_block_when_available(self, monkeypatch):
        sent = self._wire_common(monkeypatch, ranked=[("NVDA", 0.052)])
        run_morning_report()
        assert any("Relative Strength" in m and "NVDA" in m for m in sent)

    def test_no_message_when_ranking_empty(self, monkeypatch):
        sent = self._wire_common(monkeypatch, ranked=[])
        run_morning_report()
        assert not any("Relative Strength" in m for m in sent)


class TestRunMorningReportCheapAndFallback:
    def _wire_common(self, monkeypatch, results, has_llm=False):
        monkeypatch.setattr(scheduler, "is_trading_day", lambda d: True)
        monkeypatch.setattr(scheduler, "load_favourites", lambda: [r.ticker for r in results])
        monkeypatch.setattr(scheduler, "analyze_tickers", lambda targets: (results, []))
        monkeypatch.setattr(config_mod, "load_config", lambda: {"relative_strength": {"window_days": 20, "benchmark": "SPY"}})
        monkeypatch.setattr("app.llm.get_summary", _fake_get_summary)
        monkeypatch.setattr("app.llm.get_news_digest", _fake_get_news_digest)
        monkeypatch.setattr(scheduler, "rank_relative_strength", lambda targets, window, benchmark: [])
        if has_llm:
            monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        else:
            monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)
        monkeypatch.setattr(scheduler, "send", _fake_send)
        return sent

    def _cheap_result(self):
        from app.valuation import ValuationResult, HistoricalBand
        r = _result("NVDA", 1)
        r.valuation = ValuationResult(
            ticker="NVDA", trailing_pe=31.7,
            pe_band=HistoricalBand(low=39.0, high=112.0, median=46.2, mean=60.0, stdev=30.0, n=4, label="cheap"),
            peg=0.57, peg_label="cheap", verdict="cheap",
            pe_score=15.0, peg_score=25.0, score=18.0, score_label="cheap",
        )
        return r

    def test_cheap_block_sent_when_a_favourite_reads_cheap(self, monkeypatch):
        sent = self._wire_common(monkeypatch, [self._cheap_result()])
        run_morning_report()
        assert any("Cheap Right Now" in m for m in sent)

    def test_no_cheap_block_when_nothing_cheap(self, monkeypatch):
        sent = self._wire_common(monkeypatch, [_result("NVDA", 1)])  # no valuation
        run_morning_report()
        assert not any("Cheap Right Now" in m for m in sent)

    def test_failed_summary_surfaced_when_key_set(self, monkeypatch):
        sent = self._wire_common(monkeypatch, [_result("NVDA", 1)], has_llm=True)
        run_morning_report()  # _fake_get_summary returns "" -> real failure
        assert any("AI summary unavailable" in m for m in sent)

    def test_no_failure_text_when_key_not_set(self, monkeypatch):
        sent = self._wire_common(monkeypatch, [_result("NVDA", 1)], has_llm=False)
        run_morning_report()
        assert not any("AI summary unavailable" in m for m in sent)


class TestLeapsAlertTrigger:
    def test_fires_10_30_weekdays_by_default(self):
        trigger = _leaps_alert_trigger()
        assert _cron_field(trigger, "hour") == "10"
        assert _cron_field(trigger, "minute") == "30"
        assert _cron_field(trigger, "day_of_week") == "mon-fri"


class TestCheapCandidates:
    def test_returns_candidates_below_threshold(self):
        scan = _leaps_scan(iv_hv=0.5)
        assert len(_cheap_candidates(scan, 0.9)) == 1

    def test_excludes_candidates_at_or_above_threshold(self):
        scan = _leaps_scan(iv_hv=1.2)
        assert _cheap_candidates(scan, 0.9) == []

    def test_excludes_candidates_with_unknown_iv_hv(self):
        scan = _leaps_scan(iv_hv=0.5)
        scan.sample[0].iv_hv = None
        assert _cheap_candidates(scan, 0.9) == []


class TestRunLeapsAlertCheck:
    def setup_method(self):
        _leaps_alerted.clear()

    def _wire_common(self, monkeypatch, scan, has_llm=False):
        monkeypatch.setattr(scheduler, "is_trading_day", lambda d: True)
        monkeypatch.setattr(scheduler, "load_favourites", lambda: ["NVDA"])
        monkeypatch.setattr(config_mod, "load_config", _leaps_cfg)
        monkeypatch.setattr(scheduler, "scan_leaps", lambda ticker: scan)
        if has_llm:
            monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        else:
            monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)
        monkeypatch.setattr(scheduler, "send", _fake_send)
        return sent

    def test_skips_on_non_trading_day(self, monkeypatch):
        monkeypatch.setattr(scheduler, "is_trading_day", lambda d: False)

        def _boom(*a, **kw):
            raise AssertionError("should not scan on a non-trading day")
        monkeypatch.setattr(scheduler, "scan_leaps", _boom)
        monkeypatch.setattr(scheduler, "load_favourites", _boom)

        run_leaps_alert_check()

    def test_skips_when_no_favourites(self, monkeypatch):
        monkeypatch.setattr(scheduler, "is_trading_day", lambda d: True)
        monkeypatch.setattr(scheduler, "load_favourites", lambda: [])

        def _boom(*a, **kw):
            raise AssertionError("should not scan with no favourites set")
        monkeypatch.setattr(scheduler, "scan_leaps", _boom)

        run_leaps_alert_check()

    def test_alerts_when_cheap_candidate_found(self, monkeypatch):
        sent = self._wire_common(monkeypatch, _leaps_scan(iv_hv=0.5))
        run_leaps_alert_check()
        assert any("Cheap LEAPS Alert" in m and "NVDA" in m for m in sent)

    def test_no_alert_when_nothing_cheap(self, monkeypatch):
        sent = self._wire_common(monkeypatch, _leaps_scan(iv_hv=1.2))
        run_leaps_alert_check()
        assert sent == []

    def test_no_alert_on_scan_error(self, monkeypatch):
        sent = self._wire_common(monkeypatch, _leaps_scan(iv_hv=0.5, error="no options chain available"))
        run_leaps_alert_check()
        assert sent == []

    def test_no_alert_when_sample_empty(self, monkeypatch):
        sent = self._wire_common(monkeypatch, _leaps_scan(has_sample=False))
        run_leaps_alert_check()
        assert sent == []

    def test_does_not_refire_same_ticker_same_day(self, monkeypatch):
        sent = self._wire_common(monkeypatch, _leaps_scan(iv_hv=0.5))
        run_leaps_alert_check()
        run_leaps_alert_check()
        assert len([m for m in sent if "Cheap LEAPS Alert" in m]) == 1

    def test_refires_on_a_new_day(self, monkeypatch):
        sent = self._wire_common(monkeypatch, _leaps_scan(iv_hv=0.5))
        run_leaps_alert_check()
        _leaps_alerted["NVDA"] = "2000-01-01"  # simulate a stale prior day
        run_leaps_alert_check()
        assert len([m for m in sent if "Cheap LEAPS Alert" in m]) == 2

    def test_one_ticker_failure_does_not_block_the_rest(self, monkeypatch):
        monkeypatch.setattr(scheduler, "is_trading_day", lambda d: True)
        monkeypatch.setattr(scheduler, "load_favourites", lambda: ["BROKEN", "NVDA"])
        monkeypatch.setattr(config_mod, "load_config", _leaps_cfg)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        def _scan(ticker):
            if ticker == "BROKEN":
                raise RuntimeError("yfinance blew up")
            return _leaps_scan(ticker=ticker, iv_hv=0.5)
        monkeypatch.setattr(scheduler, "scan_leaps", _scan)

        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)
        monkeypatch.setattr(scheduler, "send", _fake_send)

        run_leaps_alert_check()  # must not raise, and must still alert on NVDA
        assert any("NVDA" in m for m in sent)

    def test_skips_llm_call_when_no_api_key(self, monkeypatch):
        sent = self._wire_common(monkeypatch, _leaps_scan(iv_hv=0.5), has_llm=False)

        def _boom(*a, **kw):
            raise AssertionError("should not call openrouter_chat without an API key")
        monkeypatch.setattr(scheduler, "openrouter_chat", _boom)

        run_leaps_alert_check()
        assert any("Cheap LEAPS Alert" in m for m in sent)
