import asyncio
import html
import logging
import os
import secrets
import time

from fastapi import APIRouter, Cookie, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from app.commands.registry import _registry, dispatch
from app.config import sanitize_tickers
from app.telegram import collect_output

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui")

_SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_FAILED_LOGIN_DELAY_SECONDS = 1.0  # simple brute-force throttle, no IP tracking needed
_COOKIE_NAME = "session"

# In-memory only — sessions don't survive a redeploy/restart, which is a
# fine tradeoff for a single-user personal tool with no database. The chat_id
# threaded through to dispatch() is a placeholder: send() never reaches the
# Telegram-specific code that would use it while collect_output() is active.
_sessions: dict[str, float] = {}
_WEB_CHAT_ID = "web-ui"


def _is_authenticated(session: str | None) -> bool:
    if not session:
        return False
    expiry = _sessions.get(session)
    return expiry is not None and expiry > time.time()


def _page(title: str, body: str) -> str:
    return f"""<title>{html.escape(title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ color-scheme: dark; }}
  body {{ background: #0f1115; color: #e6e6e6; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 780px; margin: 0 auto; padding: 24px 16px 64px; }}
  h1 {{ font-size: 1.3rem; }}
  a {{ color: #7ab8ff; }}
  input[type=password], input[type=text] {{
    background: #1a1d24; border: 1px solid #333; color: #e6e6e6; border-radius: 6px;
    padding: 8px 10px; font-size: 0.95rem;
  }}
  button {{
    background: #2563eb; color: white; border: none; border-radius: 6px;
    padding: 8px 14px; font-size: 0.95rem; cursor: pointer;
  }}
  button:hover {{ background: #1d4ed8; }}
  form.row {{ display: flex; gap: 8px; align-items: center; padding: 10px 0; border-bottom: 1px solid #23262e; }}
  form.row .name {{ font-weight: 600; min-width: 130px; }}
  form.row .desc {{ color: #9aa0aa; font-size: 0.85rem; flex: 1; }}
  form.row input[type=text] {{ width: 160px; }}
  .msg {{ background: #1a1d24; border: 1px solid #262a33; border-radius: 8px; padding: 14px 16px; margin: 14px 0;
          white-space: pre-wrap; word-wrap: break-word; }}
  code {{ background: rgba(255,255,255,0.06); padding: 1px 4px; border-radius: 4px; }}
  .top {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; gap: 12px; }}
  .hint {{ color: #9aa0aa; font-size: 0.85rem; }}
</style>
{body}
"""


def _login_page(error: str = "") -> str:
    err = f'<p style="color:#f87171">{html.escape(error)}</p>' if error else ""
    body = f"""
<h1>Trade Signals</h1>
{err}
<form method="post" action="/ui/login">
  <input type="password" name="password" placeholder="Password" autofocus>
  <button type="submit">Log in</button>
</form>
"""
    return _page("Log in", body)


def _dashboard_page() -> str:
    seen = set()
    rows = []
    for name, (fn, desc) in _registry.items():
        if not desc or fn in seen:
            continue
        seen.add(fn)
        rows.append(f"""
<form class="row" method="post" action="/ui/trigger">
  <input type="hidden" name="cmd" value="{html.escape(name)}">
  <span class="name">/{html.escape(name)}</span>
  <span class="desc">{html.escape(desc)}</span>
  <input type="text" name="args" placeholder="args (optional)">
  <button type="submit">Run</button>
</form>""")
    body = f"""
<div class="top">
  <h1>Trade Signals</h1>
  <form method="post" action="/ui/logout"><button type="submit">Log out</button></form>
</div>
<p class="hint">Args are space-separated, exactly like typing them after the command in Telegram
(e.g. <code>NVDA CRM</code> or <code>fav</code>). Leave blank for each command's own default.</p>
{''.join(rows)}
"""
    return _page("Dashboard", body)


def _result_page(cmd: str, args: str, collected: list[str]) -> str:
    blocks = "".join(f'<div class="msg">{msg}</div>' for msg in collected)
    if not blocks:
        blocks = '<p class="hint">No output.</p>'
    label = f"/{cmd}" + (f" {args}" if args else "")
    body = f"""
<div class="top">
  <h1>{html.escape(label)}</h1>
  <a href="/ui">&larr; back</a>
</div>
{blocks}
"""
    return _page(label, body)


@router.get("/login", response_class=HTMLResponse)
async def login_page(session: str | None = Cookie(default=None)):
    if _is_authenticated(session):
        return RedirectResponse("/ui", status_code=303)
    return _login_page()


@router.post("/login")
async def login_submit(password: str = Form(...)):
    expected = os.getenv("WEB_UI_PASSWORD", "")
    if not expected or not secrets.compare_digest(password, expected):
        await asyncio.sleep(_FAILED_LOGIN_DELAY_SECONDS)
        return HTMLResponse(_login_page("Wrong password."), status_code=401)

    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + _SESSION_TTL_SECONDS
    resp = RedirectResponse("/ui", status_code=303)
    resp.set_cookie(_COOKIE_NAME, token, max_age=_SESSION_TTL_SECONDS,
                     httponly=True, secure=True, samesite="strict")
    return resp


@router.post("/logout")
async def logout(session: str | None = Cookie(default=None)):
    if session:
        _sessions.pop(session, None)
    resp = RedirectResponse("/ui/login", status_code=303)
    resp.delete_cookie(_COOKIE_NAME)
    return resp


@router.get("/", response_class=HTMLResponse)
async def dashboard(session: str | None = Cookie(default=None)):
    if not _is_authenticated(session):
        return RedirectResponse("/ui/login", status_code=303)
    return _dashboard_page()


@router.post("/trigger", response_class=HTMLResponse)
async def trigger(
    cmd: str = Form(...), args: str = Form(""), session: str | None = Cookie(default=None),
):
    if not _is_authenticated(session):
        return RedirectResponse("/ui/login", status_code=303)

    cmd = cmd.lower()
    # Same uppercase + safe-charset filter as bot.py's Telegram parsing —
    # keeps both entry points to the exact same command surface behaving
    # identically, including the injection-safety guarantee.
    arg_list = sanitize_tickers(args.upper().split()) if args.strip() else []
    log.info("web UI triggered /%s args=%s", cmd, arg_list)

    with collect_output() as collected:
        await dispatch(cmd, arg_list, _WEB_CHAT_ID)

    return _result_page(cmd, args, collected)
