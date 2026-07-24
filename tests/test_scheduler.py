import asyncio

import app.scheduler as scheduler
import app.config as config_mod
from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.scheduler import (
    _morning_trigger, run_morning_report,
    _leaps_alert_trigger, _leaps_alerted, _cheap_candidates, run_leaps_alert_check,
    meets_action_criteria, action_candidates, _action_resolved, run_action_alert_check,
)
from app.valuation import ValuationResult


def _result(ticker: str, score: int, valuation=None, fundamentals=None, rules_passed=True) -> IndicatorResult:
    signals = [(f"R{i}", f"R{i}", SignalResult(signal=1 if score > 0 else -1, display="x"))
               for i in range(abs(score))]
    return IndicatorResult(ticker=ticker, price=100.0, prev_close=99.0, signals=signals,
                            valuation=valuation, fundamentals=fundamentals or {}, rules_passed=rules_passed)


def _valuation(score_label: str, score: float = 50.0) -> ValuationResult:
    return ValuationResult(ticker="X", verdict="cheap", score=score, score_label=score_label)


def _good_fundamentals(**overrides) -> dict:
    defaults = {"revenue_growth": 0.10, "earnings_growth": 0.10, "recommendation": "buy"}
    defaults.update(overrides)
    return defaults


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


class TestMeetsActionCriteria:
    def test_all_criteria_pass(self):
        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        assert meets_action_criteria(r, min_signals=2) is True

    def test_valuation_not_cheap_fails(self):
        r = _result("NVDA", 2, _valuation("fair"), _good_fundamentals())
        assert meets_action_criteria(r, min_signals=2) is False

    def test_no_valuation_fails(self):
        r = _result("NVDA", 2, valuation=None, fundamentals=_good_fundamentals())
        assert meets_action_criteria(r, min_signals=2) is False

    def test_score_below_threshold_fails(self):
        r = _result("NVDA", 1, _valuation("cheap"), _good_fundamentals())
        assert meets_action_criteria(r, min_signals=2) is False

    def test_unconfirmed_bounce_fails(self):
        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals(), rules_passed=False)
        assert meets_action_criteria(r, min_signals=2) is False

    def test_sell_side_score_never_qualifies(self):
        r = _result("NVDA", -2, _valuation("cheap"), _good_fundamentals())
        assert meets_action_criteria(r, min_signals=2) is False

    def test_missing_growth_data_fails(self):
        r = _result("NVDA", 2, _valuation("cheap"), {"recommendation": "buy"})
        assert meets_action_criteria(r, min_signals=2) is False

    def test_negative_growth_fails(self):
        bad = _good_fundamentals(revenue_growth=-0.05)
        r = _result("NVDA", 2, _valuation("cheap"), bad)
        assert meets_action_criteria(r, min_signals=2) is False

    def test_weak_analyst_consensus_fails(self):
        bad = _good_fundamentals(recommendation="hold")
        r = _result("NVDA", 2, _valuation("cheap"), bad)
        assert meets_action_criteria(r, min_signals=2) is False

    def test_custom_min_growth_threshold_applied(self):
        modest = _good_fundamentals(revenue_growth=0.02, earnings_growth=0.02)
        r = _result("NVDA", 2, _valuation("cheap"), modest)
        assert meets_action_criteria(r, min_signals=2, min_growth=0.05) is False
        assert meets_action_criteria(r, min_signals=2, min_growth=0.01) is True

    def test_custom_analyst_labels_applied(self):
        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals(recommendation="strong_buy"))
        assert meets_action_criteria(r, min_signals=2, analyst_labels={"buy"}) is False
        assert meets_action_criteria(r, min_signals=2, analyst_labels={"strong_buy"}) is True


class TestActionCandidates:
    def setup_method(self):
        _action_resolved.clear()

    def test_first_qualifying_sighting_is_candidate(self):
        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        assert action_candidates([r], min_signals=2) == [r]

    def test_already_resolved_ticker_excluded(self):
        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        _action_resolved["NVDA"] = True
        assert action_candidates([r], min_signals=2) == []

    def test_dropping_out_of_criteria_clears_resolved_flag(self):
        _action_resolved["NVDA"] = True
        fair = _result("NVDA", 2, _valuation("fair"), _good_fundamentals())
        action_candidates([fair], min_signals=2)
        assert "NVDA" not in _action_resolved

    def test_re_qualifying_after_drop_out_is_a_fresh_candidate(self):
        _action_resolved["NVDA"] = True
        fair = _result("NVDA", 2, _valuation("fair"), _good_fundamentals())
        action_candidates([fair], min_signals=2)  # drops out, clears the resolved flag
        cheap = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        assert action_candidates([cheap], min_signals=2) == [cheap]

    def test_non_qualifying_never_a_candidate(self):
        r = _result("NVDA", 0, _valuation("fair"), _good_fundamentals())
        assert action_candidates([r], min_signals=2) == []


