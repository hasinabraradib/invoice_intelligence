"""Write docs/test-results.md from the raw files in docs/test-results/.

Reads JUnit XML (backend, frontend, e2e), coverage.xml (backend, Cobertura) and
coverage-summary.json (frontend, Istanbul). Run by `make test-report`; the commit hash and
date come from the caller (GIT_COMMIT, GIT_DATE env vars).
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path


def junit(path: Path) -> dict:
    root = ET.parse(path).getroot()  # noqa: S314 (our own test output, not untrusted)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    cases = []
    for suite in suites:
        for case in suite.iter("testcase"):
            total["tests"] += 1
            if case.find("failure") is not None:
                total["failures"] += 1
            if case.find("error") is not None:
                total["errors"] += 1
            if case.find("skipped") is not None:
                total["skipped"] += 1
            seconds = float(case.get("time") or 0)
            total["time"] += seconds
            name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
            cases.append((seconds, name))
    total["passed"] = total["tests"] - total["failures"] - total["errors"] - total["skipped"]
    total["slowest"] = sorted(cases, reverse=True)[:10]
    return total


def backend_coverage(path: Path) -> tuple[float, list[tuple[str, float, int]]]:
    """Line coverage overall and per top-level package (app/api, app/extraction, ...)."""
    root = ET.parse(path).getroot()  # noqa: S314 (our own test output, not untrusted)
    lines = defaultdict(lambda: [0, 0])  # package -> [covered, total]
    for cls in root.iter("class"):
        # Paths are relative to the source root (app/): "api/documents.py" or "config.py".
        parts = cls.get("filename", "").removeprefix("app/").split("/")
        package = "app/" + (parts[0] + "/" if len(parts) > 1 else parts[0])
        for line in cls.iter("line"):
            lines[package][1] += 1
            lines[package][0] += int(line.get("hits", "0")) > 0
    rows = sorted((pkg, 100 * c / t if t else 0.0, t) for pkg, (c, t) in lines.items())
    return float(root.get("line-rate", 0)) * 100, rows


def frontend_coverage(path: Path) -> tuple[float, list[tuple[str, float, int]]]:
    data = json.loads(path.read_text())
    # Vitest writes absolute paths (they name the local machine's folders): store them
    # relative to the repository instead, since this file is committed.
    data = {
        k if k == "total" or "/src/" not in k else "frontend/src/" + k.split("/src/", 1)[1]: v
        for k, v in data.items()
    }
    path.write_text(json.dumps(data, indent=1) + "\n")
    groups = defaultdict(lambda: [0, 0])
    for filename, summary in data.items():
        if filename == "total":
            continue
        rel = filename.split("/src/", 1)[-1]
        parts = rel.split("/")
        group = "src/" + ("/".join(parts[:2]) if len(parts) > 2 else parts[0])
        groups[group][0] += summary["lines"]["covered"]
        groups[group][1] += summary["lines"]["total"]
    rows = sorted((g, 100 * c / t if t else 0.0, t) for g, (c, t) in groups.items())
    return data["total"]["lines"]["pct"], rows


def counts_row(name: str, r: dict) -> str:
    return (
        f"| {name} | {r['tests']} | {r['passed']} | {r['failures'] + r['errors']} "
        f"| {r['skipped']} | {r['time']:.1f} s |"
    )


def main(results: Path, out: Path) -> None:
    commit = os.environ.get("GIT_COMMIT", "unknown")
    date = os.environ.get("GIT_DATE") or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    suites = {}
    for name, file in [
        ("Backend (pytest, including slow tests)", "backend-junit.xml"),
        ("Frontend (Vitest)", "frontend-junit.xml"),
        ("End-to-end smoke (Playwright)", "e2e-junit.xml"),
    ]:
        if (results / file).exists():
            suites[name] = junit(results / file)

    lines = [
        "# Test results",
        "",
        f"- **Date:** {date}",
        f"- **Commit:** `{commit}` (the code under test; this report is committed after it)",
        "- **How to reproduce:** `make up` then `make test-report`",
        "- **Raw files:** [test-results/](test-results/) (JUnit XML, backend `coverage.xml`, "
        "frontend `coverage-summary.json`)",
        "",
        "## Counts",
        "",
        "| Suite | Tests | Passed | Failed | Skipped | Time |",
        "|---|---|---|---|---|---|",
        *[counts_row(name, r) for name, r in suites.items()],
        "",
    ]
    if (results / "coverage.xml").exists():
        total, rows = backend_coverage(results / "coverage.xml")
        lines += [
            "## Backend coverage (lines)",
            "",
            f"Overall: **{total:.1f}%**. Code that runs in child processes (parsing, OCR and "
            "embedding steps in `app/ocr/`, `app/steps.py`, `app/procpool.py`) is exercised by "
            "the tests but not traced there, so those numbers are lower bounds.",
            "",
            "| Package | Lines | Covered |",
            "|---|---|---|",
            *[f"| `{pkg}` | {n} | {pct:.1f}% |" for pkg, pct, n in rows],
            "",
        ]
    summary = results / "frontend-coverage" / "coverage-summary.json"
    if summary.exists():
        total, rows = frontend_coverage(summary)
        lines += [
            "## Frontend coverage (lines, unit tests only)",
            "",
            f"Overall: **{total:.1f}%**. Pages are covered by the end-to-end smoke test, not "
            "by unit tests, so their unit coverage is low by design.",
            "",
            "| Folder | Lines | Covered |",
            "|---|---|---|",
            *[f"| `{g}` | {n} | {pct:.1f}% |" for g, pct, n in rows],
            "",
        ]
    for name, r in suites.items():
        if not r["slowest"]:
            continue
        lines += [
            f"## Slowest tests: {name}",
            "",
            "| Time | Test |",
            "|---|---|",
            *[f"| {s:.2f} s | `{t}` |" for s, t in r["slowest"]],
            "",
        ]
    out.write_text("\n".join(lines))
    print(f"wrote {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
