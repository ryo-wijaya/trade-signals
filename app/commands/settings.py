import logging
from app.commands.registry import command
from app.config import (
    load_watchlist, load_interval, load_priority_interval,
    load_valid_intervals, load_valid_priority_intervals, load_config,
)
from app.telegram import send

log = logging.getLogger(__name__)


@command("config", description="show all current settings")
async def handle_config(args: list[str], chat_id: str) -> None:
    tickers = load_watchlist()
    interval = load_interval()
    priority = load_priority_interval()
    cfg = load_config()
    scfg = cfg.get("scheduler", {})
    is_daily = cfg.get("data", {}).get("resample", "2h").endswith("d")
    close_h = scfg.get("rth_close_hour", 16)
    open_h = scfg.get("rth_open_hour", 10)
    offset = scfg.get("minute_offset", 5)
    close_fmt = f"{close_h % 12 or 12}:{offset:02d}{'am' if close_h < 12 else 'pm'} ET"
    log.info("config queried: watchlist=%s interval=%sh priority=%smin", tickers, interval, priority)
    body = "\n".join(f"  {t}" for t in tickers)
    open_fmt = f"{open_h % 12 or 12}:{offset:02d}{'am' if open_h < 12 else 'pm'} ET"
    batch_desc = (
        f"Once daily at {close_fmt} (with LLM summaries)"
        if is_daily else
        f"Every {interval}h, Mon-Fri {open_fmt}–{close_fmt} (with LLM summaries)"
    )
    await send(
        f"<b>Config</b>\n\n"
        f"<b>Watchlist ({len(tickers)} tickers)</b>\n{body}\n\n"
        f"<b>Batch Report</b>\n  {batch_desc}\n\n"
        f"<b>Priority Check</b>\n  Every {priority}min, Mon-Fri {open_fmt}–{close_fmt}",
        chat_id=chat_id,
    )


@command("interval", description="view or change the batch report frequency")
async def handle_interval(args: list[str], chat_id: str) -> None:
    from app.scheduler import reschedule

    if not args:
        current = load_interval()
        log.info("interval queried: %sh", current)
        valid = load_valid_intervals()
        await send(
            f"Batch report frequency: every {current}h\n"
            f"Change with: {' '.join(f'/interval {v}' for v in valid)}",
            chat_id=chat_id,
        )
        return

    try:
        hours = int(args[0])
    except ValueError:
        await send(f"Usage: /interval 1  (valid: {load_valid_intervals()})", chat_id=chat_id)
        return

    if hours not in load_valid_intervals():
        await send(f"Invalid interval. Choose from: {load_valid_intervals()}", chat_id=chat_id)
        return

    reschedule(hours)
    log.info("interval changed to %sh", hours)
    await send(f"Batch report frequency set to every {hours}h.", chat_id=chat_id)


@command("priority", description="view or change the priority alert check frequency")
async def handle_priority(args: list[str], chat_id: str) -> None:
    from app.scheduler import reschedule_priority

    if not args:
        current = load_priority_interval()
        log.info("priority interval queried: %smin", current)
        valid = load_valid_priority_intervals()
        await send(
            f"Priority check frequency: every {current}min\n"
            f"Change with: {' '.join(f'/priority {v}' for v in valid)}",
            chat_id=chat_id,
        )
        return

    try:
        minutes = int(args[0])
    except ValueError:
        await send(f"Usage: /priority 30  (valid: {load_valid_priority_intervals()})", chat_id=chat_id)
        return

    if minutes not in load_valid_priority_intervals():
        await send(f"Invalid interval. Choose from: {load_valid_priority_intervals()}", chat_id=chat_id)
        return

    reschedule_priority(minutes)
    log.info("priority interval changed to %smin", minutes)
    await send(f"Priority check frequency set to every {minutes}min.", chat_id=chat_id)
