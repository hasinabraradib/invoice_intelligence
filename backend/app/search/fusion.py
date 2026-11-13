"""Reciprocal rank fusion (RRF): merge several ranked lists into one. Pure function."""

from collections.abc import Hashable, Sequence


def rrf[K: Hashable](rankings: Sequence[Sequence[K]], k: int = 60) -> list[tuple[K, float]]:
    """Each list gives an item 1 / (k + rank) points (rank starts at 1); points add up.

    Why: keyword scores (ts_rank) and cosine similarities are on different scales, so we
    combine positions, not raw scores. An item ranked well in BOTH lists beats one that is
    first in only one. k=60 (the usual value) keeps any single list from dominating.
    Ties: the item with the better best-rank first, then the order first seen.
    """
    scores: dict[K, float] = {}
    best_rank: dict[K, int] = {}
    first_seen: dict[K, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            best_rank[item] = min(best_rank.get(item, rank), rank)
            first_seen.setdefault(item, len(first_seen))
    return sorted(scores.items(), key=lambda kv: (-kv[1], best_rank[kv[0]], first_seen[kv[0]]))
