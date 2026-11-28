"""Compatibility bridge to the proven root-level research implementations.

The algorithms remain in their original files during the architecture migration.
This bridge gives them stable package imports and a unified command interface
without copying or silently changing calculation logic.
"""
from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_FILES = {
    "multi-event": "multi_event_reaction_analyzer.py",
    "stage-c": "stage_c_xauusd_analyzer_corrected.py",
    "stage-d": "stage_d_weighted_forecast.py",
}


@lru_cache(maxsize=len(TASK_FILES))
def load_legacy_task(task: str) -> ModuleType:
    """Load one existing task module by its stable public task name."""
    try:
        filename = TASK_FILES[task]
    except KeyError as exc:
        raise ValueError(
            f"Unknown task {task!r}. Choose one of: {', '.join(TASK_FILES)}"
        ) from exc

    path = REPO_ROOT / filename
    if not path.is_file():
        raise FileNotFoundError(f"Task implementation not found: {path}")

    module_name = f"_xauusd_legacy_{task.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def run_legacy_task(task: str, argv: list[str] | None = None) -> int:
    """Run the original CLI while preserving its argument parser and exit code."""
    module = load_legacy_task(task)
    if not hasattr(module, "main"):
        raise AttributeError(f"{TASK_FILES[task]} does not expose main()")

    previous_argv = sys.argv[:]
    try:
        sys.argv = [str(REPO_ROOT / TASK_FILES[task]), *(argv or [])]
        result = module.main()
        return int(result) if result is not None else 0
    finally:
        sys.argv = previous_argv
