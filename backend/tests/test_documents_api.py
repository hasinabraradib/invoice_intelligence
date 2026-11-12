"""POST /documents: per-file results, status codes, storage safety and deduplication."""

import pytest

from tests import factories

pytestmark = pytest.mark.anyio


async def _upload(api, files):
    return await api.post(
        "/documents",
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
    )


async def test_batch_with_good_and_bad_files(api, queue, tmp_path, upload_dir):
    pdf = factories.text_pdf(tmp_path / "a.pdf").read_bytes()
    png = factories.image(tmp_path / "c.png", "PNG").read_bytes()
    xlsx = factories.invoice_xlsx(tmp_path / "e.xlsx").read_bytes()

    response = await _upload(
        api,
        [
            ("invoice.pdf", pdf),
            ("photo.pdf", png),  # PNG pretending to be a PDF
            ("empty.pdf", b""),
            ("notes.csv", b"just some words without any structure\n"),
            ("invoice.xlsx", xlsx),
        ],
    )

    assert response.status_code == 202
    results = {r["original_filename"]: r for r in response.json()["results"]}
    accepted = [results[n] for n in ("invoice.pdf", "photo.pdf", "invoice.xlsx")]
    assert all(r["status_code"] == 202 and r["document_id"] and r["job_id"] for r in accepted)
    assert results["invoice.pdf"]["detected_kind"] == "pdf"
    assert len(results["invoice.pdf"]["sha256"]) == 64
    assert results["photo.pdf"]["detected_kind"] == "png"
    assert any("does not match" in w for w in results["photo.pdf"]["warnings"])
    assert results["empty.pdf"]["status_code"] == 422
    assert results["empty.pdf"]["error"]["code"] == "empty_file"
    assert results["notes.csv"]["status_code"] == 415
    assert [str(j) for j in queue.job_ids] == [r["job_id"] for r in accepted]
    assert len(list(upload_dir.iterdir())) == 3  # rejected files are not kept

    status = (await api.get(f"/documents/{results['invoice.pdf']['document_id']}/status")).json()
    assert status["status"] == "uploaded" and status["job_status"] == "queued"


async def test_oversized_file_gets_413_and_others_still_succeed(api, tmp_path):
    too_big = b"%PDF-1.4\n" + b"0" * (1024 * 1024)  # just over the 1 MB test limit
    ok = factories.text_pdf(tmp_path / "ok.pdf").read_bytes()

    results = (await _upload(api, [("big.pdf", too_big), ("ok.pdf", ok)])).json()["results"]

    assert results[0]["status_code"] == 413 and results[0]["error"]["code"] == "file_too_large"
    assert results[1]["status_code"] == 202


async def test_path_traversal_filename_is_stored_safely(api, tmp_path, upload_dir):
    pdf = factories.text_pdf(tmp_path / "a.pdf").read_bytes()

    result = (await _upload(api, [("../../evil.pdf", pdf)])).json()["results"][0]

    assert result["status_code"] == 202 and result["original_filename"] == "evil.pdf"
    [stored] = list(upload_dir.iterdir())
    assert stored.resolve().is_relative_to(upload_dir.resolve())
    assert not (upload_dir.parent / "evil.pdf").exists()
    assert "stored_path" not in result  # internal paths are never returned


async def test_same_file_twice_is_stored_once(api, queue, tmp_path, upload_dir):
    pdf = factories.text_pdf(tmp_path / "a.pdf").read_bytes()

    first = (await _upload(api, [("invoice.pdf", pdf)])).json()["results"][0]
    second = (await _upload(api, [("copy.pdf", pdf)])).json()["results"][0]
    same_request = (await _upload(api, [("x.pdf", pdf), ("y.pdf", pdf)])).json()["results"]

    assert first["duplicate"] is False
    assert second["duplicate"] is True and second["status_code"] == 200
    assert second["document_id"] == first["document_id"] and second["job_id"] == first["job_id"]
    assert [r["duplicate"] for r in same_request] == [True, True]
    assert len(list(upload_dir.iterdir())) == 1 and len(queue.job_ids) == 1


async def test_too_many_files_and_no_files(api):
    response = await _upload(api, [(f"f{i}.csv", b"a,b\n1,2\n") for i in range(11)])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    response = await api.post("/documents")
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
