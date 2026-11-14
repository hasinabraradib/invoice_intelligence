"""Store a run_eval.py report's headline in the database (the dashboard shows the latest).

python scripts/store_eval_run.py /results/2026-11-14T10-00-00.json
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.config import get_settings  # noqa: E402
from app.db import create_engine, create_sessionmaker  # noqa: E402
from app.evaluation.store import save_run  # noqa: E402


def newest_run(path: Path) -> Path:
    """A results file, or the newest run in a folder: only run files (named by a sortable
    timestamp), never other JSON kept there such as hard-dev-bench results."""
    if not path.is_dir():
        return path
    return sorted(path.glob("[0-9][0-9][0-9][0-9]-*.json"))[-1]


async def main(path: Path, report: dict) -> None:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_sessionmaker(engine)() as session:
            run = await save_run(session, report, path.name)
            print(f"stored evaluation run {run.id} ({path.name})")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    results = newest_run(Path(sys.argv[1]))
    asyncio.run(main(results, json.loads(results.read_text())))
