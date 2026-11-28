"""Calendar-date parsing, coverage, gaps and ordering checks."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .models import CsvFile, Issue

FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
)


def parse_date(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass
    for fmt in FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def validate_dates(csv_file: CsvFile) -> tuple[list[Issue], list[date]]:
    issues: list[Issue] = []
    dates: list[date] = []
    for record in csv_file.records:
        raw = record.values.get("date", "")
        if not raw:
            # Date may be embedded in one of the timestamp columns.
            raw = next((record.values.get(k, "") for k in ("datetime_utc", "datetime_local", "datetime", "date_time") if record.values.get(k)), "")
        parsed = parse_date(raw)
        if parsed is None:
            if raw:
                issues.append(Issue("WARNING", "INVALID_DATE", f"Cannot parse date value: {raw}", csv_file.relative_path, record.row_number, "date"))
            elif not any(record.values.get(k) for k in ("datetime_utc", "datetime_local", "datetime", "date_time", "date")):
                issues.append(Issue("WARNING", "MISSING_DATE", "No date/timestamp value in row", csv_file.relative_path, record.row_number, "date"))
            continue
        dates.append(parsed)

    if dates:
        if dates != sorted(dates):
            issues.append(Issue("WARNING", "DATE_ORDER_NOT_MONOTONIC", "Rows are not ordered by date", csv_file.relative_path))
        unique_days = sorted(set(dates))
        # Report gaps, but do not treat weekends/holidays as data errors.
        gaps = [(left, right) for left, right in zip(unique_days, unique_days[1:]) if right - left > timedelta(days=1)]
        if gaps:
            preview = ", ".join(f"{a.isoformat()}..{b.isoformat()}" for a, b in gaps[:8])
            issues.append(Issue("INFO", "DATE_GAPS", f"{len(gaps)} calendar gaps detected (weekends/holidays may explain them): {preview}", csv_file.relative_path))
    return issues, dates
