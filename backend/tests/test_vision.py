"""Vision fallback: pages OCR cannot read are read from the image, and always reviewed."""

import base64
import hashlib
import json
import shutil

import cv2
import httpx
import numpy as np
import pytest
from sqlalchemy import select

import app.models as m
from app.config import Settings
from app.extraction.llm import FakeLLMClient, LLMError, OpenAIClient
from app.extraction.prompt import SourcePage
from app.pipeline import VISION_EXTRACTED, process_document, wants_vision
from app.repository import Repository
from tests import factories
from tests.test_pipeline import GOOD_REPLY

pytestmark = pytest.mark.anyio
needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="no tesseract")

VISION_REPLY = {
    "pages": [{"page": 1, "text": "\n".join(factories.INVOICE_LINES)}],
    "invoice": GOOD_REPLY,
}


def _settings(upload_dir, **overrides) -> Settings:
    return Settings(upload_dir=upload_dir, ocr_enabled=True, embeddings_enabled=False, **overrides)


def _noisy_scan(path) -> None:
    """The invoice text under so much speckle that Tesseract reads little of it."""
    page = np.full((1100, 1700), 255, np.uint8)
    for i, line in enumerate(factories.INVOICE_LINES):
        cv2.putText(page, line, (60, 120 + 90 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2)
    rng = np.random.default_rng(7)
    mask = rng.random(page.shape)
    page[mask < 0.06] = 0
    page[mask > 0.94] = 255
    cv2.imwrite(str(path), cv2.GaussianBlur(page, (5, 5), 1.6))


async def _document(session, path, mime) -> m.Document:
    data = path.read_bytes()
    return await Repository(session).create_document(
        filename=path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type=mime,
        size_bytes=len(data),
        storage_path=str(path),
    )


async def _invoice(session, doc_id) -> m.Invoice:
    return await session.scalar(select(m.Invoice).where(m.Invoice.document_id == doc_id))


# ---------------------------------------------------------------- the decision


def _pages(*confidences):
    return [SourcePage(n, "text", ocr_confidence=c) for n, c in enumerate(confidences, start=1)]


def test_vision_is_wanted_only_below_the_ocr_threshold():
    llm = FakeLLMClient(GOOD_REPLY, vision=[VISION_REPLY])
    settings = Settings(ocr_min_confidence=60)
    assert wants_vision(_pages(42.0, 91.0), llm, settings) == [1]
    assert wants_vision(_pages(60.0, 75.0), llm, settings) == []  # at the threshold: OCR is used
    assert wants_vision(_pages(None), llm, settings) == []  # text layer, no OCR at all


def test_vision_is_not_used_when_off_unsupported_or_too_long():
    low = _pages(20.0)
    capable = FakeLLMClient(GOOD_REPLY, vision=[VISION_REPLY])
    assert wants_vision(low, capable, Settings(vision_fallback_enabled=False)) == []
    assert wants_vision(low, FakeLLMClient(GOOD_REPLY), Settings()) == []  # no image support
    many = _pages(20.0, 90.0, 90.0, 90.0, 90.0)
    assert wants_vision(many, capable, Settings(vision_max_pages=4)) == []


# ---------------------------------------------------------------- end to end (real OCR)


@needs_tesseract
async def test_unreadable_scan_is_read_by_vision_and_always_reviewed(
    db_session, tmp_path, upload_dir
):
    path = tmp_path / "noisy.png"
    _noisy_scan(path)
    doc = await _document(db_session, path, "image/png")
    llm = FakeLLMClient(GOOD_REPLY, vision=[VISION_REPLY])

    outcome = await process_document(doc.id, db_session, llm, _settings(upload_dir))

    page = (await Repository(db_session).get_page_texts(doc.id))[0]
    assert len(llm.vision_calls) == 1 and llm.calls == []  # the image path, not the OCR text
    assert outcome.status == m.DocumentStatus.NEEDS_REVIEW
    invoice = await _invoice(db_session, doc.id)
    assert invoice.needs_review
    assert (
        invoice.fields["total"]["value"] == "4.50" and invoice.fields["total"]["source"] == "vision"
    )
    assert all(f["source"] == "vision" and f["confidence"] <= 0.5 for f in invoice.fields.values())
    codes = await db_session.scalars(
        select(m.ValidationIssue.code).where(m.ValidationIssue.invoice_id == invoice.id)
    )
    assert VISION_EXTRACTED in set(codes)
    # The transcription replaced the unreadable OCR text, so search sees it too.
    assert page.source == "vision" and "INV-2024-001" in page.text
    images = llm.vision_calls[0]["images"]
    assert len(images) == 1 and images[0].startswith(b"\x89PNG")


@needs_tesseract
async def test_readable_pages_never_use_vision(db_session, tmp_path, upload_dir):
    path = factories.text_pdf(tmp_path / "clean.pdf", pages=1)
    doc = await _document(db_session, path, "application/pdf")
    llm = FakeLLMClient(GOOD_REPLY, vision=[VISION_REPLY])

    await process_document(doc.id, db_session, llm, _settings(upload_dir))

    invoice = await _invoice(db_session, doc.id)
    assert llm.vision_calls == [] and len(llm.calls) == 1
    assert invoice.fields["total"]["source"] == "text"


@needs_tesseract
async def test_a_failed_vision_call_falls_back_to_the_ocr_text(db_session, tmp_path, upload_dir):
    path = tmp_path / "noisy.png"
    _noisy_scan(path)
    doc = await _document(db_session, path, "image/png")
    llm = FakeLLMClient(GOOD_REPLY, vision=[LLMError("HTTP 400: image too large")])

    await process_document(doc.id, db_session, llm, _settings(upload_dir))

    invoice = await _invoice(db_session, doc.id)
    assert len(llm.vision_calls) == 1 and len(llm.calls) == 1  # tried, then the OCR text
    assert invoice.fields["total"]["source"] == "text"
    page = (await Repository(db_session).get_page_texts(doc.id))[0]
    assert page.source == "ocr"


# ---------------------------------------------------------------- the provider call


def test_openai_sends_page_images_and_counts_vision_usage():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(VISION_REPLY), "refusal": None}}],
                "usage": {"prompt_tokens": 1500, "completion_tokens": 300},
            },
        )

    llm = OpenAIClient(
        "sk-test", "gpt-6-luna", http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    png = b"\x89PNG\r\n\x1a\nfake"
    reply = llm.complete_json_with_images("sys", "page 1", [png], {"type": "object"})

    assert reply == VISION_REPLY
    content = sent[0]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "page 1"}
    url = content[1]["image_url"]["url"]
    assert url == "data:image/png;base64," + base64.b64encode(png).decode()
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    usage = llm.usage.as_dict()
    assert (usage["llm_calls"], usage["vision_calls"], usage["vision_input_tokens"]) == (1, 1, 1500)
