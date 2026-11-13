"""The evaluation set parses and its scoring rules behave (no LLM calls here)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from assistant_eval import CORPUS, citation_ok, load_cases, value_ok  # noqa: E402

EVAL = Path(__file__).resolve().parents[2] / "docs" / "assistant-eval.md"


def test_eval_set_has_15_cases_matching_the_corpus():
    if not EVAL.exists():  # the docs folder is not copied into the Docker image
        import pytest

        pytest.skip("docs/assistant-eval.md not available here")
    cases = load_cases(EVAL)
    assert [c.number for c in cases] == list(range(1, 16))
    files = {name for name, _ in CORPUS}
    for case in cases:
        assert case.citation == "-" or case.citation.rsplit(":", 1)[0] in files


def test_value_scoring():
    assert value_ok("2000.00", "You spent 2,000.00 BDT with ACME (eval-acme-march.pdf, p. 1).")
    assert not value_ok("2000.00", "You spent 1,999.00 BDT.")
    assert value_ok("UT-5", "The invoice number is ut-5.")
    assert value_ok("2026-03-25", "It is due on 2026-03-25.")
    # The same date written out is right (the first real run marked this wrong).
    assert value_ok("2026-03-25", "The Dhaka Power invoice is due **March 25, 2026** (p. 1).")
    assert value_ok("2026-03-25", "Due 25 March 2026.")
    assert not value_ok("2026-03-25", "It is due on March 26, 2026.")
    assert not value_ok("2026-03-05", "Due 03/05/2026.")  # ambiguous: not counted as right
    assert value_ok("NONE", "I found no invoices from Globex dated in March 2026.")
    assert not value_ok("NONE", "Globex billed 450.50 USD.")  # invented an amount


def test_citation_scoring():
    citations = [{"filename": "eval-batch.pdf", "page": 3}]
    assert citation_ok("eval-batch.pdf:3", citations)
    assert not citation_ok("eval-batch.pdf:2", citations)
    assert citation_ok("-", [])
