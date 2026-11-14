"""Write the "Runtime prompts" section of docs/prompts.md from the code.

The prompts are copied from the modules that use them, and the example user messages are
built by the same functions the app calls, so the document cannot drift from the code.
Run with `make prompts-doc` after changing a prompt.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.assistant import prompt as assistant_prompt  # noqa: E402
from app.extraction import prompt as extraction_prompt  # noqa: E402
from app.extraction import vision  # noqa: E402
from app.segmentation import classifier  # noqa: E402

BEGIN, END = "<!-- runtime-prompts:begin -->", "<!-- runtime-prompts:end -->"


def block(text: str) -> str:
    return "```text\n" + text.strip("\n").rstrip() + "\n```"


def section() -> str:
    page = extraction_prompt.SourcePage(
        number=1, text="ACME Ltd\nInvoice No: INV-2041\nTotal: 1,320.00 BDT", source="text_layer"
    )
    _, extraction_user = extraction_prompt.build_prompts([page])
    tool_result = assistant_prompt.wrap_tool_result(
        "sum_invoices", {"totals": [{"currency": "BDT", "total": "1320.00", "count": 1}]}
    )
    parts = [
        "## Runtime prompts",
        "",
        "Generated from the code by `backend/scripts/export_prompts.py` (`make prompts-doc`). "
        "Every prompt tells the model that document text is data, not instructions, and the "
        "model only extracts and quotes: plain code parses, calculates and validates.",
        "",
        "### 1. Extraction (text) — "
        "[`app/extraction/prompt.py`](../backend/app/extraction/prompt.py)",
        "",
        "System prompt:",
        "",
        block(extraction_prompt.SYSTEM_PROMPT),
        "",
        "User message for a one-line example page (the tag is derived from a hash of the "
        "document text, so the text cannot contain its own closing tag):",
        "",
        block(extraction_user),
        "",
        "Added once if the reply is not valid JSON for the schema (one repair attempt):",
        "",
        block(extraction_prompt.REPAIR_INSTRUCTION),
        "",
        "### 2. Batch segmentation classifier — "
        "[`app/segmentation/classifier.py`](../backend/app/segmentation/classifier.py)",
        "",
        "Used only for pages the rules cannot decide; it sees the top of each page and of "
        "the page before it.",
        "",
        block(classifier.SYSTEM_PROMPT),
        "",
        "### 3. Vision reading and second opinion — "
        "[`app/extraction/vision.py`](../backend/app/extraction/vision.py)",
        "",
        "The page images are attached to the user message, which only lists the page order. "
        "Values from this reading are marked `vision` and always reviewed.",
        "",
        block(vision.SYSTEM_PROMPT),
        "",
        "### 4. Assistant — [`app/assistant/prompt.py`](../backend/app/assistant/prompt.py)",
        "",
        "System prompt (`{today}` is filled in with the current date):",
        "",
        block(assistant_prompt.SYSTEM_PROMPT),
        "",
        "How a tool result is handed back to the model:",
        "",
        block(tool_result),
    ]
    return "\n".join(parts)


def main(doc: Path) -> None:
    text = doc.read_text()
    start, end = text.index(BEGIN) + len(BEGIN), text.index(END)
    doc.write_text(text[:start] + "\n" + section() + "\n" + text[end:])
    print(f"updated {doc}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
