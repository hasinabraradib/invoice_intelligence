"""CPU-heavy steps in killable processes: a step that never returns cannot block the worker."""

import dataclasses
import time

import pytest

from app.extraction.llm import FakeLLMClient
from app.steps import StepPools, parse_task
from tests import factories
from tests import procpool_tasks as tasks
from tests.test_api import _upload
from tests.test_pipeline import GOOD_REPLY

pytestmark = pytest.mark.anyio


@pytest.fixture
def pools(settings):
    p = StepPools.create(settings)
    yield p
    p.close()


async def test_a_parse_step_that_never_returns_is_killed_and_the_next_job_runs(
    api, run_job, tmp_path, pools
):
    stuck = dataclasses.replace(pools, parse_fn=tasks.stuck_parse, parse_timeout=1)
    first = await _upload(api, "a.pdf", factories.text_pdf(tmp_path / "a.pdf", 1).read_bytes())
    second = await _upload(api, "b.pdf", factories.text_pdf(tmp_path / "b.pdf", 2).read_bytes())

    started = time.monotonic()
    assert await run_job(first["job_id"], FakeLLMClient(GOOD_REPLY), pools=stuck) == "failed"
    assert time.monotonic() - started < 15  # stopped at its timeout, not the job timeout
    status = (await api.get(f"/documents/{first['document_id']}/status")).json()
    assert status["error"] == "reading the file took longer than 1 s and was stopped"

    # Same pools, real parser: the next job is not blocked by anything left behind.
    assert pools.parse_fn is parse_task
    assert await run_job(second["job_id"], FakeLLMClient(GOOD_REPLY), pools=pools) == "processed"


async def test_an_embedding_that_never_returns_only_costs_the_semantic_index(
    api, run_job, tmp_path, pools
):
    """The document finishes with a warning and keyword search still finds it."""
    quick = dataclasses.replace(pools, embed_timeout=1)
    uploaded = await _upload(api, "a.pdf", factories.text_pdf(tmp_path / "a.pdf", 1).read_bytes())

    started = time.monotonic()
    outcome = await run_job(
        uploaded["job_id"], FakeLLMClient(GOOD_REPLY), pools=quick, embedder=tasks.StuckEmbedder()
    )
    assert outcome in ("processed", "needs_review")
    assert time.monotonic() - started < 15
    status = (await api.get(f"/documents/{uploaded['document_id']}/status")).json()
    assert status["job_status"] == "succeeded"
    assert "semantic search index not updated (StepTimeout)" in status["error"]
    assert "keyword search still works" in status["error"]

    found = (await api.get("/search", params={"q": "invoice", "mode": "keyword"})).json()
    assert uploaded["document_id"] in {hit["document_id"] for hit in found["items"]}
