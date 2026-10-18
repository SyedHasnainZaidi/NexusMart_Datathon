"""Run the full pipeline without Airflow (same task functions, same order).

    python scripts/run_pipeline.py              # all tasks
    python scripts/run_pipeline.py --from clean # resume from a task
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexusmart_etl.pipeline import TASKS, _run_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default=TASKS[0][0], choices=[t for t, _ in TASKS])
    ap.add_argument("--only", default=None, choices=[t for t, _ in TASKS])
    args = ap.parse_args()
    run_id = _run_id()
    names = [t for t, _ in TASKS]
    todo = [t for t in TASKS if (args.only and t[0] == args.only) or
            (not args.only and names.index(t[0]) >= names.index(args.start))]
    for name, fn in todo:
        t0 = time.time()
        logging.info("=== %s ===", name)
        result = fn(run_id=run_id)
        logging.info("=== %s done in %.1fs: %s", name, time.time() - t0, result)
