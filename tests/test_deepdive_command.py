import asyncio

import app.commands.deepdive as deepdive_mod
from app.commands.deepdive import handle_deepdive
from app.indicators.base import SignalResult
from app.indicators.engine import IndicatorResult
from app.options.snapshot import OptionsSnapshot


def _result(ticker: str) -> IndicatorResult:
    signals = [("BB", "Bollinger", SignalResult(signal=0, display="x"))]
    return IndicatorResult(ticker=ticker, price=100.0, prev_close=99.0, signals=signals)


def _snapshot(**overrides) -> OptionsSnapshot:
    defaults = dict(ticker="NVDA", spot=100.0, expiration="2026-08-28", dte=36,
                     hv=0.30, atm_iv=0.35, iv_hv=1.17, iv_hv_label="fair")
    defaults.update(overrides)
    return OptionsSnapshot(**defaults)


class TestMarketHoursCaveatIntegration:
    def _wire(self, monkeypatch, snapshot, market_open):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setattr(deepdive_mod, "load_favourites", lambda: ["NVDA"])
        monkeypatch.setattr(deepdive_mod, "analyze_tickers", lambda tickers: ([_result("NVDA")], []))
        monkeypatch.setattr(deepdive_mod, "scan_snapshot", lambda ticker: snapshot)
        monkeypatch.setattr(deepdive_mod, "market_hours_caveat",
                             lambda: "" if market_open else "Note: US markets are closed right now.")

        async def _fake_openrouter_chat(prompt, max_tokens, timeout=60):
            return "HOLD — nothing decisive."
        monkeypatch.setattr(deepdive_mod, "openrouter_chat", _fake_openrouter_chat)

        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)
        monkeypatch.setattr(deepdive_mod, "send", _fake_send)
        return sent

    def test_caveat_shown_when_snapshot_missing_and_market_closed(self, monkeypatch):
        sent = self._wire(monkeypatch, _snapshot(atm_iv=None, error="no options chain available"), market_open=False)
        asyncio.run(handle_deepdive([], "123"))
        assert any("US markets are closed" in m for m in sent)

    def test_no_caveat_when_snapshot_missing_but_market_open(self, monkeypatch):
        sent = self._wire(monkeypatch, _snapshot(atm_iv=None, error="no options chain available"), market_open=True)
        asyncio.run(handle_deepdive([], "123"))
        assert not any("US markets are closed" in m for m in sent)

    def test_no_caveat_when_snapshot_has_usable_data_even_if_market_closed(self, monkeypatch):
        sent = self._wire(monkeypatch, _snapshot(), market_open=False)
        asyncio.run(handle_deepdive([], "123"))
        assert not any("US markets are closed" in m for m in sent)

    def test_no_caveat_when_snapshot_is_none_and_market_closed(self, monkeypatch):
        sent = self._wire(monkeypatch, None, market_open=False)
        asyncio.run(handle_deepdive([], "123"))
        assert any("US markets are closed" in m for m in sent)
