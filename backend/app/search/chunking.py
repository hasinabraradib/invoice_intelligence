"""Split page text into overlapping chunks for embedding. Pure functions."""

from dataclasses import dataclass

# A word is about 1.3 model tokens on average (word-pieces: "1,320.00" -> "1", ",", "320"...).
TOKENS_PER_WORD = 1.3


@dataclass(frozen=True)
class Chunk:
    page_number: int
    chunk_index: int  # 0, 1, 2... within the page
    text: str


def chunk_page(
    page_number: int, text: str, max_tokens: int = 500, overlap_tokens: int = 50
) -> list[Chunk]:
    """Chunks of ~max_tokens, each repeating the last ~overlap_tokens of the previous one,
    so a sentence cut at a boundary still appears whole in one chunk. One page only:
    chunks never cross pages, so every hit can say which page it came from."""
    words = text.split()
    if not words:
        return []
    size = max(1, int(max_tokens / TOKENS_PER_WORD))
    overlap = min(int(overlap_tokens / TOKENS_PER_WORD), size - 1)
    step = size - overlap
    chunks = []
    for index, start in enumerate(range(0, len(words), step)):
        chunks.append(Chunk(page_number, index, " ".join(words[start : start + size])))
        if start + size >= len(words):
            break
    return chunks


def chunk_pages(
    pages: list[tuple[int, str]], max_tokens: int = 500, overlap_tokens: int = 50
) -> list[Chunk]:
    return [
        c for number, text in pages for c in chunk_page(number, text, max_tokens, overlap_tokens)
    ]