class TestConfirmAiOutlook:
    def test_no_api_key_degrades_to_passed(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        assert asyncio.run(scheduler._confirm_ai_outlook(r)) == (True, True, "")

    def test_ai_says_buy_passes(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setattr("app.config.load_config", lambda: {"llm": {"detailed_max_tokens": 320}})

        async def _fake_chat(prompt, max_tokens):
            return "BUY\n\nGreat setup across the board."
        monkeypatch.setattr("app.llm.openrouter_chat", _fake_chat)

        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        resolved, passed, reason = asyncio.run(scheduler._confirm_ai_outlook(r))
        assert resolved is True
        assert passed is True
        assert "Great setup" in reason

    def test_ai_says_hold_vetoes(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setattr("app.config.load_config", lambda: {"llm": {"detailed_max_tokens": 320}})

        async def _fake_chat(prompt, max_tokens):
            return "HOLD\n\nMixed signals."
        monkeypatch.setattr("app.llm.openrouter_chat", _fake_chat)

        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        resolved, passed, reason = asyncio.run(scheduler._confirm_ai_outlook(r))
        assert resolved is True
        assert passed is False

    def test_empty_reply_is_unresolved_not_vetoed(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setattr("app.config.load_config", lambda: {"llm": {"detailed_max_tokens": 320}})

        async def _fake_chat(prompt, max_tokens):
            return ""
        monkeypatch.setattr("app.llm.openrouter_chat", _fake_chat)

        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        resolved, _, _ = asyncio.run(scheduler._confirm_ai_outlook(r))
        assert resolved is False

    def test_exception_is_unresolved_not_vetoed(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setattr("app.config.load_config", lambda: {"llm": {"detailed_max_tokens": 320}})

        async def _fake_chat(prompt, max_tokens):
            raise RuntimeError("network down")
        monkeypatch.setattr("app.llm.openrouter_chat", _fake_chat)

        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        resolved, _, _ = asyncio.run(scheduler._confirm_ai_outlook(r))
        assert resolved is False


def _cron_field(trigger, name: str) -> str:
    return str(next(f for f in trigger.fields if f.name == name))


class TestRunActionAlertCheck:
    def setup_method(self):
        _action_resolved.clear()

    def _wire(self, monkeypatch, results, ai_outcome=(True, True, "BUY\n\nAI agrees.")):
        monkeypatch.setattr(scheduler, "is_trading_day", lambda d: True)
        monkeypatch.setattr(scheduler, "collect_results", lambda: (results, []))

        async def _fake_confirm(r):
            return ai_outcome
        monkeypatch.setattr(scheduler, "_confirm_ai_outlook", _fake_confirm)

        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)
        monkeypatch.setattr(scheduler, "send", _fake_send)
        return sent

    def test_skips_on_non_trading_day(self, monkeypatch):
        monkeypatch.setattr(scheduler, "is_trading_day", lambda d: False)

        def _boom():
            raise AssertionError("should not collect results on a non-trading day")
        monkeypatch.setattr(scheduler, "collect_results", _boom)

        run_action_alert_check()

    def test_no_candidates_sends_nothing(self, monkeypatch):
        r = _result("NVDA", 0, _valuation("fair"), _good_fundamentals())
        sent = self._wire(monkeypatch, [r])
        run_action_alert_check()
        assert sent == []

    def test_full_candidate_confirmed_by_ai_sends_action_alert(self, monkeypatch):
        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        sent = self._wire(monkeypatch, [r], ai_outcome=(True, True, "BUY\n\nAll signals align."))
        run_action_alert_check()
        assert any("ACTION ALERT" in m and "NVDA" in m for m in sent)

    def test_ai_veto_sends_nothing(self, monkeypatch):
        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        sent = self._wire(monkeypatch, [r], ai_outcome=(True, False, "HOLD\n\nMixed."))
        run_action_alert_check()
        assert sent == []

    def test_unresolved_ai_check_does_not_send_and_leaves_ticker_unresolved(self, monkeypatch):
        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        sent = self._wire(monkeypatch, [r], ai_outcome=(False, False, ""))
        run_action_alert_check()
        assert sent == []
        assert "NVDA" not in _action_resolved

    def test_does_not_refire_same_qualifying_streak(self, monkeypatch):
        r = _result("NVDA", 2, _valuation("cheap"), _good_fundamentals())
        sent = self._wire(monkeypatch, [r], ai_outcome=(True, True, "BUY\n\nGood."))
        run_action_alert_check()
        run_action_alert_check()
        assert len([m for m in sent if "ACTION ALERT" in m]) == 1

    def test_missing_any_single_criterion_blocks_alert_entirely(self, monkeypatch):
        # cheap + oversold + confirmed + growth all pass, but consensus is
        # only "hold" -- the whole point is that ONE weak leg blocks it.
        bad_fundamentals = _good_fundamentals(recommendation="hold")
        r = _result("NVDA", 2, _valuation("cheap"), bad_fundamentals)
        sent = self._wire(monkeypatch, [r])
        run_action_alert_check()
        assert sent == []


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
