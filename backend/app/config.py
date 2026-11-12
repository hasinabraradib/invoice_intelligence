"""Application settings, read from environment variables (and an optional .env file).

Why pydantic-settings: values are type-checked at startup, so a typo such as
MAX_UPLOAD_MB=abc crashes immediately with a clear error instead of failing later.
"""

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
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    # SecretStr hides the value in repr()/logs: it prints as '**********'.
    llm_api_key: SecretStr | None = None

    max_upload_mb: int = Field(default=20, gt=0, le=200)
    upload_dir: Path = Path("/data/uploads")
    # Safety caps so a single hostile or huge document cannot exhaust memory/CPU.
    max_files_per_request: int = Field(default=20, gt=0, le=100)
    max_pdf_pages: int = Field(default=2000, gt=0)
    max_sheet_rows: int = Field(default=10_000, gt=0)
    max_sheet_columns: int = Field(default=200, gt=0)

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
