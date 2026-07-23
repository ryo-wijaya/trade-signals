import pytest
from fastapi.testclient import TestClient

import app.web_ui as web_ui_mod
import main


@pytest.fixture
def client(monkeypatch):
    # Plain instantiation (no `with`) never triggers main.app's lifespan, so
    # the real scheduler/Telegram polling loop never starts during tests.
    # base_url must be https:// -- the session cookie is Secure (correctly,
    # since Fly.io terminates real TLS in production), and a Secure cookie is
    # silently dropped by the client over the default plain-http testserver.
    web_ui_mod._sessions.clear()
    monkeypatch.setenv("WEB_UI_PASSWORD", "correct-horse-battery-staple")
    monkeypatch.setattr(web_ui_mod, "_FAILED_LOGIN_DELAY_SECONDS", 0)
    return TestClient(main.app, base_url="https://testserver")


def _login(client) -> None:
    resp = client.post("/ui/login", data={"password": "correct-horse-battery-staple"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "session" in resp.cookies


class TestLoginPage:
    def test_get_login_page_shows_password_field(self, client):
        resp = client.get("/ui/login")
        assert resp.status_code == 200
        assert "password" in resp.text

    def test_get_login_page_redirects_if_already_authenticated(self, client):
        _login(client)
        resp = client.get("/ui/login", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui"


class TestLoginSubmit:
    def test_correct_password_sets_cookie_and_redirects(self, client):
        resp = client.post("/ui/login", data={"password": "correct-horse-battery-staple"}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui"
        assert "session" in resp.cookies

    def test_wrong_password_is_rejected(self, client):
        resp = client.post("/ui/login", data={"password": "guess"}, follow_redirects=False)
        assert resp.status_code == 401
        assert "Wrong password" in resp.text
        assert "session" not in resp.cookies

    def test_empty_password_against_unset_secret_is_rejected(self, client, monkeypatch):
        monkeypatch.delenv("WEB_UI_PASSWORD", raising=False)
        resp = client.post("/ui/login", data={"password": ""}, follow_redirects=False)
        assert resp.status_code == 401

    def test_wrong_password_does_not_create_a_session(self, client):
        client.post("/ui/login", data={"password": "guess"}, follow_redirects=False)
        assert web_ui_mod._sessions == {}


class TestDashboardAuth:
    def test_no_cookie_redirects_to_login(self, client):
        resp = client.get("/ui/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/login"

    def test_valid_cookie_shows_dashboard(self, client):
        _login(client)
        resp = client.get("/ui/")
        assert resp.status_code == 200
        assert "/help" in resp.text
        assert "/signals" in resp.text

    def test_garbage_cookie_redirects_to_login(self, client):
        client.cookies.set("session", "not-a-real-token")
        resp = client.get("/ui/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/login"


class TestLogout:
    def test_logout_clears_session_and_further_requests_need_login(self, client):
        _login(client)
        assert client.get("/ui/").status_code == 200

        resp = client.post("/ui/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/login"

        resp = client.get("/ui/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/login"


class TestTrigger:
    def test_requires_auth(self, client):
        resp = client.post("/ui/trigger", data={"cmd": "help", "args": ""}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/login"

    def test_runs_help_and_shows_collected_output(self, client):
        _login(client)
        resp = client.post("/ui/trigger", data={"cmd": "help", "args": ""})
        assert resp.status_code == 200
        assert "Trade Signals Bot" in resp.text  # from build_help()'s own header
        assert "/signals" in resp.text

    def test_unknown_command_shows_unknown_message_not_an_error(self, client):
        _login(client)
        resp = client.post("/ui/trigger", data={"cmd": "notarealcommand", "args": ""})
        assert resp.status_code == 200
        assert "Unknown command" in resp.text

    def test_args_are_uppercased_like_the_telegram_bot_does(self, monkeypatch, client):
        _login(client)
        captured = {}

        async def _fake_dispatch(cmd, args, chat_id):
            captured["cmd"] = cmd
            captured["args"] = args
            captured["chat_id"] = chat_id

        monkeypatch.setattr(web_ui_mod, "dispatch", _fake_dispatch)
        client.post("/ui/trigger", data={"cmd": "signals", "args": "nvda crm"})
        assert captured["cmd"] == "signals"
        assert captured["args"] == ["NVDA", "CRM"]
        assert captured["chat_id"] == "web-ui"

    def test_malicious_arg_is_stripped_before_reaching_dispatch(self, monkeypatch, client):
        _login(client)
        captured = {}

        async def _fake_dispatch(cmd, args, chat_id):
            captured["args"] = args

        monkeypatch.setattr(web_ui_mod, "dispatch", _fake_dispatch)
        client.post("/ui/trigger", data={"cmd": "signals", "args": "NVDA <script>alert(1)</script>"})
        assert captured["args"] == ["NVDA"]

    def test_blank_args_becomes_empty_list(self, monkeypatch, client):
        _login(client)
        captured = {}

        async def _fake_dispatch(cmd, args, chat_id):
            captured["args"] = args

        monkeypatch.setattr(web_ui_mod, "dispatch", _fake_dispatch)
        client.post("/ui/trigger", data={"cmd": "signals", "args": "  "})
        assert captured["args"] == []


class TestNoLifespanDuringTests:
    def test_scheduler_and_bot_not_started_by_plain_testclient(self, client):
        # Sanity check on the test setup itself: plain TestClient() instantiation
        # (no `with`) must not have triggered main.py's lifespan, or these HTTP
        # tests would be starting a real scheduler + Telegram polling loop.
        import app.scheduler as scheduler_mod
        assert scheduler_mod._scheduler is None
