"""POST /documents: per-file results, mapped status codes, and batch isolation."""

from tests import factories


def _upload(client, files):
    return client.post(
        "/documents",
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
    )


def test_batch_with_good_and_bad_files(client, tmp_path, upload_dir):
    text_pdf = factories.text_pdf(tmp_path / "a.pdf").read_bytes()
    mixed_pdf = factories.pdf_with_image_page(tmp_path / "b.pdf").read_bytes()
    png = factories.image(tmp_path / "c.png", "PNG").read_bytes()
    truncated = factories.truncated_pdf(tmp_path / "d.pdf").read_bytes()
    xlsx = factories.invoice_xlsx(tmp_path / "e.xlsx").read_bytes()

    response = _upload(
        client,
        [
            ("invoice.pdf", text_pdf),
            ("mixed.pdf", mixed_pdf),
            ("photo.pdf", png),  # PNG pretending to be a PDF
            ("broken.pdf", truncated),
            ("empty.pdf", b""),
            ("notes.csv", b"just some words without any structure\n"),
            ("invoice.xlsx", xlsx),
        ],
    )

    assert response.status_code == 200
    results = {r["original_filename"]: r for r in response.json()["results"]}

    assert results["invoice.pdf"]["status_code"] == 200
    assert results["invoice.pdf"]["detected_kind"] == "pdf"
    assert results["invoice.pdf"]["page_count"] == 2
    assert results["invoice.pdf"]["pages_needing_ocr"] == 0
    assert len(results["invoice.pdf"]["sha256"]) == 64
    assert results["invoice.pdf"]["size"] == len(text_pdf)

    assert results["mixed.pdf"]["pages_needing_ocr"] == 1

    assert results["photo.pdf"]["status_code"] == 200
    assert results["photo.pdf"]["detected_kind"] == "png"
    assert any("does not match" in w for w in results["photo.pdf"]["warnings"])

    assert results["broken.pdf"]["status_code"] == 422
    assert results["broken.pdf"]["error"]["code"] == "corrupt_file"
    assert results["empty.pdf"]["status_code"] == 422
    assert results["empty.pdf"]["error"]["code"] == "empty_file"
    assert results["notes.csv"]["status_code"] == 415
    assert results["notes.csv"]["error"]["code"] == "unsupported_file_type"

    assert results["invoice.xlsx"]["detected_kind"] == "xlsx"
    assert results["invoice.xlsx"]["sheet_count"] == 2

    # Only the 4 usable files are kept; rejected ones are deleted.
    assert len(list(upload_dir.iterdir())) == 4


def test_oversized_file_gets_413_and_others_still_succeed(client, tmp_path):
    too_big = b"%PDF-1.4\n" + b"0" * (1024 * 1024)  # just over the 1 MB test limit
    ok = factories.text_pdf(tmp_path / "ok.pdf").read_bytes()

    results = _upload(client, [("big.pdf", too_big), ("ok.pdf", ok)]).json()["results"]

    assert results[0]["status_code"] == 413
    assert results[0]["error"]["code"] == "file_too_large"
    assert results[1]["status_code"] == 200


def test_path_traversal_filename_is_stored_safely(client, tmp_path, upload_dir):
    pdf = factories.text_pdf(tmp_path / "a.pdf").read_bytes()

    result = _upload(client, [("../../evil.pdf", pdf)]).json()["results"][0]

    assert result["status_code"] == 200
    assert result["original_filename"] == "evil.pdf"
    stored = list(upload_dir.iterdir())
    assert len(stored) == 1
    assert stored[0].resolve().is_relative_to(upload_dir.resolve())
    assert not (tmp_path / "evil.pdf").exists() and not (upload_dir.parent / "evil.pdf").exists()
    assert "stored_path" not in result  # internal paths are never returned to clients


def test_password_protected_pdf_is_422(client, tmp_path):
    locked = factories.encrypted_pdf(tmp_path / "locked.pdf").read_bytes()

    result = _upload(client, [("locked.pdf", locked)]).json()["results"][0]

    assert result["status_code"] == 422
    assert result["error"]["code"] == "password_protected"


def test_too_many_files_is_rejected(client):
    response = _upload(client, [(f"f{i}.csv", b"a,b\n1,2\n") for i in range(11)])
    assert response.status_code == 400


def test_no_files_is_a_validation_error(client):
    assert client.post("/documents").status_code == 422


def test_same_file_twice_is_stored_once(client, tmp_path, upload_dir):
    pdf = factories.text_pdf(tmp_path / "a.pdf").read_bytes()

    first = _upload(client, [("invoice.pdf", pdf)]).json()["results"][0]
    second = _upload(client, [("copy-of-invoice.pdf", pdf)]).json()["results"][0]

    assert first["duplicate"] is False and first["document_id"]
    assert second["duplicate"] is True
    assert second["document_id"] == first["document_id"]
    assert second["sha256"] == first["sha256"] and second["detected_kind"] == "pdf"
    assert len(list(upload_dir.iterdir())) == 1  # the second copy was not kept


def test_duplicate_within_one_request(client, tmp_path, upload_dir):
    pdf = factories.text_pdf(tmp_path / "a.pdf").read_bytes()
    results = _upload(client, [("a.pdf", pdf), ("b.pdf", pdf)]).json()["results"]
    assert [r["duplicate"] for r in results] == [False, True]
    assert len(list(upload_dir.iterdir())) == 1
