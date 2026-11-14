"""GET /stats dashboard extras: daily activity, issues, review queue, recent uploads."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

import app.models as m
from tests.test_api import _processed
from tests.test_pipeline import GOOD_REPLY, MISSING

pytestmark = pytest.mark.anyio


async def test_an_empty_system_reports_no_made_up_numbers(api):
    stats = (await api.get("/stats")).json()
    assert len(stats["daily"]) == 14
    assert all(
        (d["uploaded"], d["processed"], d["needs_review"], d["failed"]) == (0, 0, 0, 0)
        and d["seconds_per_page"] is None
        for d in stats["daily"]
    )
    assert stats["daily"][-1]["date"] == datetime.now(UTC).date().isoformat()  # oldest first
    assert stats["avg_seconds_per_page"] is None  # "no data", not 0
    assert stats["avg_extraction_confidence"] is None
    assert stats["top_issues"] == stats["review_queue"] == stats["recent_uploads"] == []


async def test_activity_issues_queue_and_recent_uploads(api, run_job, tmp_path, db_session):
    first = await _processed(api, run_job, tmp_path, name="a.pdf")
    # No total printed: MISSING_REQUIRED (an error) sends it to review.
    second = await _processed(
        api, run_job, tmp_path, name="b.pdf", reply={**GOOD_REPLY, "total": MISSING}
    )
    old = await _processed(api, run_job, tmp_path, name="c.pdf")
    await db_session.execute(
        update(m.Document)
        .where(m.Document.id == old["document_id"])
        .values(created_at=datetime.now(UTC) - timedelta(days=20))  # outside the 14 days
    )
    await db_session.commit()

    stats = (await api.get("/stats")).json()

    today = stats["daily"][-1]
    assert today["uploaded"] == 2  # the 20-day-old upload is not in the chart
    assert today["processed"] + today["needs_review"] == 2 and today["failed"] == 0
    assert today["seconds_per_page"] is not None and today["seconds_per_page"] >= 0
    assert stats["avg_seconds_per_page"] is not None
    assert 0 < stats["avg_extraction_confidence"] <= 1

    codes = [i["code"] for i in stats["top_issues"]]
    assert "MISSING_REQUIRED" in codes and len(codes) <= 5
    counts = [i["count"] for i in stats["top_issues"]]
    assert counts == sorted(counts, reverse=True)

    queued = {q["document_id"]: q for q in stats["review_queue"]}
    assert second["document_id"] in queued
    # Same invoice number as a.pdf, so b.pdf has two errors (duplicate + missing total)
    # and some warnings: the main issue is an error, ties broken by code.
    assert queued[second["document_id"]]["main_issue"] == "DUPLICATE_INVOICE"
    assert queued[second["document_id"]]["filename"] == "b.pdf"

    recent = [u["id"] for u in stats["recent_uploads"]]
    assert recent[:2] == [second["document_id"], first["document_id"]]  # newest first
    assert stats["recent_uploads"][0]["status"] in ("processed", "needs_review")
