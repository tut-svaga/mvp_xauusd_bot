"""Publication-time, timezone-offset and ambiguous-midnight checks."""
from __future__ import annotations

from datetime import datetime, timezone

from .models import CsvFile, Issue

TIMESTAMP_FIELDS = ("datetime_utc", "datetime_local", "datetime", "date_time")
DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
)
MISSING = {"", "-", "—", "–", "n/a", "na", "none", "null", "tba", "tentative"}


def parse_datetime(raw: str) -> datetime | None:
    value = (raw or "").strip()
    if not value or value.casefold() in MISSING:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def timezone_label(value: datetime, source: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        return "UTC_IMPLICIT" if source == "datetime_utc" else "NAIVE"
    seconds = int(value.utcoffset().total_seconds())
    if seconds == 0:
        return "UTC"
    sign = "+" if seconds > 0 else "-"
    seconds = abs(seconds)
    hours, remainder = divmod(seconds, 3600)
    return f"{sign}{hours:02d}:{remainder // 60:02d}"


def select_timestamp(values: dict[str, str]) -> tuple[str, datetime | None, bool]:
    saw_midnight = False
    for field in TIMESTAMP_FIELDS:
        raw = values.get(field, "")
        parsed = parse_datetime(raw)
        if parsed is None:
            continue
        if parsed.hour == parsed.minute == parsed.second == parsed.microsecond == 0:
            saw_midnight = True
            continue
        return field, parsed, saw_midnight
    date_value, time_value = values.get("date", ""), values.get("time", "")
    if date_value and time_value:
        parsed = parse_datetime(f"{date_value}T{time_value}")
        if parsed:
            if parsed.hour == parsed.minute == parsed.second == parsed.microsecond == 0:
                saw_midnight = True
            else:
                return "date+time", parsed, saw_midnight
    return "", None, saw_midnight


def validate_times(csv_file: CsvFile) -> tuple[list[Issue], dict[str, int], dict[str, int]]:
    issues: list[Issue] = []
    sources: dict[str, int] = {}
    zones: dict[str, int] = {}
    for record in csv_file.records:
        source, parsed, midnight = select_timestamp(record.values)
        if parsed is None:
            code = "AMBIGUOUS_MIDNIGHT" if midnight else "MISSING_OR_INVALID_TIMESTAMP"
            message = "Timestamp is midnight-only; publication time may be unknown" if midnight else "No valid publication timestamp found"
            issues.append(Issue("WARNING", code, message, csv_file.relative_path, record.row_number, "datetime"))
            continue
        sources[source] = sources.get(source, 0) + 1
        zone = timezone_label(parsed, source)
        zones[zone] = zones.get(zone, 0) + 1
        if zone == "NAIVE":
            issues.append(Issue("WARNING", "TIMEZONE_NOT_SPECIFIED", "Timestamp has no timezone or UTC offset", csv_file.relative_path, record.row_number, source))
    return issues, sources, zones
