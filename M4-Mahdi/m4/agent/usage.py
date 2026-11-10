"""Persistent ledger of LLM calls (token usage, latency, failures) and the daily call budget.

Recording never breaks the agent: database errors are logged and ignored, so the LLM
client also works in contexts without an initialised database (scripts, unit tests).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

logger = logging.getLogger("m4.llm.usage")


def _day_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def record_call(entry: dict) -> None:
    try:
        from ..db.models import LlmCall
        from ..db.session import SessionLocal
        with SessionLocal() as db:
            db.add(LlmCall(**{k: entry.get(k) for k in (
                "purpose", "model_requested", "model_used", "status", "prompt_tokens", "completion_tokens",
                "reasoning_tokens", "total_tokens", "seconds", "error", "request_id")}))
            db.commit()
    except Exception as exc:  # pragma: no cover - ledger must never break the workflow
        logger.warning("LLM usage not recorded: %s", exc)


def calls_today() -> int:
    try:
        from ..db.models import LlmCall
        from ..db.session import SessionLocal
        with SessionLocal() as db:
            return int(db.scalar(select(func.count()).select_from(LlmCall).where(LlmCall.created_at >= _day_start())) or 0)
    except Exception:
        return 0


def usage_summary(days: int = 1) -> dict:
    """Calls and tokens since the start of the UTC day (days=1) or the last `days` days."""
    since = _day_start() - timedelta(days=days - 1)
    try:
        from ..db.models import LlmCall
        from ..db.session import SessionLocal
        with SessionLocal() as db:
            rows = list(db.scalars(select(LlmCall).where(LlmCall.created_at >= since).order_by(LlmCall.created_at.desc())))
    except Exception:
        rows = []
    ok = [r for r in rows if r.status == "ok"]
    by_purpose: dict[str, int] = {}
    for r in rows:
        by_purpose[r.purpose] = by_purpose.get(r.purpose, 0) + 1
    return {"since": since.isoformat(), "calls": len(rows), "successful": len(ok),
            "failed": len(rows) - len(ok), "total_tokens": sum(r.total_tokens or 0 for r in ok),
            "prompt_tokens": sum(r.prompt_tokens or 0 for r in ok), "completion_tokens": sum(r.completion_tokens or 0 for r in ok),
            "by_purpose": by_purpose, "models": sorted({r.model_used for r in ok if r.model_used}),
            "last_error": next((r.error for r in rows if r.status != "ok"), None)}
