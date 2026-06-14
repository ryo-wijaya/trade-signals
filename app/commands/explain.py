import logging

from app.commands.registry import command
from app.config import load_config
from app.telegram import send

log = logging.getLogger(__name__)


@command("explain", description="how to read each indicator")
async def handle_explain(args: list[str], chat_id: str) -> None:
    explanations = load_config().get("explanations", [])
    if not explanations:
        await send("No explanations configured.", chat_id=chat_id)
        return
    lines = ["<b>Indicator Guide</b>"]
    for item in explanations:
        lines.append("")
        lines.append(f"<b>{item['name']}</b>")
        lines.append(item["text"])
    await send("\n".join(lines), chat_id=chat_id)
