import asyncio

import app.commands.opc as opc_mod
from app.commands.opc import render_opc, handle_opc
from app.options.profit_calc import OpcResult, OpcCell


def _result(**overrides) -> OpcResult:
    defaults = dict(
        ticker="NVDA", spot=200.0, strike=200.0, option_type="call",
        expiration="2026-09-18", dte=60, iv=0.45, hv=0.39, iv_hv=1.15, iv_hv_label="fair",
        premium=15.0, contracts=1,
        breakeven=215.0, max_loss_per_contract=1500.0,
        checkpoints=[0, 30, 60],
        price_pcts=[-0.1, 0.0, 0.1],
        grid={
            (0, -0.1): OpcCell(value=8.0, pl_per_contract=-700.0, pl_pct=-0.4667),
            (0, 0.0): OpcCell(value=15.0, pl_per_contract=0.0, pl_pct=0.0),
            (0, 0.1): OpcCell(value=24.0, pl_per_contract=900.0, pl_pct=0.6),
            (30, -0.1): OpcCell(value=5.0, pl_per_contract=-1000.0, pl_pct=-0.6667),
            (30, 0.0): OpcCell(value=12.0, pl_per_contract=-300.0, pl_pct=-0.2),
            (30, 0.1): OpcCell(value=22.0, pl_per_contract=700.0, pl_pct=0.4667),
            (60, -0.1): OpcCell(value=0.0, pl_per_contract=-1500.0, pl_pct=-1.0),
            (60, 0.0): OpcCell(value=0.0, pl_per_contract=-1500.0, pl_pct=-1.0),
            (60, 0.1): OpcCell(value=20.0, pl_per_contract=500.0, pl_pct=0.3333),
        },
    )
    defaults.update(overrides)
    return OpcResult(**defaults)


class TestRenderOpc:
    def test_error_short_circuits_rendering(self):
        r = OpcResult(ticker="BADTICKER", error="no options chain available")
        body = render_opc(r)
        assert "BADTICKER" in body
        assert "no options chain available" in body

    def test_header_shows_ticker_spot_strike_and_expiration(self):
        body = render_opc(_result())
        assert "NVDA" in body
        assert "$200.00" in body
        assert "$200C" in body
        assert "2026-09-18" in body

    def test_put_option_labeled_p(self):
        body = render_opc(_result(option_type="put"))
        assert "$200P" in body

    def test_premium_iv_ivhv_and_breakeven_shown(self):
        body = render_opc(_result())
        assert "$15.00" in body
        assert "45%" in body
        assert "1.15 fair" in body
        assert "$215.00" in body
        assert "$1500" in body

    def test_unknown_iv_hv_shown_without_crashing(self):
        body = render_opc(_result(iv_hv=None, iv_hv_label="unknown"))
        assert "unknown" in body

    def test_no_raw_grid_table_in_deterministic_render(self):
        # The old big price/date grid was replaced with an AI summary
        # (appended separately by handle_opc) -- render_opc itself should
        # only show the slim data header now.
        body = render_opc(_result())
        assert "<code>" not in body
        assert "today" not in body


def _async_return(value):
    async def _inner(*a, **kw):
        return value
    return _inner


