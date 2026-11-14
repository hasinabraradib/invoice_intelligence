"""End-to-end evaluation through the real API and worker (see `make eval`).

Uploads the labelled dataset to a running API, waits for the worker, runs the planted
cross-checks and the assistant eval, scores everything against the ground truth, and
writes:
    eval/results/<timestamp>.json   raw results: every document, field and finding
    docs/evaluation.md              tables (between the eval markers), failure examples

`make eval` runs this inside an ISOLATED stack (compose project "invoice-eval": its own
database, Redis and uploads), because duplicate detection and the assistant look at every
document in the database: your own documents would change the results.

    python /eval/run_eval.py --api http://api:8000 [--skip-assistant] [--real]
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, "/app")  # the backend image: `app` is importable
from app.evaluation import scoring  # noqa: E402

EVAL = Path(__file__).resolve().parent
DATASET = EVAL / "dataset"
RESULTS = EVAL / "results"
DOCS = Path(os.environ.get("EVAL_DOCS_DIR", "/docs"))
TERMINAL = {"processed", "needs_review", "failed", "partially_failed"}
BEGIN, END = "<!-- eval:begin -->", "<!-- eval:end -->"


# ---------------------------------------------------------------- API calls


class Api:
    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, timeout=120)

    def upload(self, paths: list[Path]) -> list[dict]:
        results = []
        for start in range(0, len(paths), 20):  # MAX_FILES_PER_REQUEST
            files = [("files", (p.name, p.read_bytes())) for p in paths[start : start + 20]]
            response = self.http.post("/documents", files=files)
            response.raise_for_status()
            results += response.json()["results"]
        return results

    def wait(self, ids: list[str], timeout: float) -> None:
        deadline = time.monotonic() + timeout
        pending = set(ids)
        while pending:
            for doc_id in list(pending):
                status = self.http.get(f"/documents/{doc_id}/status").json()
                if status["status"] in TERMINAL and status.get("job_status") != "running":
                    pending.discard(doc_id)
            if pending and time.monotonic() > deadline:
                raise TimeoutError(f"{len(pending)} document(s) still processing")
            if pending:
                print(f"  waiting for {len(pending)} document(s)...", file=sys.stderr)
                time.sleep(5)

    def document(self, doc_id: str) -> dict:
        detail = self.http.get(f"/documents/{doc_id}").json()
        result = self.http.get(f"/documents/{doc_id}/result")
        detail["invoices"] = result.json()["invoices"] if result.status_code == 200 else []
        return detail

    def cross_check(self, a: str, b: str, timeout: float = 300) -> dict:
        response = self.http.post("/cross-checks", json={"document_a_id": a, "document_b_id": b})
        response.raise_for_status()
        check_id = response.json()["id"]
        deadline = time.monotonic() + timeout
        while True:
            check = self.http.get(f"/cross-checks/{check_id}").json()
            if check["status"] in ("succeeded", "failed") or time.monotonic() > deadline:
                return check
            time.sleep(2)


# ---------------------------------------------------------------- running


def load_dataset(real: bool) -> tuple[dict, list[dict], dict[str, Path]]:
    manifest = json.loads((DATASET / "manifest.json").read_text())
    labels = [json.loads(p.read_text()) for p in sorted((DATASET / "labels").glob("*.json"))]
    for label in labels:
        label["dataset"] = "synthetic"
    paths = {f["file"]: DATASET / "files" / f["file"] for f in manifest["files"]}
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        raise SystemExit(f"dataset files missing ({missing[:3]}...): run `make eval-dataset`")
    for f in manifest["files"]:
        if scoring_sha(paths[f["file"]]) != f["sha256"]:
            raise SystemExit(f"{f['file']} differs from the manifest: run `make eval-dataset`")
    if real:
        real_labels = json.loads((DATASET / "real" / "labels.json").read_text())["invoices"]
        for label in real_labels:
            path = DATASET / "real" / label["file"]
            if not path.exists():
                print(
                    f"skipping real label {label['id']}: {label['file']} not found", file=sys.stderr
                )
                continue
            label["dataset"] = "real"
            labels.append(label)
            paths[label["file"]] = path
            manifest["files"].append(
                {
                    "file": label["file"],
                    "file_type": label["file_type"],
                    "split": label["split"],
                    "role": "invoice",
                    "invoices": [label["id"]],
                    "expected_segments": None,
                }
            )
    return manifest, labels, paths


def scoring_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def process(api: Api, manifest: dict, paths: dict[str, Path], timeout: float) -> dict[str, dict]:
    """Upload everything (duplicate copies last), wait, and fetch each document."""
    first = [f for f in manifest["files"] if f["role"] != "duplicate_copy"]
    later = [f for f in manifest["files"] if f["role"] == "duplicate_copy"]
    docs: dict[str, dict] = {}
    for batch in (first, later):
        if not batch:
            continue
        started = datetime.now(UTC)
        uploads = api.upload([paths[f["file"]] for f in batch])
        for f, up in zip(batch, uploads, strict=True):
            docs[f["file"]] = {"upload": up, "uploaded_at": started.isoformat()}
        ids = [u["document_id"] for u in uploads if u.get("document_id")]
        print(f"uploaded {len(ids)}/{len(batch)} file(s); processing...", file=sys.stderr)
        api.wait(ids, timeout)
    for doc in docs.values():
        if doc["upload"].get("document_id"):
            doc.update(api.document(doc["upload"]["document_id"]))
    return docs


def run_assistant_eval() -> dict | None:
    out = Path("/tmp/assistant_eval.json")  # noqa: S108 (container scratch)
    print("running the assistant eval (15 questions)...", file=sys.stderr)
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "/app/scripts/assistant_eval.py", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not out.exists():
        print(f"assistant eval failed: {proc.stderr[-500:]}", file=sys.stderr)
        return None
    return json.loads(out.read_text())


# ---------------------------------------------------------------- scoring


def _seconds(job: dict | None) -> float | None:
    if not job or not job.get("started_at") or not job.get("finished_at"):
        return None
    start = datetime.fromisoformat(job["started_at"])
    return (datetime.fromisoformat(job["finished_at"]) - start).total_seconds()


def score(manifest: dict, labels: list[dict], docs: dict[str, dict], prices: dict) -> dict:
    invoice_scores = []
    for label in labels:
        doc = docs.get(label["file"], {})
        invoice = scoring.pick_invoice(label, doc.get("invoices") or [])
        s = scoring.score_invoice(label, invoice)
        invoice_scores.append((label, s))

    file_rows = []
    for f in manifest["files"]:
        doc = docs.get(f["file"], {})
        job = doc.get("latest_job") or {}
        seconds = _seconds(job)
        pages = doc.get("page_count") or 1
        file_rows.append(
            {
                "file": f["file"],
                "file_type": f["file_type"],
                "split": f["split"],
                "subset": f.get("subset", "clean"),
                "role": f["role"],
                "status": doc.get("status") or ("rejected" if doc.get("upload") else "missing"),
                "error": doc.get("error") or (doc.get("upload") or {}).get("error"),
                "pages": pages,
                "invoices_found": len(doc.get("invoices") or []),
                "seconds": seconds,
                "seconds_per_page": seconds / pages if seconds is not None else None,
                "llm_calls": job.get("llm_calls", 0),
                "input_tokens": job.get("llm_input_tokens", 0),
                "output_tokens": job.get("llm_output_tokens", 0),
                "cached_input_tokens": job.get("llm_cached_input_tokens", 0),
                "segments": [[i["page_start"], i["page_end"]] for i in doc.get("invoices") or []],
                "expected_segments": f.get("expected_segments"),
            }
        )

    def group(split: str | None, subset: str | None = None) -> dict[str, Any]:
        """Invoices and files of one split (None: all) and subset ("clean", "hard")."""

        def keep(item_split: str, item_subset: str) -> bool:
            return split in (None, item_split) and subset in (None, item_subset)

        chosen = [
            s for label, s in invoice_scores if keep(label["split"], label.get("subset", "clean"))
        ]
        files = [r for r in file_rows if keep(r["split"], r["subset"])]
        by_type: dict[str, list] = defaultdict(list)
        for s in chosen:
            by_type[s.file_type].append(s)
        return {
            "invoices": len(chosen),
            "overall": summary(chosen, files, prices),
            "per_file_type": {
                t: summary(items, [r for r in files if r["file_type"] == t], prices)
                for t, items in sorted(by_type.items())
            },
            "fields": scoring.field_metrics(chosen)["per_field"],
            "flags": scoring.flag_metrics(chosen),
            "duplicates": scoring.duplicate_metrics(chosen),
        }

    segments = [
        {
            "file": r["file"],
            "split": r["split"],
            **scoring.segment_metrics(r["expected_segments"], r["segments"]),
            "predicted_segments": r["segments"],
            "expected_segments": r["expected_segments"],
        }
        for r in file_rows
        if r["expected_segments"]
    ]
    hard_kinds: dict[str, dict[str, list]] = {"test": defaultdict(list), "dev": defaultdict(list)}
    for label, s in invoice_scores:
        if label.get("hard_kind"):
            hard_kinds[label["split"]][label["hard_kind"]].append(s)
    return {
        "invoice_scores": [
            {
                "dataset": label.get("dataset"),
                "subset": label.get("subset", "clean"),
                "hard_kind": label.get("hard_kind"),
                **scoring.as_dict(s),
            }
            for label, s in invoice_scores
        ],
        "files": file_rows,
        "segmentation": segments,
        # "test" is the clean test invoices (comparable across runs); the hard subset,
        # also held out, is reported next to it, never mixed in.
        "splits": {
            "test": group("test", "clean"),
            "test_hard": group("test", "hard"),
            "dev": group("dev", "clean"),
            "dev_hard": group("dev", "hard"),  # the set robustness work is measured on
            "all": group(None),
        },
        "hard_kinds": _kind_table(hard_kinds["test"], prices),
        "hard_kinds_dev": _kind_table(hard_kinds["dev"], prices),
    }


def _kind_table(kinds: dict[str, list], prices: dict) -> dict[str, dict]:
    return {
        kind: {
            **{k: v for k, v in summary(items, [], prices).items() if k in HARD_KEYS},
            "invoices": len(items),
        }
        for kind, items in sorted(kinds.items())
    }


HARD_KEYS = ("field_accuracy", "false_fill_rate", "missing_rate", "wrong_rate", "line_item_f1")


def summary(scores: list, files: list[dict], prices: dict) -> dict[str, Any]:
    fields = scoring.field_metrics(scores)["overall"]
    lines = scoring.line_metrics(scores)
    flags = scoring.flag_metrics(scores)
    ok = [r for r in files if r["status"] in scoring.SUCCESS_STATUSES]
    timed = [r["seconds"] for r in files if r["seconds"] is not None]
    per_page = [r["seconds_per_page"] for r in files if r["seconds_per_page"] is not None]
    tokens_in = sum(r["input_tokens"] for r in files)
    tokens_out = sum(r["output_tokens"] for r in files)
    cost = None
    if prices["input"] is not None and prices["output"] is not None:
        cost = float(
            (Decimal(tokens_in) * prices["input"] + Decimal(tokens_out) * prices["output"])
            / Decimal(1_000_000)
        )
    return {
        "field_accuracy": fields["accuracy"],
        "false_fill_rate": fields["false_fill_rate"],
        "missing_rate": fields["missing_rate"],
        "wrong_rate": fields["wrong_rate"],
        "line_item_precision": lines["precision"],
        "line_item_recall": lines["recall"],
        "line_item_f1": lines["f1"],
        "flag_precision": flags["precision"],
        "flag_recall": flags["recall"],
        "documents": len(files),
        "document_success_rate": round(len(ok) / len(files), 4) if files else None,
        "document_failure_rate": round(1 - len(ok) / len(files), 4) if files else None,
        "p50_seconds_per_document": scoring.percentile(timed, 50),
        "p95_seconds_per_document": scoring.percentile(timed, 95),
        "p50_seconds_per_page": scoring.percentile(per_page, 50),
        "p95_seconds_per_page": scoring.percentile(per_page, 95),
        "llm_calls": sum(r["llm_calls"] for r in files),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cached_input_tokens": sum(r["cached_input_tokens"] for r in files),
        "cost_usd": round(cost, 6) if cost is not None else None,
        "cost_usd_per_document": round(cost / len(files), 6)
        if cost is not None and files
        else None,
    }


# ---------------------------------------------------------------- report


def pct(v: Any) -> str:
    return "–" if v is None else f"{v * 100:.1f}%"


def num(v: Any, digits: int = 2) -> str:
    return "–" if v is None else f"{v:.{digits}f}"


def prf(m: dict) -> str:
    keys = ("line_item_precision", "line_item_recall", "line_item_f1")
    return " / ".join(pct(m[k]) for k in keys)


def failure_examples(report: dict, subset: str = "clean", limit: int = 12) -> list[dict]:
    """Real mistakes on the test split (one subset), most informative kinds first."""
    order = {"false_fill": 0, "wrong": 1, "missing": 2}
    out = []
    for inv in report["invoice_scores"]:
        if inv["split"] != "test" or inv.get("subset", "clean") != subset:
            continue
        for f in inv["fields"]:
            if f["outcome"] in order:
                out.append(
                    {"invoice": inv["id"], "file": inv["file"], "file_type": inv["file_type"], **f}
                )
    out.sort(key=lambda x: (order[x["outcome"]], x["file_type"], x["invoice"]))
    per_kind: Counter = Counter()
    picked = []
    for x in out:  # at most two per (outcome, field), so the list shows variety
        key = (x["outcome"], x["field"])
        if per_kind[key] < 2 and len(picked) < limit:
            picked.append(x)
            per_kind[key] += 1
    return picked


def _headline_rows(m: dict) -> list[tuple[str, str]]:
    o = m["overall"]
    return [
        ("Invoices scored", str(m["invoices"])),
        ("Field accuracy", pct(o["field_accuracy"])),
        ("False-fill rate (value given, truth null)", pct(o["false_fill_rate"])),
        ("Missing rate (truth has a value, none given)", pct(o["missing_rate"])),
        ("Wrong value rate", pct(o["wrong_rate"])),
        ("Line items precision / recall / F1", prf(o)),
        (
            "Validation flags precision / recall",
            f"{pct(o['flag_precision'])} / {pct(o['flag_recall'])}",
        ),
        ("Documents processed (not failed)", pct(o["document_success_rate"])),
        (
            "Time per document p50 / p95 (s)",
            f"{num(o['p50_seconds_per_document'], 1)} / {num(o['p95_seconds_per_document'], 1)}",
        ),
        (
            "Time per page p50 / p95 (s)",
            f"{num(o['p50_seconds_per_page'], 1)} / {num(o['p95_seconds_per_page'], 1)}",
        ),
    ]


def _failure_table(examples: list[dict], empty: str) -> list[str]:
    if not examples:
        return [empty]
    lines = [
        "| Invoice | File type | Field | Outcome | Truth | Predicted | Evidence quoted |",
        "|---|---|---|---|---|---|---|",
    ]
    for x in examples:
        ev = " ".join(str(x.get("evidence") or "").split())[:60].replace("|", "/")
        lines.append(
            f"| {x['invoice']} | {x['file_type']} | {x['field']} | {x['outcome']} | "
            f"{x['truth']} | {x['predicted']} | {ev} |"
        )
    return lines


def render(report: dict) -> str:
    test = report["splits"]["test"]
    columns = [("Test, clean", test), ("Dev, clean", report["splits"]["dev"])]
    if report["splits"].get("test_hard", {}).get("invoices"):
        columns.insert(1, ("Test, hard subset", report["splits"]["test_hard"]))
    if report["splits"].get("dev_hard", {}).get("invoices"):
        columns.append(("Dev, hard set", report["splits"]["dev_hard"]))
    lines = [
        BEGIN,
        f"_Generated by `make eval` on {report['run_at'][:16].replace('T', ' ')} UTC with "
        f"`{report['provider']}` / `{report['model']}`. Raw results: "
        f"`eval/results/{report['results_file']}`._",
        "",
        "### Summary of this run (synthetic data)",
        "",
        "| Metric | " + " | ".join(name for name, _ in columns) + " |",
        "|---" * (len(columns) + 1) + "|",
    ]
    table = [_headline_rows(m) for _, m in columns]
    for i, (label, _) in enumerate(table[0]):
        lines.append(f"| {label} | " + " | ".join(rows[i][1] for rows in table) + " |")
    for key, title in (
        ("hard_kinds", "Hard subset by kind (test split)"),
        ("hard_kinds_dev", "Hard dev set by kind (dev split)"),
    ):
        if not report.get(key):
            continue
        lines += [
            "",
            f"### {title}",
            "",
            "| Kind | Invoices | Field acc. | False fill | Missing | Wrong | Line F1 |",
            "|---|---|---|---|---|---|---|",
        ]
        for kind, m in report[key].items():
            lines.append(
                f"| {kind} | {m['invoices']} | {pct(m['field_accuracy'])} | "
                f"{pct(m['false_fill_rate'])} | {pct(m['missing_rate'])} | "
                f"{pct(m['wrong_rate'])} | {pct(m['line_item_f1'])} |"
            )
    lines += [
        "",
        "### Per file type (test split)",
        "",
        "| File type | Invoices | Field acc. | False fill | Missing | Line F1 | Flag P / R "
        "| Docs ok | p50 s/doc |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    counts = defaultdict(int)
    for inv in report["invoice_scores"]:
        if inv["split"] == "test" and inv.get("subset", "clean") == "clean":
            counts[inv["file_type"]] += 1
    for ftype, m in test["per_file_type"].items():
        lines.append(
            f"| {ftype} | {counts[ftype]} | {pct(m['field_accuracy'])} | "
            f"{pct(m['false_fill_rate'])} | "
            f"{pct(m['missing_rate'])} | {pct(m['line_item_f1'])} | {pct(m['flag_precision'])} / "
            f"{pct(m['flag_recall'])} | {pct(m['document_success_rate'])} | "
            f"{num(m['p50_seconds_per_document'], 1)} |"
        )

    lines += [
        "",
        "### Per field (test split)",
        "",
        "| Field | Accuracy | False fill | Missing | Wrong | n |",
        "|---|---|---|---|---|---|",
    ]
    for name, m in test["fields"].items():
        lines.append(
            f"| {name} | {pct(m['accuracy'])} | {pct(m['false_fill_rate'])} | "
            f"{pct(m['missing_rate'])} | {pct(m['wrong_rate'])} | {m['n']} |"
        )

    lines += [
        "",
        "### Validation flags by code (test split)",
        "",
        "| Code | Precision | Recall | TP | FP | FN |",
        "|---|---|---|---|---|---|",
    ]
    for code, m in test["flags"]["per_code"].items():
        lines.append(
            f"| {code} | {pct(m['precision'])} | {pct(m['recall'])} | "
            f"{m['tp']} | {m['fp']} | {m['fn']} |"
        )

    dup = test["duplicates"]
    lines += [
        "",
        "### Duplicates, segmentation, cross-checks",
        "",
        f"- **Duplicate detection:** {dup['detected']}/{dup['pairs']} planted pair(s) flagged; "
        f"{dup['false_alarms']} false alarm(s) on other invoices.",
    ]
    for seg in report["segmentation"]:
        lines.append(
            f"- **Segmentation** of `{seg['file']}`: {seg['exact']}/{seg['expected']} invoices "
            f"with exactly the right page range (found {seg['predicted']}); "
            f"expected {seg['expected_segments']}, "
            f"got {seg['predicted_segments']}."
        )
    for cc in report["cross_checks"]:
        missed = [
            f"{x['kind']} {x['invoice_number']}" for x in cc["metrics"]["details"] if not x["found"]
        ]
        lines.append(
            f"- **Cross-check** `{cc['a']}` vs `{cc['b']}` ({cc['split']}): "
            f"{cc['metrics']['found']}/"
            f"{cc['metrics']['expected']} planted findings found, "
            f"{cc['metrics']['extra_findings']} extra"
            + (f"; missed: {', '.join(missed)}" if missed else "")
            + "."
        )

    a = report["all_files"]
    lines += [
        "",
        "### LLM usage and cost (all files, both splits)",
        "",
        f"- {a['llm_calls']} LLM calls, {a['input_tokens']:,} input tokens "
        f"({a['cached_input_tokens']:,} of them prompt-cache hits), "
        f"{a['output_tokens']:,} output tokens.",
        f"- Estimated cost: **${num(a['cost_usd'], 4)}** in total, "
        f"**${num(a['cost_usd_per_document'], 5)}** per document, at "
        f"${report['prices']['input']} / ${report['prices']['output']} per million input / "
        "output tokens. Cached input is priced at the full rate, so this is an upper bound.",
    ]
    ae = report.get("assistant_eval")
    if ae:
        lines += [
            "",
            "### Assistant (docs/assistant-eval.md, 15 questions)",
            "",
            f"- Right number: {ae['right_number']}/{ae['questions']}, right citation: "
            f"{ae['right_citation']}/{ae['questions']}, "
            f"both: {ae['both_right']}/{ae['questions']}.",
            f"- {ae.get('llm_calls', '–')} LLM calls, {ae.get('input_tokens', 0):,} input / "
            f"{ae.get('output_tokens', 0):,} output tokens (corpus extraction + questions).",
        ]
        wrong = [r for r in ae.get("results", []) if not (r["value_ok"] and r["citation_ok"])]
        for r in wrong[:5]:
            lines.append(
                f'  - Q{r["number"]} "{r["question"]}": expected {r["expected"]}; answer: '
                f'"{" ".join(str(r["answer"]).split())[:160]}"'
            )
    lines += ["", "### Failure examples (clean test invoices, from this run)", ""]
    lines += _failure_table(
        report["failure_examples"],
        "No wrong, missing or invented field values on the clean test invoices in this run.",
    )
    if report.get("hard_kinds"):
        lines += ["", "### Failure examples (hard subset, from this run)", ""]
        lines += _failure_table(
            report.get("failure_examples_hard", []),
            "No wrong, missing or invented field values on the hard subset in this run.",
        )
    failed = [r for r in report["files"] if r["status"] not in ("processed", "needs_review")]
    if failed:
        lines += ["", "Documents that did not process:", ""]
        lines += [f"- `{r['file']}`: {r['status']} ({r['error']})" for r in failed]
    lines.append(END)
    return "\n".join(lines)


def write_docs(report: dict) -> None:
    path = DOCS / "evaluation.md"
    generated = render(report)
    if path.exists() and BEGIN in path.read_text():
        text = path.read_text()
        text = re.sub(
            re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: generated, text, flags=re.S
        )
    else:
        text = f"# Evaluation\n\n{generated}\n"
    path.write_text(text)


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://api:8000")
    parser.add_argument(
        "--timeout", type=float, default=3600, help="seconds to wait for processing"
    )
    parser.add_argument("--skip-assistant", action="store_true")
    parser.add_argument("--real", action="store_true", help="also score eval/dataset/real/")
    args = parser.parse_args()

    run_at = datetime.now(UTC).replace(microsecond=0)
    price = lambda name: Decimal(os.environ[name]) if os.environ.get(name) else None  # noqa: E731
    prices = {
        "input": price("LLM_PRICE_INPUT_PER_MTOK"),
        "output": price("LLM_PRICE_OUTPUT_PER_MTOK"),
    }
    manifest, labels, paths = load_dataset(args.real)
    api = Api(args.api)
    started = time.monotonic()
    docs = process(api, manifest, paths, args.timeout)

    numbers = {  # invoice id -> extracted invoice number, to label line-level findings
        inv["invoice_id"]: ((inv.get("fields") or {}).get("invoice_number") or {}).get("value")
        for doc in docs.values()
        for inv in doc.get("invoices") or []
    }
    cross_checks = []
    for cc in manifest["cross_checks"]:
        a, b = docs[cc["a"]].get("id"), docs[cc["b"]].get("id")
        check = api.cross_check(a, b) if a and b else {"status": "not run", "findings": []}
        for f in check.get("findings", []):
            f["invoice_number_a"] = numbers.get(f.get("invoice_a_id"))
            f["invoice_number_b"] = numbers.get(f.get("invoice_b_id"))
        cross_checks.append(
            {
                **cc,
                "status": check["status"],
                "findings": check.get("findings", []),
                "metrics": scoring.crosscheck_metrics(
                    cc["expected_findings"], check.get("findings", [])
                ),
            }
        )
    assistant = None if args.skip_assistant else run_assistant_eval()

    scored = score(manifest, labels, docs, prices)
    provider = os.environ.get("LLM_PROVIDER", "none")
    model = os.environ.get("LLM_MODEL") or {
        "openai": "gpt-6-luna",
        "gemini": "gemini-2.5-flash",
        "groq": "openai/gpt-oss-120b",
    }.get(provider, "none")
    report = {
        "run_at": run_at.isoformat(),
        "dataset": "synthetic+real"
        if any(x.get("dataset") == "real" for x in labels)
        else "synthetic",
        "provider": provider,
        "model": model,
        "prices": {k: (str(v) if v is not None else None) for k, v in prices.items()},
        "wall_seconds": round(time.monotonic() - started, 1),
        "results_file": run_at.strftime("%Y-%m-%dT%H-%M-%SZ") + ".json",
        "headline_split": "test",
        **scored,
        "all_files": summary([], scored["files"], prices),
        "cross_checks": cross_checks,
        "assistant_eval": assistant,
    }
    report["failure_examples"] = failure_examples(report)
    report["failure_examples_hard"] = failure_examples(report, subset="hard")
    t = report["splits"]["test"]["overall"]
    hard = report["splits"]["test_hard"]["overall"]
    ae = assistant or {}
    report["headline"] = {
        **{
            k: t[k]
            for k in (
                "field_accuracy",
                "false_fill_rate",
                "missing_rate",
                "line_item_f1",
                "flag_precision",
                "flag_recall",
                "document_success_rate",
                "p50_seconds_per_document",
                "p95_seconds_per_document",
            )
        },
        "cost_usd_per_document": report["all_files"]["cost_usd_per_document"],
        # The realistic counterpart of the clean numbers (None if the subset is absent).
        "hard_field_accuracy": hard["field_accuracy"],
        "hard_false_fill_rate": hard["false_fill_rate"],
        "hard_missing_rate": hard["missing_rate"],
        "hard_line_item_f1": hard["line_item_f1"],
        "segmentation_exact": (
            sum(s["exact"] for s in scored["segmentation"])
            / max(1, sum(s["expected"] for s in scored["segmentation"]))
        ),
        "crosscheck_recall": (
            sum(c["metrics"]["found"] for c in cross_checks)
            / max(1, sum(c["metrics"]["expected"] for c in cross_checks))
        ),
        "duplicates_detected": report["splits"]["all"]["duplicates"]["detected"],
        "assistant_both_right": (ae["both_right"] / ae["questions"])
        if ae.get("questions")
        else None,
    }
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / report["results_file"]
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n")
    write_docs(report)
    print(
        json.dumps({"results": str(out.relative_to(EVAL.parent)), **report["headline"]}, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
