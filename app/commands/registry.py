import logging
from typing import Callable, Awaitable

log = logging.getLogger(__name__)
_registry: dict[str, tuple[Callable, str]] = {}


def command(*names: str, description: str = ""):
    def decorator(fn: Callable[..., Awaitable]) -> Callable:
        for name in names:
            _registry[name] = (fn, description)
        return fn
    return decorator


async def dispatch(cmd: str, args: list[str], chat_id: str) -> None:
    entry = _registry.get(cmd)
    if entry:
        log.info("command=/%s args=%s chat=%s", cmd, args or "[]", chat_id)
        await entry[0](args, chat_id)
    else:
        log.warning("unknown command=/%s chat=%s", cmd, chat_id)
        from app.telegram import send
        await send(f"Unknown command: /{cmd}\n\nSend /help for the command list.", chat_id=chat_id)


def build_help() -> str:
    lines = ["<b>Trade Signals Bot</b>\n\n<b>Commands:</b>"]
    seen: set[Callable] = set()
    for name, (fn, desc) in _registry.items():
        if desc and fn not in seen:
            lines.append(f"/{name}  {desc}")
            seen.add(fn)
    return "\n".join(lines)
