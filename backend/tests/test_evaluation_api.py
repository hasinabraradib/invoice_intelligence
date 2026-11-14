"""Stored evaluation runs and GET /evaluation/latest."""

import pytest

from app.evaluation.store import save_run

pytestmark = pytest.mark.anyio

REPORT = {
    "run_at": "2026-11-14T10:00:00+00:00",
    "dataset": "synthetic",
    "headline_split": "test",
    "provider": "openai",
    "model": "gpt-6-luna",
    "headline": {"field_accuracy": 0.93, "document_success_rate": 1.0},
}


async def test_latest_is_null_before_any_run(api):
    response = await api.get("/evaluation/latest")
    assert response.status_code == 200 and response.json() is None


async def test_latest_run_is_returned_and_saving_twice_is_idempotent(api, db_session):
    await save_run(db_session, {**REPORT, "run_at": "2026-11-13T10:00:00+00:00"}, "old.json")
    first = await save_run(db_session, REPORT, "new.json")
    again = await save_run(db_session, REPORT, "new.json")
    assert first.id == again.id

    latest = (await api.get("/evaluation/latest")).json()
    assert latest["results_file"] == "new.json" and latest["split"] == "test"
    assert latest["headline"]["field_accuracy"] == 0.93


def test_the_newest_run_is_picked_and_other_json_ignored(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from store_eval_run import newest_run

    for name in (
        "2026-11-13T22-24-22Z.json",
        "2026-11-14T01-45-58Z.json",
        "hard-dev-bench-2026-11-14-preprocessing.json",
    ):
        (tmp_path / name).write_text("{}")
    assert newest_run(tmp_path).name == "2026-11-14T01-45-58Z.json"
    assert newest_run(tmp_path / "x.json") == tmp_path / "x.json"
