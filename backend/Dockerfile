# Backend image, shared by the `api` and `worker` services (they differ only in command).
FROM python:3.12-slim

# No .pyc files in the image; unbuffered stdout so logs appear immediately in `docker logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# uv installs dependencies much faster than pip; copied from its official image.
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv

WORKDIR /app

# Tesseract OCR engine + English and Bengali language data. --no-install-recommends and
# removing the apt lists keep the image small (no docs, no extra fonts or GUI packages).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-ben \
    && rm -rf /var/lib/apt/lists/*
# One thread per Tesseract process: we OCR page by page, and Tesseract's internal
# multithreading makes small pages slower in containers (CPU oversubscription).
ENV OMP_THREAD_LIMIT=1

# Install dependencies BEFORE copying source code: this layer is cached and only
# rebuilt when pyproject.toml changes, not on every code edit.
# INSTALL_DEV=true also installs pytest/ruff (used by `make test` / `make lint`).
ARG INSTALL_DEV=false
COPY pyproject.toml ./
RUN if [ "$INSTALL_DEV" = "true" ]; then EXTRA="--extra dev"; else EXTRA=""; fi \
    && uv pip install --system --no-cache -r pyproject.toml $EXTRA

# Run as an unprivileged user: if the app is ever compromised it is not root in the container.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data/uploads \
    && chown -R app:app /data/uploads /app

COPY --chown=app:app . .
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
