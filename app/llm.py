import logging
import os
import re
import httpx

from app.indicators.engine import IndicatorResult

log = logging.getLogger(__name__)
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def get_summary(r: IndicatorResult, detailed: bool = False) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        log.debug("OPENROUTER_API_KEY not set — skipping LLM summary for %s", r.ticker)
        return ""

    from app.config import load_config
    from app.telegram import _call
    cfg = load_config().get("llm", {})
    model = cfg.get("model", "perplexity/sonar-pro")
    max_tokens = cfg.get("detailed_max_tokens", 120) if detailed else cfg.get("max_tokens", 80)

    log.info("llm summary requested for %s model=%s detailed=%s", r.ticker, model, detailed)

    call = _call(r.score, len(r.signals))

    if detailed:
        prompt = (
            f"{r.ticker} is at ${r.price:.2f}. Technical rating: {call}.\n\n"
            f"Give an independent fundamental outlook — your view may agree or disagree with the technical rating. "
            f"In 2-3 sentences: (1) buy/hold/sell from a fundamental perspective with one specific reason "
            f"(earnings trend, analyst consensus, growth drivers, or valuation), "
            f"(2) the most important upcoming catalyst or recent development for this stock. "
            f"Do NOT restate or refer to the technical indicators. "
            f"Do NOT say you lack current data — give your best assessment. "
            f"Plain text only. No markdown, no bullets, no citation numbers."
        )
    else:
        prompt = (
            f"{r.ticker} at ${r.price:.2f} (technical: {call}). "
            f"In 1-2 sentences, give an independent buy/hold/sell from fundamentals — "
            f"earnings trend, analyst view, or key catalyst. "
            f"Do NOT restate the technical signals. No data disclaimers. Plain text only."
        )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            )
            if resp.status_code != 200:
                log.error("openrouter %d for %s: %s", resp.status_code, r.ticker, resp.text[:200])
                return ""
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            summary = re.sub(r"\[\d+\]", "", raw)
            summary = re.sub(r"\*\*(.+?)\*\*", r"\1", summary)
            summary = re.sub(r"\*(.+?)\*", r"\1", summary)
            summary = summary.strip()
            log.info("llm summary complete for %s (%d chars)", r.ticker, len(summary))
            return summary
    except Exception as exc:
        log.error("llm get_summary failed for %s: %s", r.ticker, exc)
        return ""
