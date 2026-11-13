"""LocalEmbedder: the model is loaded so that long pages cannot hang the worker."""

import subprocess
import sys
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
