"""Application settings, read from environment variables (and an optional .env file).

Why pydantic-settings: values are type-checked at startup, so a typo such as
MAX_UPLOAD_MB=abc crashes immediately with a clear error instead of failing later.
"""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Env var names are matched case-insensitively: DATABASE_URL -> database_url.
    # extra="ignore" lets the same .env hold variables meant for other services (e.g. POSTGRES_*).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Required: no default, so the app refuses to start without them (fail fast).
    database_url: str
    redis_url: str

    # Which LLM backend to use; "none" lets the app boot without any API key.
    llm_provider: Literal["none", "gemini", "groq"] = "none"
    # SecretStr hides the value in repr()/logs: it prints as '**********'.
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None  # empty = the provider's default model
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_max_attempts: int = Field(default=4, ge=1, le=10)
    llm_chunk_tokens: int = Field(default=6000, ge=500)

    # Validation: amounts may differ by this much (rounding); fields below this
    # confidence (0-1) are flagged for human review.
    money_tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)
    review_confidence: float = Field(default=0.6, ge=0, le=1)

    # Background jobs
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    job_retry_base_seconds: float = Field(default=10, gt=0)  # 10s, 20s, 40s... + jitter
    job_timeout_base_seconds: int = Field(default=120, gt=0)
    job_timeout_per_page_seconds: int = Field(default=15, gt=0)
    job_timeout_max_seconds: int = Field(default=3600, gt=0)
    worker_concurrency: int = Field(default=2, ge=1, le=32)
    stuck_job_minutes: int = Field(default=30, ge=1)

    # Semantic search. Chunks of ~500 tokens with 50 overlap, never across pages.
    embeddings_enabled: bool = True
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embed_chunk_tokens: int = Field(default=500, ge=50, le=510)  # model reads <= 512
    embed_chunk_overlap: int = Field(default=50, ge=0)
    # Semantic matches below this cosine similarity are dropped (unrelated text). Measured
    # with all-MiniLM-L6-v2 on invoice-sized chunks: right match 0.25-0.38, wrong invoice
    # 0.08-0.11, unrelated text <= 0.12. 0.20 sits between them.
    semantic_min_similarity: float = Field(default=0.20, ge=-1, le=1)

    # Browsers may call the API from these origins (the Next.js dev server by default).
    cors_origins: list[str] = ["http://localhost:3000"]

    @field_validator("llm_model", "llm_api_key", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        # "LLM_MODEL=" in .env gives "", which should mean "not set".
        return None if isinstance(value, str) and not value.strip() else value

    max_upload_mb: int = Field(default=20, gt=0, le=200)
    upload_dir: Path = Path("/data/uploads")
    # Safety caps so a single hostile or huge document cannot exhaust memory/CPU.
    max_files_per_request: int = Field(default=20, gt=0, le=100)
    max_pdf_pages: int = Field(default=2000, gt=0)
    max_sheet_rows: int = Field(default=10_000, gt=0)
    max_sheet_columns: int = Field(default=200, gt=0)

    # OCR. OCR_LANGS uses Tesseract's syntax: "eng", "ben" or "eng+ben". The pattern also
    # stops anything except language codes from reaching the tesseract command line.
    ocr_enabled: bool = True
    ocr_langs: str = Field(default="eng", pattern=r"^[a-z_]{3,}(\+[a-z_]{3,})*$")
    ocr_min_confidence: float = Field(default=60, ge=0, le=100)
    ocr_page_timeout_seconds: int = Field(default=60, gt=0)
    ocr_dpi: int = Field(default=300, ge=72, le=600)

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        # We use SQLAlchemy's async engine, which needs an async driver in the URL.
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must start with postgresql+asyncpg://")
        return value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Build settings once and reuse them (environment is read a single time)."""
    return Settings()
