"""Typed errors for document handling.

Each error has a stable machine-readable `code` and a short, safe `message`.
Messages never contain file paths or document content, so they can be shown to API users.
HTTP status codes are NOT decided here; the API layer maps error types to statuses, which
keeps parsing code independent from the web framework.
"""


class DocumentError(Exception):
    code = "document_error"
    default_message = "The document could not be processed."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class UnsupportedFileType(DocumentError):
    code = "unsupported_file_type"
    default_message = "Unsupported file type. Allowed: PDF, PNG, JPEG, TIFF, XLSX, XLS, CSV."


class FileTooLarge(DocumentError):
    code = "file_too_large"
    default_message = "The file exceeds the maximum allowed size."


class CorruptFile(DocumentError):
    code = "corrupt_file"
    default_message = "The file is damaged or unreadable."


class PasswordProtected(DocumentError):
    code = "password_protected"
    default_message = "The file is password-protected; upload an unprotected copy."


class EmptyFile(DocumentError):
    code = "empty_file"
    default_message = "The file is empty."
