import logging
from app.commands.registry import command
from app.config import (
    load_watchlist, load_favourites, load_priority_interval,
    load_valid_priority_intervals, load_config,
)
from app.telegram import send

log = logging.getLogger(__name__)


@command("config", description="show all current settings")
async def handle_config(args: list[str], chat_id: str) -> None:
    tickers = load_watchlist()
    favourites = load_favourites()
    priority = load_priority_interval()
    cfg = load_config()
    scfg = cfg.get("scheduler", {})
    close_h = scfg.get("rth_close_hour", 16)
    open_h = scfg.get("rth_open_hour", 10)
    offset = scfg.get("minute_offset", 5)
    morning_h = scfg.get("morning_report_hour", 10)
    morning_m = scfg.get("morning_report_minute", 0)
    leaps_alert_h = scfg.get("leaps_alert_hour", 10)
    leaps_alert_m = scfg.get("leaps_alert_minute", 30)
    leaps_alert_threshold = cfg.get("options", {}).get("leaps_alert", {}).get("iv_hv_threshold", 0.9)
    close_fmt = f"{close_h % 12 or 12}:{offset:02d}{'am' if close_h < 12 else 'pm'} ET"
    open_fmt = f"{open_h % 12 or 12}:{offset:02d}{'am' if open_h < 12 else 'pm'} ET"
    morning_fmt = f"{morning_h % 12 or 12}:{morning_m:02d}{'am' if morning_h < 12 else 'pm'} ET"
    leaps_alert_fmt = f"{leaps_alert_h % 12 or 12}:{leaps_alert_m:02d}{'am' if leaps_alert_h < 12 else 'pm'} ET"
    valid_priorities = "  ".join(f"/priority {v}" for v in load_valid_priority_intervals())

    log.info("config queried: watchlist=%s favourites=%s priority=%smin", tickers, favourites, priority)
    body = "\n".join(f"  {t}" for t in tickers)
    await send(
        f"<b>Config</b>\n\n"
        f"<b>Watchlist ({len(tickers)} tickers)</b>\n{body}\n\n"
        f"<b>Morning Report</b>  (detailed AI summary + relative strength + cheap list + news)\n"
        f"  Daily {morning_fmt} Mon–Fri\n"
        f"  Scope: favourites only ({len(favourites)} tickers)\n"
        f"  Not manually triggerable — use /signalsplus or /news on demand\n\n"
        f"<b>Action Alert</b> (cheap + oversold-confirmed + growth + analyst consensus + AI outlook)\n"
        f"  Every {priority}min · Mon–Fri {open_fmt}–{close_fmt}\n"
        f"  Change: {valid_priorities}\n\n"
        f"<b>Cheap LEAPS Alert</b>\n"
        f"  Daily {leaps_alert_fmt} Mon–Fri · IV/HV below {leaps_alert_threshold:.2f}\n"
        f"  Scope: favourites only ({len(favourites)} tickers)\n\n"
        f"<b>Earnings</b>\n"
        f"  Weekly · Saturday midnight SGT",
        chat_id=chat_id,
    )


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
