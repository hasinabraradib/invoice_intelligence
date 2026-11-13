"""Pure search helpers: reciprocal rank fusion, chunking, the fake embedder."""

import math

from app.search.chunking import TOKENS_PER_WORD, chunk_page, chunk_pages
from app.search.embedder import DIM, FakeEmbedder
from app.search.fusion import rrf


def test_rrf_item_good_in_both_lists_beats_first_in_one():
    keyword = ["only_kw_top", "both", "x"]
    semantic = ["only_sem_top", "both", "y"]
    ranked = [item for item, _ in rrf([keyword, semantic], k=60)]
    assert ranked[0] == "both"  # 2 x 1/62 > 1/61
    scores = dict(rrf([keyword, semantic], k=60))
    assert math.isclose(scores["both"], 2 / 62) and math.isclose(scores["only_kw_top"], 1 / 61)


def test_rrf_ties_and_order():
    # a and b both appear once at rank 1: equal scores, first-seen order decides.
    assert rrf([["a"], ["b"]]) == [("a", 1 / 61), ("b", 1 / 61)]
    # k=0 for easy maths: c = 1 + 1/3, x = 1, d = 1/2 + 1/2 = 1. x and d tie; x has the
    # better best-rank (1 vs 2), so it comes first.
    ranked = rrf([["c", "d"], ["x", "d", "c"]], k=0)
    assert [item for item, _ in ranked] == ["c", "x", "d"]
    assert dict(ranked)["d"] == dict(ranked)["x"] == 1.0


def test_rrf_empty_lists():
    assert rrf([]) == []
    assert rrf([[], []]) == []
    assert rrf([["a"], []]) == [("a", 1 / 61)]


def test_chunks_keep_page_numbers_and_overlap():
    words = [f"w{i}" for i in range(1000)]
    chunks = chunk_page(7, " ".join(words), max_tokens=130, overlap_tokens=26)  # 100 / 20 words

    size, overlap = int(130 / TOKENS_PER_WORD), int(26 / TOKENS_PER_WORD)
    assert all(c.page_number == 7 for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text.split()) <= size for c in chunks)
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert previous.text.split()[-overlap:] == current.text.split()[:overlap]
    covered = {w for c in chunks for w in c.text.split()}
    assert covered == set(words)  # nothing lost
    assert chunks[-1].text.split()[-1] == "w999"


def test_chunks_never_cross_pages():
    chunks = chunk_pages([(1, "alpha " * 10), (2, ""), (3, "omega " * 10)])
    assert [(c.page_number, c.chunk_index) for c in chunks] == [(1, 0), (3, 0)]
    assert "omega" not in chunks[0].text and "alpha" not in chunks[1].text


def test_short_page_is_one_chunk():
    [chunk] = chunk_page(1, "Total due: 4.50 EUR")
    assert chunk.text == "Total due: 4.50 EUR"


def test_fake_embedder_is_deterministic_unit_length():
    embedder = FakeEmbedder()
    a, b, c = embedder.embed(["electricity bill", "electricity bill", "office chairs"])
    assert a == b and len(a) == DIM
    assert math.isclose(sum(v * v for v in a), 1.0)
    assert sum(x * y for x, y in zip(a, c, strict=True)) < 0.5
    assert len(embedder.embed([""])[0]) == DIM
