import asyncio

import pytest

from app.commands.registry import command, dispatch, _registry


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_registry)
    yield
    _registry.clear()
    _registry.update(saved)


class TestDispatch:
    def test_calls_registered_handler_with_args_and_chat_id(self):
        seen = {}

        @command("mycmd")
        async def _handler(args, chat_id):
            seen["args"] = args
            seen["chat_id"] = chat_id

        asyncio.run(dispatch("mycmd", ["AAPL"], "123"))
        assert seen == {"args": ["AAPL"], "chat_id": "123"}

    def test_unknown_command_sends_help_pointer(self, monkeypatch):
        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)
        monkeypatch.setattr("app.telegram.send", _fake_send)

        asyncio.run(dispatch("doesnotexist", [], "123"))
        assert any("Unknown command" in m for m in sent)

    def test_handler_exception_is_caught_and_reported_to_user(self, monkeypatch):
        @command("boom")
        async def _handler(args, chat_id):
            raise RuntimeError("kaboom")

        sent = []

        async def _fake_send(msg, chat_id=None):
            sent.append(msg)
        monkeypatch.setattr("app.telegram.send", _fake_send)

        asyncio.run(dispatch("boom", [], "123"))  # must not raise
        assert any("/boom failed unexpectedly" in m for m in sent)

    def test_handler_exception_does_not_propagate(self, monkeypatch):
        @command("boom2")
        async def _handler(args, chat_id):
            raise ValueError("bad state")

        async def _fake_send(msg, chat_id=None):
            return None
        monkeypatch.setattr("app.telegram.send", _fake_send)

        asyncio.run(dispatch("boom2", [], "123"))  # should complete without raising
