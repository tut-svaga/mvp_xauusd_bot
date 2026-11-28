"""Economic-event names, values and duplicate-row validation."""
from __future__ import annotations

from .models import CsvFile, Issue

MISSING = {"", "-", "—", "–", "n/a", "na", "none", "null", "tba", "tentative"}


def validate_events(csv_file: CsvFile) -> tuple[list[Issue], set[str]]:
    issues: list[Issue] = []
    names: set[str] = set()
    seen: set[tuple[str, ...]] = set()
    for record in csv_file.records:
        values = record.values
        name = (values.get("event") or values.get("event_name") or "").strip()
        currency = values.get("currency", "").strip().upper()
        if not name or name.casefold() in MISSING:
            issues.append(Issue("WARNING", "MISSING_EVENT_NAME", "Event name is empty", csv_file.relative_path, record.row_number, "event"))
        else:
            names.add(name)
        for field in ("actual", "forecast", "previous"):
            value = values.get(field, "").strip()
            if value.casefold() in MISSING:
                issues.append(Issue("INFO", f"MISSING_{field.upper()}", f"{field.title()} value is absent", csv_file.relative_path, record.row_number, field))
        timestamp = next((values.get(k, "") for k in ("datetime_utc", "datetime_local", "datetime", "date_time") if values.get(k)), "")
        if not timestamp:
            timestamp = f"{values.get('date', '')} {values.get('time', '')}".strip()
        key = (name.casefold(), currency, timestamp, values.get("actual", "").casefold(),
               values.get("forecast", "").casefold(), values.get("previous", "").casefold())
        if key in seen:
            issues.append(Issue("WARNING", "DUPLICATE_EVENT_ROW", "Duplicate event row detected", csv_file.relative_path, record.row_number))
        else:
            seen.add(key)
    return issues, names
