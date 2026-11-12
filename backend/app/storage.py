"""Save uploads to disk safely.

- The stored name is a random UUID. The user's filename is NEVER used to build a path,
  so names like "../../etc/passwd" cannot escape UPLOAD_DIR (path traversal).
- The size limit is enforced while streaming, so an oversized file is rejected as soon as
  it crosses the limit and never fully lands in UPLOAD_DIR.
- SHA-256 is computed in the same pass; later steps use it to make jobs idempotent
  (same content -> same document, CLAUDE.md rule 4).
"""

import hashlib
import re
import unicodedata
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple

import anyio
from fastapi import UploadFile

from app.parsing.errors import FileTooLarge

CHUNK_SIZE = 1024 * 1024  # 1 MiB: bounded memory use whatever the file size
MAX_DISPLAY_NAME_LENGTH = 255


class StoredFile(NamedTuple):
    stored_path: Path
    sha256: str
    size: int
    original_filename: str  # sanitised, for display only


def sanitize_filename(raw: str | None) -> str:
    """Make a user-supplied filename safe to DISPLAY (never to use as a path).

    Keeps only the last path component (handles both / and \\), normalises Unicode,
    removes control and other risky characters, and caps the length.
    """
    name = PureWindowsPath(PurePosixPath(raw or "").name).name
    name = unicodedata.normalize("NFKC", name)
    # Keep letters/digits in any language plus a few safe punctuation characters.
    name = "".join(ch if ch.isalnum() or ch in " ._-()[]" else "_" for ch in name)
    name = re.sub(r"\s+", " ", name).strip(" .")  # no leading dots (hidden files) or padding
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        keep = MAX_DISPLAY_NAME_LENGTH - len(ext) - 1
        name = f"{stem[:keep]}.{ext}" if dot and len(ext) <= 10 else name[:MAX_DISPLAY_NAME_LENGTH]
    return name or "unnamed"


async def save_upload(upload: UploadFile, upload_dir: Path, max_bytes: int) -> StoredFile:
    """Stream `upload` into `upload_dir`. Raises FileTooLarge (and leaves no file behind)."""
    # anyio.Path runs filesystem calls in a worker thread so the event loop never blocks.
    await anyio.Path(upload_dir).mkdir(parents=True, exist_ok=True)
    upload_root = Path(await anyio.Path(upload_dir).resolve())
    stored_path = upload_root / uuid.uuid4().hex  # no extension: the name carries no meaning
    # Defence in depth: this cannot fail with a UUID name, but proves the invariant.
    if not Path(await anyio.Path(stored_path).resolve()).is_relative_to(upload_root):
        raise RuntimeError("stored path escaped the upload directory")

    digest = hashlib.sha256()
    size = 0
    try:
        # "xb" = create exclusively: never overwrite an existing file.
        async with await anyio.open_file(stored_path, "xb") as out:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLarge(
                        f"The file exceeds the maximum allowed size of "
                        f"{max_bytes // (1024 * 1024)} MB."
                    )
                digest.update(chunk)
                await out.write(chunk)
    except BaseException:
        # Any failure (too large, disk full, client disconnect) -> remove the partial file.
        await anyio.Path(stored_path).unlink(missing_ok=True)
        raise

    return StoredFile(
        stored_path=stored_path,
        sha256=digest.hexdigest(),
        size=size,
        original_filename=sanitize_filename(upload.filename),
    )
