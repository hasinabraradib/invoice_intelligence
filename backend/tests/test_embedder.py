"""LocalEmbedder: the model is loaded so that long pages cannot hang the worker."""

import subprocess
import sys
import time
import types

import pytest

from app.search.embedder import LocalEmbedder

# A crowded invoice page: mostly numbers, which split into many word-pieces
# ("1,435.30" -> 1 , 435 . 30), so ~280 words are ~500 tokens.
CROWDED_PAGE = "TAX INVOICE Meghna Traders Ltd " + " ".join(
    f"{n} Cotton fabric (metre) - lot {n} {n % 24 + 1} {n * 3.17:,.2f} {n * 41.9:,.2f}"
    for n in range(1, 25)
)


def test_model_is_loaded_with_eager_attention(monkeypatch):
    """Regression: torch's fused SDPA attention never returned on CPU (torch 2.15,
    aarch64, >1 thread) for inputs over ~128 tokens, and the hung encode could not be
    cancelled, so one crowded page blocked every later job in the worker."""
    seen = {}

    class FakeModel:
        def __init__(self, name, device, model_kwargs=None):
            seen.update(name=name, device=device, model_kwargs=model_kwargs)

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeModel)
    )
    model = LocalEmbedder("some-model")._load()
    assert seen["model_kwargs"] == {"attn_implementation": "eager"}
    assert seen["device"] == "cpu" and model.max_seq_length == 512


@pytest.mark.slow
def test_real_model_embeds_a_crowded_page_quickly(settings):
    """With the real model, a ~500-token page must embed in seconds. Run in a child
    process with a timeout, so a hang fails the test instead of blocking the suite."""
    code = (
        "import sys; from app.search.embedder import LocalEmbedder;"
        f"v = LocalEmbedder({settings.embedding_model!r}).embed([sys.argv[1]]);"
        "assert len(v) == 1 and len(v[0]) == 384"
    )
    try:
        subprocess.run([sys.executable, "-c", code, CROWDED_PAGE], check=True, timeout=120)  # noqa: S603
    except subprocess.TimeoutExpired:
        pytest.fail("embedding a long page hung (fused attention kernel?)")


@pytest.mark.slow
@pytest.mark.anyio
async def test_a_512_token_page_embeds_within_5_seconds_in_the_worker_pool(settings):
    """The production path: the embedding step's own process, real model, hard timeout."""
    from transformers import AutoTokenizer

    from app.procpool import ProcessPool
    from app.steps import embed_task

    page = " ".join([CROWDED_PAGE] * 2)  # well past 512 word-pieces; the model reads 512
    tokens = len(AutoTokenizer.from_pretrained(settings.embedding_model)(page)["input_ids"])
    assert tokens >= 512

    pool = ProcessPool("embed", size=1)
    embedder = LocalEmbedder(settings.embedding_model)
    try:
        await pool.run(embed_task, embedder, ["warm up: load the model"], timeout=120)
        started = time.monotonic()
        vectors = await pool.run(embed_task, embedder, [page], timeout=30)
        elapsed = time.monotonic() - started
    finally:
        pool.close()
    assert len(vectors) == 1 and len(vectors[0]) == 384
    assert elapsed < 5, f"embedding a 512-token page took {elapsed:.1f} s"
