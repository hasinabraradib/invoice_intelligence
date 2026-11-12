"""Uploads are stored under random names inside UPLOAD_DIR, with size limits and a hash."""

import hashlib
import io

import pytest
from fastapi import UploadFile

from app.parsing.errors import FileTooLarge
from app.storage import sanitize_filename, save_upload

pytestmark = pytest.mark.anyio


async def test_saves_file_inside_upload_dir_with_hash(upload_dir):
    data = b"%PDF-1.4 fake" * 1000
    upload = UploadFile(file=io.BytesIO(data), filename="../../evil.pdf")

    stored = await save_upload(upload, upload_dir, max_bytes=1024 * 1024)

    assert stored.stored_path.parent == upload_dir.resolve()
    assert stored.stored_path.resolve().is_relative_to(upload_dir.resolve())
    assert ".." not in stored.stored_path.name and "evil" not in stored.stored_path.name
    assert stored.original_filename == "evil.pdf"
    assert stored.size == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    assert stored.stored_path.read_bytes() == data


async def test_too_large_raises_and_leaves_no_file(upload_dir):
    upload = UploadFile(file=io.BytesIO(b"x" * 3000), filename="big.pdf")

    with pytest.raises(FileTooLarge):
        await save_upload(upload, upload_dir, max_bytes=1000)

    assert list(upload_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../evil.pdf", "evil.pdf"),
        ("..\\..\\windows\\evil.pdf", "evil.pdf"),
        ("/etc/passwd", "passwd"),
        ("invoice\x00\n<script>.pdf", "invoice___script_.pdf"),
        (".hidden.pdf", "hidden.pdf"),
        ("Rechnung März 2024.pdf", "Rechnung März 2024.pdf"),  # non-ASCII letters are kept
        ("", "unnamed"),
        (None, "unnamed"),
        ("..", "unnamed"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_caps_length_and_keeps_extension():
    name = sanitize_filename("a" * 400 + ".pdf")
    assert len(name) == 255
    assert name.endswith(".pdf")