class TestHandleOpc:
    def _wire(self, monkeypatch, result, ai_summary="TRADE — cheap IV and a supportive setup."):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)
        monkeypatch.setattr(opc_mod, "send", _fake_send)

        captured = {}

        def _fake_compute_sync(ticker, strike, option_type, expiration, premium_override):
            captured.update(ticker=ticker, strike=strike, option_type=option_type,
                             expiration=expiration, premium_override=premium_override)
            return result
        monkeypatch.setattr(opc_mod, "compute_opc", _fake_compute_sync)
        monkeypatch.setattr(opc_mod, "openrouter_chat", _async_return(ai_summary))
        monkeypatch.setattr(opc_mod, "load_config", lambda: {"llm": {"opc_max_tokens": 400}})
        return sent, captured

    def test_missing_args_shows_usage(self, monkeypatch):
        sent, _ = self._wire(monkeypatch, _result())
        asyncio.run(handle_opc(["NVDA", "200", "CALL"], "123"))
        assert any("Usage: /opc" in m for m in sent)

    def test_no_api_key_blocks_with_message(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)
        monkeypatch.setattr(opc_mod, "send", _fake_send)

        def _boom(*a, **kw):
            raise AssertionError("should not compute without an API key")
        monkeypatch.setattr(opc_mod, "compute_opc", _boom)

        asyncio.run(handle_opc(["NVDA", "200", "CALL", "2026-09-18"], "123"))
        assert any("OPENROUTER_API_KEY is not set" in m for m in sent)

    def test_valid_args_dispatches_to_compute_opc(self, monkeypatch):
        sent, captured = self._wire(monkeypatch, _result())
        asyncio.run(handle_opc(["NVDA", "200", "CALL", "2026-09-18"], "123"))
        assert captured["ticker"] == "NVDA"
        assert captured["strike"] == 200.0
        assert captured["option_type"] == "call"
        assert captured["expiration"] == "2026-09-18"
        assert captured["premium_override"] is None
        assert any("NVDA" in m and "$200.00" in m for m in sent)

    def test_ai_summary_appended_with_bolded_verdict(self, monkeypatch):
        sent, _ = self._wire(monkeypatch, _result(), ai_summary="TRADE — cheap IV and a supportive setup.")
        asyncio.run(handle_opc(["NVDA", "200", "CALL", "2026-09-18"], "123"))
        assert any("<b>TRADE — cheap IV and a supportive setup.</b>" in m for m in sent)

    def test_put_side_accepted(self, monkeypatch):
        sent, captured = self._wire(monkeypatch, _result(option_type="put"))
        asyncio.run(handle_opc(["NVDA", "200", "PUT", "2026-09-18"], "123"))
        assert captured["option_type"] == "put"

    def test_premium_override_parsed(self, monkeypatch):
        sent, captured = self._wire(monkeypatch, _result())
        asyncio.run(handle_opc(["NVDA", "200", "CALL", "2026-09-18", "12.50"], "123"))
        assert captured["premium_override"] == 12.50

    def test_invalid_strike_rejected(self, monkeypatch):
        sent, _ = self._wire(monkeypatch, _result())
        asyncio.run(handle_opc(["NVDA", "notanumber", "CALL", "2026-09-18"], "123"))
        assert any("Invalid strike" in m for m in sent)

    def test_invalid_option_type_rejected(self, monkeypatch):
        sent, _ = self._wire(monkeypatch, _result())
        asyncio.run(handle_opc(["NVDA", "200", "BANANA", "2026-09-18"], "123"))
        assert any("Option type must be" in m for m in sent)

    def test_invalid_premium_rejected(self, monkeypatch):
        sent, _ = self._wire(monkeypatch, _result())
        asyncio.run(handle_opc(["NVDA", "200", "CALL", "2026-09-18", "notanumber"], "123"))
        assert any("Invalid premium" in m for m in sent)

    def test_result_error_surfaced_in_output_no_ai_call(self, monkeypatch):
        def _boom(*a, **kw):
            raise AssertionError("should not call the AI when compute_opc errored")

        sent, _ = self._wire(monkeypatch, OpcResult(ticker="NVDA", error="no options chain available"))
        monkeypatch.setattr(opc_mod, "openrouter_chat", _boom)
        asyncio.run(handle_opc(["NVDA", "200", "CALL", "2026-09-18"], "123"))
        assert any("no options chain available" in m for m in sent)

    def test_empty_ai_summary_shows_unavailable_message(self, monkeypatch):
        sent, _ = self._wire(monkeypatch, _result(), ai_summary="")
        asyncio.run(handle_opc(["NVDA", "200", "CALL", "2026-09-18"], "123"))
        assert any("AI analysis unavailable" in m for m in sent)

    def test_ai_call_exception_shows_unavailable_message(self, monkeypatch):
        async def _boom(*a, **kw):
            raise RuntimeError("network down")

        sent, _ = self._wire(monkeypatch, _result())
        monkeypatch.setattr(opc_mod, "openrouter_chat", _boom)
        asyncio.run(handle_opc(["NVDA", "200", "CALL", "2026-09-18"], "123"))
        assert any("AI analysis unavailable" in m for m in sent)
