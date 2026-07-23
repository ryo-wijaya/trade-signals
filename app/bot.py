import asyncio
import logging
import os
import httpx

from app.commands import dispatch
from app.config import sanitize_tickers
from app.telegram import _api

log = logging.getLogger(__name__)

def _allowed_chats() -> set[str]:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    return {c.strip() for c in raw.split(",") if c.strip()}


async def _get_updates(offset: int | None) -> list[dict]:
    params: dict = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset
    async with httpx.AsyncClient(timeout=35) as client:
        resp = await client.get(_api("getUpdates"), params=params)
        return resp.json().get("result", [])


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """Parses a Telegram message into (command, args), or None if it isn't a
    command. Args are uppercased and filtered to the same safe ticker/
    keyword/number charset enforced everywhere args enter the system (see
    app.config.sanitize_tickers) — they flow into HTML-formatted messages,
    some not yet escaped at every render site, so this is the one place to
    close that off rather than trusting every downstream f-string."""
    if not text.startswith("/"):
        return None
    parts = text.strip().split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    args = sanitize_tickers(a.upper() for a in parts[1:])
    return cmd, args


async def start_polling() -> None:
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        log.warning("TELEGRAM_BOT_TOKEN not set — polling disabled")
        return

    log.info("polling started")
    offset: int | None = None

    while True:
        try:
            updates = await _get_updates(offset)
            allowed = _allowed_chats()
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if msg and "text" in msg:
                    text: str = msg["text"]
                    chat_id = str(msg["chat"]["id"])
                    if allowed and chat_id not in allowed:
                        log.warning("rejected message from unknown chat_id %s", chat_id)
                        continue
                    parsed = parse_command(text)
                    if parsed:
                        cmd, args = parsed
                        asyncio.create_task(dispatch(cmd, args, chat_id))
        except asyncio.CancelledError:
            log.info("polling stopped")
            break
        except Exception as exc:
            log.error("polling error: %s", exc)
            await asyncio.sleep(5)
