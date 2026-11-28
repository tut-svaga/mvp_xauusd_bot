"""CSV header and row-shape validation."""
from __future__ import annotations

from collections import Counter

from .models import CsvFile, Issue
from .scanner import normalize_header

ALIASES = {
    "event": {"event", "event_name"},
    "currency": {"currency"},
    "date_time": {"datetime_utc", "datetime_local", "datetime", "date", "date_time"},
    "actual": {"actual"},
    "forecast": {"forecast"},
    "previous": {"previous"},
}


def validate_schema(csv_file: CsvFile) -> list[Issue]:
    issues: list[Issue] = []
    if csv_file.read_error:
        return [Issue("ERROR", "CSV_READ_ERROR", csv_file.read_error, csv_file.relative_path)]

    raw_headers = [str(h or "").strip().lstrip("\ufeff") for h in csv_file.headers]
    exact_duplicates = sorted(
        name for name, count in Counter(raw_headers).items()
        if name and count > 1
    )
    if exact_duplicates:
        issues.append(Issue(
            "ERROR", "DUPLICATE_HEADERS",
            f"Repeated identical headers: {', '.join(exact_duplicates)}",
            csv_file.relative_path,
        ))

    headers = [normalize_header(h) for h in raw_headers]
    for logical_name, accepted in ALIASES.items():
        if not (set(headers) & accepted):
            severity = "ERROR" if logical_name in {"event", "date_time"} else "WARNING"
            issues.append(Issue(
                severity, f"MISSING_{logical_name.upper()}_COLUMN",
                f"No recognized {logical_name} column found", csv_file.relative_path,
            ))

    for record in csv_file.records:
        if record.values.get("__conflicting_headers__"):
            issues.append(Issue(
                "WARNING", "CONFLICTING_CASE_VARIANT_COLUMNS",
                "Case-variant columns contain different non-empty values: "
                + record.values["__conflicting_headers__"],
                csv_file.relative_path, record.row_number,
            ))
        if "__extra_columns__" in record.values:
            issues.append(Issue(
                "WARNING", "ROW_EXTRA_COLUMNS",
                "Row contains more values than the header",
                csv_file.relative_path, record.row_number,
            ))
    return issues
