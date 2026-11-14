"""Page previews: PDFium is not thread-safe, and the API renders previews in a thread pool.

The review screen and the documents list ask for several thumbnails at once. Rendering
different PDFs at the same time from several threads crashed the API process with a
segmentation fault (found while recording the walkthrough video). The renders run in a
child process here, so a crash fails the test instead of killing pytest.
"""

import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    """
    import sys, tempfile
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    sys.path.insert(0, ".")
    from reportlab.pdfgen import canvas
    from app.previews import page_preview

    work = Path(tempfile.mkdtemp())
    sources = []
    for i in range(12):
        path = work / f"doc-{i}.pdf"
        c = canvas.Canvas(str(path))
        for page in range(3):
            for line in range(40):
                c.drawString(40, 800 - line * 18, f"Invoice {i} page {page} line {line} " * 3)
            c.showPage()
        c.save()
        sources.append(path)

    def render(job):
        i, page, round_ = job
        return page_preview(sources[i], page, work / f"cache-{i}-{round_}")

    jobs = [(i, page, r) for r in range(4) for i in range(12) for page in (1, 2, 3)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(render, jobs))
    assert all(p.exists() and p.stat().st_size > 1000 for p in results)
    print("ok", len(results))
    """
)


def test_many_previews_rendered_at_once_do_not_crash_the_process():
    result = subprocess.run(  # noqa: S603 (our own fixed script, run with this Python)
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=300, check=False
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr[-500:]}"
    assert result.stdout.startswith("ok 144")
