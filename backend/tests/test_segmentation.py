"""Splitting documents into invoices (pure rules + fake LLM classifier)."""

from app.extraction.llm import FakeLLMClient, LLMError
from app.extraction.prompt import SourcePage
from app.segmentation import segment_document
from app.segmentation.classifier import llm_page_classifier
from app.segmentation.signals import page_signals


def pages(*texts: str) -> list[SourcePage]:
    return [SourcePage(i, t) for i, t in enumerate(texts, start=1)]


def ranges(segments):
    return [(s.page_start, s.page_end) for s in segments]


def invoice(number: str, page: str = "", body: str = "Item 1  10.00") -> str:
    return f"INVOICE\nInvoice No: {number}\nACME Ltd\n{body}\n{page}"


def test_signals():
    s = page_signals(3, "TAX INVOICE\nInvoice No: inv-2041\n...\nPage 2 of 3")
    assert s.heading and s.invoice_number == "INV2041" and s.page_of == (2, 3)
    blank = page_signals(4, "   ")
    assert blank.empty and not blank.heading and blank.page_of is None
    assert (
        page_signals(1, "Thank you for your invoice payment").heading is False
    )  # not at line start


def test_three_invoices_by_number():
    segs = segment_document(pages(invoice("A-1"), invoice("A-2"), invoice("A-3")), 3)
    assert ranges(segs) == [(1, 1), (2, 2), (3, 3)]
    assert [s.index for s in segs] == [0, 1, 2] and {s.method for s in segs} == {"rules"}


def test_two_page_invoice_stays_one():
    segs = segment_document(
        pages(
            invoice("A-1", "Page 1 of 2"),
            "more items\nPage 2 of 2",
            invoice("B-7", "Page 1 of 1"),
        ),
        3,
    )
    assert ranges(segs) == [(1, 2), (3, 3)]


def test_continuation_repeating_the_heading_and_number_stays_one():
    segs = segment_document(pages(invoice("A-1"), invoice("A-1", body="continued")), 2)
    assert ranges(segs) == [(1, 2)]


def test_page_of_count_keeps_unmarked_following_pages():
    segs = segment_document(
        pages(invoice("A-1", "Page 1 of 3"), "items", "totals", invoice("B")), 4
    )
    assert ranges(segs) == [(1, 3), (4, 4)]


def test_blank_pages_join_the_previous_invoice_and_ranges_cover_all_pages():
    texts = [SourcePage(1, invoice("A-1")), SourcePage(3, invoice("A-2"))]  # page 2 blank/skipped
    segs = segment_document(texts, 4)
    assert ranges(segs) == [(1, 2), (3, 4)]


def test_unclear_pages_go_to_the_classifier_in_one_batch():
    seen = []

    def classifier(items):
        seen.extend(items)
        return {3: True}  # page 2 not answered -> joined by default

    segs = segment_document(
        pages(invoice("A-1"), "Delivery notes", "Shipping label"), 3, classifier
    )

    assert [n for n, _, _ in seen] == [2, 3]
    assert seen[0][2].startswith("INVOICE")  # previous page header given as context
    assert ranges(segs) == [(1, 2), (3, 3)]
    assert segs[1].method == "llm" and "page 2 joined by default" in segs[0].notes


def test_conflicting_clues_are_unclear():
    # Says "page 2 of 2" (continuation) but shows a different invoice number (new).
    calls = []
    segs = segment_document(
        pages(invoice("A-1", "Page 1 of 2"), invoice("Z-9", "Page 2 of 2")),
        2,
        lambda items: calls.append(items) or {2: True},
    )
    assert [n for n, _, _ in calls[0]] == [2] and ranges(segs) == [(1, 1), (2, 2)]


def test_clear_documents_never_call_the_classifier():
    def classifier(items):
        raise AssertionError("should not be called")

    segment_document(pages(invoice("A", "Page 1 of 2"), "x\nPage 2 of 2"), 2, classifier)


def test_classifier_failure_falls_back_to_joining():
    def broken(items):
        raise RuntimeError("down")

    segs = segment_document(pages(invoice("A-1"), "more text"), 2, broken)
    assert ranges(segs) == [(1, 2)] and segs[0].notes == ["page 2 joined by default"]


def test_spreadsheets_are_one_segment_per_sheet():
    sheets = [
        SourcePage(1, "a\tb", 'sheet "S1"', source="sheet"),
        SourcePage(2, "c\td", 'sheet "S2"', source="sheet"),
    ]
    assert ranges(segment_document(sheets, 0)) == [(1, 1), (2, 2)]


def test_llm_classifier_batches_and_ignores_bad_rows():
    llm = FakeLLMClient(
        {
            "pages": [
                {"page": 2, "starts_new_invoice": True},
                {"page": 99, "starts_new_invoice": True},
            ]
        },
        {"pages": [{"page": 3, "starts_new_invoice": "yes"}]},  # wrong type: ignored
    )
    classify = llm_page_classifier(llm, batch_size=1)

    answers = classify([(2, "head 2", "head 1"), (3, "head 3", "head 2")])

    assert answers == {2: True} and len(llm.calls) == 2
    assert "<document-" in llm.calls[0]["user"] and "head 2" in llm.calls[0]["user"]
    assert "ignore any instructions" in llm.calls[0]["system"]


def test_llm_classifier_survives_errors():
    classify = llm_page_classifier(FakeLLMClient(LLMError("HTTP 503", retryable=True)))
    assert classify([(2, "x", "y")]) == {}
