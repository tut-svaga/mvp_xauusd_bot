"""Locate and read CSV inputs without modifying source files."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .models import CsvFile, CsvRecord

EXCLUDED_REPORT_FILES = {
    "event_catalog.csv", "skipped_events.csv", "event_reactions.csv",
    "group_stats.csv", "run_summary.csv", "run_summary.json",
    "forex_factory_cache.csv",
}


def normalize_header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lstrip("\ufeff").casefold()


def detect_delimiter(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(8192)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def discover_csv_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"Economic data directory does not exist: {root}")
    return sorted(
        path for path in root.rglob("*.csv")
        if path.name.casefold() not in EXCLUDED_REPORT_FILES
    )


def scan_csv(path: Path, relative_to: Path | None = None) -> CsvFile:
    relative = path.as_posix()
    category = "auxiliary"
    if relative_to is not None:
        try:
            rel = path.relative_to(relative_to)
            relative = rel.as_posix()
            category = rel.parts[0] if len(rel.parts) > 1 else "_root"
        except ValueError:
            pass

    result = CsvFile(
        path=path,
        relative_path=relative,
        category=category,
        byte_size=path.stat().st_size if path.exists() else 0,
    )
    try:
        result.delimiter = detect_delimiter(path)
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=result.delimiter)
            result.headers = [str(h or "").strip() for h in (reader.fieldnames or [])]
            for row_number, row in enumerate(reader, start=2):
                if not row or not any(str(v or "").strip() for k, v in row.items() if k is not None):
                    continue
                values = {
                    normalize_header(key): str(value or "").strip()
                    for key, value in row.items() if key is not None
                }
                if None in row and row[None]:
                    values["__extra_columns__"] = " | ".join(str(v) for v in row[None])
                result.records.append(CsvRecord(row_number, values))
            if not result.headers:
                result.read_error = "empty_file_or_missing_header"
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        result.read_error = f"{type(exc).__name__}: {exc}"
    return result
