"""Stable command/API layer for the three historical XAUUSD research tasks."""
from .legacy import load_legacy_task, run_legacy_task

__all__ = ["load_legacy_task", "run_legacy_task"]
