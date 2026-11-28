"""Compare discovered report files with the event catalog and manifest."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .models import CsvFile, FileReport


def compare_catalog(root: Path, reports: list[FileReport]) -> list[dict[str, Any]]:
    path = root / "event_catalog.csv"
    if not path.is_file():
        return [{"file": "event_catalog.csv", "status": "NOT_FOUND", "details": str(path)}]
    by_file = {item.file: item for item in reports}
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for item in csv.DictReader(handle):
            relative = (item.get("file") or "").replace("\\", "/").strip()
            if not relative:
                continue
            actual = by_file.get(relative)
            try:
                expected_rows = int((item.get("row_count") or "0").strip() or 0)
            except ValueError:
                expected_rows = -1
            statuses: list[str] = []
            if actual is None:
                statuses.append("CATALOG_FILE_NOT_FOUND")
            elif expected_rows != actual.rows:
                statuses.append("ROW_COUNT_MISMATCH")
            expected_currencies = {x.strip().upper() for x in (item.get("currencies") or "").split(",") if x.strip()}
            actual_currencies = set(actual.currencies) if actual else set()
            if actual and expected_currencies != actual_currencies:
                statuses.append("CURRENCY_MISMATCH")
            expected_first, expected_last = (item.get("first_date") or "").strip(), (item.get("last_date") or "").strip()
            actual_first, actual_last = (actual.first_date, actual.last_date) if actual else ("", "")
            if actual and (expected_first != actual_first or expected_last != actual_last):
                statuses.append("DATE_RANGE_MISMATCH")
            rows.append({
                "file": relative, "catalog_rows": expected_rows,
                "actual_rows": actual.rows if actual else "",
                "catalog_currencies": ",".join(sorted(expected_currencies)),
                "actual_currencies": ",".join(sorted(actual_currencies)),
                "catalog_first_date": expected_first, "actual_first_date": actual_first,
                "catalog_last_date": expected_last, "actual_last_date": actual_last,
                "status": ";".join(statuses) if statuses else "OK",
            })
    catalog_files = {row["file"] for row in rows}
    for relative, actual in sorted(by_file.items()):
        if relative not in catalog_files:
            rows.append({
                "file": relative, "catalog_rows": "", "actual_rows": actual.rows,
                "catalog_currencies": "", "actual_currencies": ",".join(sorted(actual.currencies)),
                "catalog_first_date": "", "actual_first_date": actual.first_date,
                "catalog_last_date": "", "actual_last_date": actual.last_date,
                "status": "NOT_IN_CATALOG",
            })
    return rows


def check_manifest(root: Path, discovered_files: set[str]) -> list[dict[str, str]]:
    path = root / ".calendar_reports_manifest.json"
    if not path.is_file():
        return [{"file": path.name, "status": "NOT_FOUND", "details": str(path)}]
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [{"file": path.name, "status": "READ_ERROR", "details": str(exc)}]
    if not isinstance(payload, dict):
        return [{"file": path.name, "status": "INVALID_FORMAT", "details": "Manifest root must be a JSON object"}]
    generated = {
        str(item).replace("\\", "/").strip()
        for item in payload.get("generated_files", [])
        if str(item).strip()
    }
    rows = [
        {"file": item, "status": "MISSING_FROM_DISK", "details": "Listed in manifest but not discovered"}
        for item in sorted(generated - discovered_files)
    ]
    rows.extend(
        {"file": item, "status": "NOT_IN_MANIFEST", "details": "CSV exists but is absent from generated_files"}
        for item in sorted(discovered_files - generated)
    )
    return rows or [{"file": "", "status": "OK", "details": "Manifest and discovered CSV files match"}]
