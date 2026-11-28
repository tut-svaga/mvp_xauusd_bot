"""Unified CLI: python -m research.historical_reports.tasks <task> [args]."""
from __future__ import annotations

import argparse
import sys

from .legacy import TASK_FILES, run_legacy_task


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.historical_reports.tasks",
        description="Unified entry point for historical XAUUSD research tasks.",
    )
    parser.add_argument(
        "task",
        choices=tuple(TASK_FILES),
        help="multi-event: all economic reports; stage-c: eight US releases; stage-d: weighted forecast model",
    )
    args, remainder = parser.parse_known_args()
    try:
        return run_legacy_task(args.task, remainder)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"ERROR [{args.task}]: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
