import logging
from app.commands.registry import command
from app.config import load_watchlist, save_watchlist
from app.telegram import send

log = logging.getLogger(__name__)


@command("watchlist", description="view current watchlist")
async def handle_watchlist(args: list[str], chat_id: str) -> None:
    tickers = load_watchlist()
    log.info("watchlist queried: %s", tickers)
    body = "\n".join(f"  {t}" for t in tickers)
    await send(f"<b>Watchlist ({len(tickers)} tickers)</b>\n{body}", chat_id=chat_id)


@command("add", description="add tickers to the watchlist")
async def handle_add(args: list[str], chat_id: str) -> None:
    if not args:
        await send("Usage: /add AAPL TSLA", chat_id=chat_id)
        return
    current = set(load_watchlist())
    added = [t for t in args if t not in current]
    current.update(args)
    updated = sorted(current)
    save_watchlist(updated)
    log.info("watchlist add: %s -> watchlist now %s", added, updated)
    msg = f"Added: {', '.join(added)}" if added else "All tickers already in watchlist."
    await send(msg, chat_id=chat_id)


@command("remove", description="remove tickers from the watchlist")
async def handle_remove(args: list[str], chat_id: str) -> None:
    if not args:
        await send("Usage: /remove AAPL", chat_id=chat_id)
        return
    current = set(load_watchlist())
    removed = [t for t in args if t in current]
    remaining = sorted(current - set(args))
    if not remaining:
        log.warning("watchlist remove rejected: would empty the list")
        await send("Cannot remove all tickers. Watchlist must have at least one.", chat_id=chat_id)
        return
    save_watchlist(remaining)
    log.info("watchlist remove: %s -> watchlist now %s", removed, remaining)
    msg = f"Removed: {', '.join(removed)}" if removed else "None of those tickers were in the watchlist."
    await send(msg, chat_id=chat_id)
