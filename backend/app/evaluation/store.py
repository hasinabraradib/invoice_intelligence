"""Save and read evaluation runs (headline numbers only)."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvalRun


async def save_run(session: AsyncSession, report: dict[str, Any], results_file: str) -> EvalRun:
    """Store the headline of a run_eval.py report. Saving the same run twice is a no-op."""
    run_at = datetime.fromisoformat(report["run_at"])
    existing = await session.scalar(
        select(EvalRun).where(EvalRun.run_at == run_at, EvalRun.results_file == results_file)
    )
    if existing:
        return existing
    run = EvalRun(
        run_at=run_at,
        dataset=report["dataset"],
        split=report["headline_split"],
        provider=report["provider"],
        model=report["model"],
        results_file=results_file,
        headline=report["headline"],
    )
    session.add(run)
    await session.commit()
    return run


async def latest_run(session: AsyncSession) -> EvalRun | None:
    return await session.scalar(select(EvalRun).order_by(EvalRun.run_at.desc()).limit(1))
