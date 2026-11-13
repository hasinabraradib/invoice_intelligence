"""The latest evaluation run's headline numbers (written by `make eval`)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import EvalRunView
from app.db import get_session
from app.evaluation.store import latest_run

router = APIRouter(prefix="/evaluation", tags=["evaluation"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    "/latest",
    response_model=EvalRunView | None,
    summary="Latest evaluation results (null if `make eval` has not been run)",
)
async def get_latest(session: Session) -> EvalRunView | None:
    run = await latest_run(session)
    return EvalRunView.model_validate(run) if run else None
