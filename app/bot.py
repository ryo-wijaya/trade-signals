import asyncio
import logging
import os
import httpx

from app.commands import dispatch
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


async def start_polling() -> None:
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        print("[bot] TELEGRAM_BOT_TOKEN not set – polling disabled")
        return

    print("[bot] polling started")
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
                    if text.startswith("/"):
                        parts = text.strip().split()
                        cmd = parts[0].lstrip("/").split("@")[0].lower()
                        args = [a.upper() for a in parts[1:]]
                        asyncio.create_task(dispatch(cmd, args, chat_id))
        except asyncio.CancelledError:
            print("[bot] polling stopped")
            break
        except Exception as exc:
            print(f"[bot] polling error: {exc}")
            await asyncio.sleep(5)
