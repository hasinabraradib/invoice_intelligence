"""Load demo data: a varied set of synthetic invoices, uploaded through the API.

Run by `make demo` inside the api container, after `make eval-dataset` has generated the
synthetic files (fixed seed, no real data). The files go through the normal upload path,
so the worker processes them like any user upload. Re-running is safe: the API recognises
files it already has (same SHA-256) and returns the existing documents.
"""

import os
import sys
import time
from pathlib import Path

import httpx

FILES = Path(sys.argv[1] if len(sys.argv) > 1 else "/eval/dataset/files")
API = os.environ.get("DEMO_API_URL", "http://api:8000")

# A spread of what the app handles: text PDFs, a scanned PDF, phone photos, spreadsheets,
# CSV, a purchase order, a duplicate copy, a 6-invoice batch and the ledger it matches.
DEMO = [
    "inv-02.pdf",
    "inv-02-purchase-order.pdf",
    "inv-06.pdf",
    "inv-06-copy.jpg",
    "inv-01-scanned.pdf",
    "inv-08.jpg",
    "inv-03.xlsx",
    "inv-04.csv",
    "inv-09.pdf",
    "inv-12.pdf",
    "batch-6-invoices.pdf",
    "ledger-batch.xlsx",
]
FINISHED = {"processed", "needs_review", "failed", "partially_failed"}


def main() -> int:
    key = os.environ.get("API_KEY") or os.environ.get("API_KEYS", "").split(",")[0].strip()
    if not key:
        print("No API key: run `make up` first (it creates one in .env).")
        return 1
    if os.environ.get("LLM_PROVIDER", "none") == "none":
        print(
            "Note: LLM_PROVIDER is 'none', so invoices will not be extracted (spreadsheets and "
            "the ledger still load). Add a provider key to .env and run `make up` again."
        )
    missing = [name for name in DEMO if not (FILES / name).exists()]
    if missing:
        print(f"Missing generated files {missing}: run `make eval-dataset` first.")
        return 1

    http = httpx.Client(base_url=API, headers={"X-API-Key": key}, timeout=120)
    files = [("files", (name, (FILES / name).read_bytes())) for name in DEMO]
    response = http.post("/documents", files=files)
    response.raise_for_status()
    ids: dict[str, str] = {}
    for result in response.json()["results"]:
        name = result["original_filename"]
        note = "already loaded" if result.get("duplicate") else f"HTTP {result['status_code']}"
        print(f"  {name:<28} {note}")
        if result.get("document_id"):
            ids[name] = result["document_id"]

    print("Waiting for the worker (this takes a minute or two with a real LLM)...")
    deadline = time.monotonic() + 15 * 60
    status: dict[str, str] = {}
    while time.monotonic() < deadline:
        status = {n: http.get(f"/documents/{i}/status").json()["status"] for n, i in ids.items()}
        done = sum(s in FINISHED for s in status.values())
        print(f"  {done}/{len(ids)} finished", end="\r", flush=True)
        if done == len(ids):
            break
        time.sleep(5)
    print()
    for name, value in sorted(status.items()):
        print(f"  {name:<28} {value}")

    batch, ledger = ids.get("batch-6-invoices.pdf"), ids.get("ledger-batch.xlsx")
    if batch and ledger and status.get("batch-6-invoices.pdf") in FINISHED - {"failed"}:
        check = http.post("/cross-checks", json={"document_a_id": batch, "document_b_id": ledger})
        if check.status_code < 300:
            print("Started a cross-check of the batch PDF against the ledger (see /cross-check).")
    print("Done. Open http://localhost:3000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
