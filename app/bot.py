import asyncio
import os
import httpx

from app.commands import dispatch
from app.telegram import _api


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
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if msg and "text" in msg:
                    text: str = msg["text"]
                    chat_id = str(msg["chat"]["id"])
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
