import logging

from app.commands.registry import command, build_help
from app.telegram import send

log = logging.getLogger(__name__)


@command("help", description="show all commands")
async def handle_help(args: list[str], chat_id: str) -> None:
    log.info("help requested")
    await send(build_help(), chat_id=chat_id)
