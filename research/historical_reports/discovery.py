#!/usr/bin/env python3
"""Inventory and first-pass quality discovery for XAUUSD research data.

Read-only with respect to source data. Scans individual economic-report CSVs,
compares them with event_catalog.csv and .calendar_reports_manifest.json,
optionally inspects the Forex Factory cache and XAUUSD M1 BID/ASK file layout.

Run from the repository root:
    py -3 research/historical_reports/discovery.py

Optional XAUUSD quote-data inspection:
    py -3 research/historical_reports/discovery.py --xau-root "E:\\work\\XAU_gold\\XAUUSD\\raw"

Outputs (default: output/research_discovery):
    report_inventory.csv
    catalog_comparison.csv
    manifest_check.csv
    auxiliary_sources.csv
    xauusd_quote_inventory.csv  (only when --xau-root is supplied)
    discovery_summary.json
    discovery_report.md

Only the Python standard library is required.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ECONOMIC_DATA = REPO_ROOT / "data" / "economic_data"
DEFAULT_CACHE = REPO_ROOT / "data" / "forex_factory_cache.csv"
DEFAULT_OUTPUT = REPO_ROOT / "output" / "research_discovery"

EXCLUDED_REPORT_FILES = {
    "event_catalog.csv", "skipped_events.csv", "event_reactions.csv",
    "group_stats.csv", "run_summary.csv", "run_summary.json",
    "forex_factory_cache.csv",
}
TIMESTAMP_FIELDS = ("datetime_utc", "datetime_local", "DateTime")
DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
)
XAU_FILENAME_RE = re.compile(
    r"XAUUSD_M1_(BID|ASK)_(\d{4}-\d{2}-\d{2})\.csv$", re.IGNORECASE
)


class DiscoveryError(RuntimeError):
    """Fatal discovery input error."""


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_header(value: Any) -> str:
    return clean(value).lstrip("\ufeff").casefold()


def get_value(row: dict[str, Any], *names: str) -> str:
    lowered = {normalize_header(k): v for k, v in row.items() if k is not None}
    for name in names:
        value = clean(lowered.get(normalize_header(name), ""))
        if value:
            return value
    return ""


def is_missing_value(value: str) -> bool:
    return clean(value).casefold() in {
        "", "-", "—", "–", "n/a", "na", "none", "null", "tba", "tentative"
    }


def detect_delimiter(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(8192)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def parse_datetime(raw: str) -> datetime | None:
    value = clean(raw)
    if not value:
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


def is_midnight_timestamp(parsed: datetime) -> bool:
    return (
        parsed.hour == 0 and parsed.minute == 0
        and parsed.second == 0 and parsed.microsecond == 0
    )


def choose_timestamp(row: dict[str, Any]) -> tuple[str, str, datetime | None, bool]:
    """Follow the analyzer's timestamp priority; flag midnight-only values."""
    saw_midnight = False
    for field in TIMESTAMP_FIELDS:
        raw = get_value(row, field)
        if not raw:
            continue
        parsed = parse_datetime(raw)
        if parsed is None:
            continue
        if is_midnight_timestamp(parsed):
            saw_midnight = True
            continue
        return field, raw, parsed, saw_midnight

    date_value = get_value(row, "date")
    time_value = get_value(row, "time")
    if date_value and time_value:
        raw = f"{date_value}T{time_value}"
        parsed = parse_datetime(raw)
        if parsed is not None:
            if is_midnight_timestamp(parsed):
                saw_midnight = True
            else:
                return "date+time", raw, parsed, saw_midnight
    elif date_value:
        parsed = parse_datetime(date_value)
        if parsed is not None:
            saw_midnight = True
    return "", "", None, saw_midnight


def timezone_label(parsed: datetime, source: str = "") -> str:
    # A naive value in a field explicitly named datetime_utc is interpreted as UTC.
    if (parsed.tzinfo is None or parsed.utcoffset() is None) and source.casefold() == "datetime_utc":
        return "UTC_IMPLICIT"
    offset = parsed.utcoffset()
    if offset is None:
        return "NAIVE"
    if offset.total_seconds() == 0:
        return "UTC"
    seconds = int(offset.total_seconds())
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    hours, remainder = divmod(seconds, 3600)
    return f"{sign}{hours:02d}:{remainder // 60:02d}"


def read_csv_header(path: Path) -> tuple[list[str], str]:
    delimiter = detect_delimiter(path)
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            headers = next(reader)
        except StopIteration:
            return [], delimiter
    return [clean(item) for item in headers], delimiter


def scan_csv(path: Path, relative_to: Path | None = None) -> dict[str, Any]:
    """Scan one CSV row-by-row without loading the entire file into memory."""
    result: dict[str, Any] = {
        "file": path.as_posix(),
        "category": "auxiliary",
        "bytes": path.stat().st_size if path.exists() else 0,
        "rows": 0,
        "currencies": Counter(),
        "event_names": set(),
        "timestamp_sources": Counter(),
        "timezone_offsets": Counter(),
        "date_values": [],
        "missing_currency_rows": 0,
        "missing_event_rows": 0,
        "missing_timestamp_rows": 0,
        "ambiguous_midnight_rows": 0,
        "unparseable_timestamp_rows": 0,
        "naive_timestamp_rows": 0,
        "missing_actual_rows": 0,
        "missing_forecast_rows": 0,
        "missing_previous_rows": 0,
        "duplicate_rows": 0,
        "malformed_rows": 0,
        "header": [],
        "delimiter": ",",
        "read_error": "",
    }
    if relative_to is not None:
        try:
            relative = path.relative_to(relative_to)
            result["file"] = relative.as_posix()
            result["category"] = relative.parts[0] if len(relative.parts) > 1 else "_root"
        except ValueError:
            pass

    seen_rows: set[tuple[str, ...]] = set()
    try:
        headers, delimiter = read_csv_header(path)
        result["header"] = headers
        result["delimiter"] = delimiter
        if not headers:
            result["read_error"] = "empty_file_or_missing_header"
            return result

        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            for row in reader:
                if not row or not any(clean(value) for key, value in row.items() if key is not None):
                    continue
                result["rows"] += 1
                if None in row and row[None]:
                    result["malformed_rows"] += 1

                currency = get_value(row, "Currency", "currency").upper()
                event_name = get_value(row, "Event", "event", "event_name")
                actual = get_value(row, "Actual", "actual")
                forecast = get_value(row, "Forecast", "forecast")
                previous = get_value(row, "Previous", "previous")

                if currency:
                    result["currencies"][currency] += 1
                else:
                    result["missing_currency_rows"] += 1
                if event_name:
                    result["event_names"].add(event_name)
                else:
                    result["missing_event_rows"] += 1
                if is_missing_value(actual):
                    result["missing_actual_rows"] += 1
                if is_missing_value(forecast):
                    result["missing_forecast_rows"] += 1
                if is_missing_value(previous):
                    result["missing_previous_rows"] += 1

                source, raw_time, parsed_time, saw_midnight = choose_timestamp(row)
                if parsed_time is None:
                    if saw_midnight:
                        result["ambiguous_midnight_rows"] += 1
                    else:
                        result["missing_timestamp_rows"] += 1
                        if any(get_value(row, field) for field in TIMESTAMP_FIELDS) or get_value(row, "date"):
                            result["unparseable_timestamp_rows"] += 1
                else:
                    raw_date = get_value(row, "date")
                    parsed_date = parse_datetime(raw_date) if raw_date else None
                    result["date_values"].append(
                        (parsed_date.date() if parsed_date else parsed_time.date()).isoformat()
                    )
                    zone_name = timezone_label(parsed_time, source)
                    result["timestamp_sources"][source] += 1
                    result["timezone_offsets"][zone_name] += 1
                    if zone_name == "NAIVE":
                        result["naive_timestamp_rows"] += 1

                duplicate_key = (
                    event_name.casefold(), currency, raw_time,
                    actual.casefold(), forecast.casefold(), previous.casefold(),
                )
                if duplicate_key in seen_rows:
                    result["duplicate_rows"] += 1
                else:
                    seen_rows.add(duplicate_key)

    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        result["read_error"] = f"{type(exc).__name__}: {exc}"
    return result


def counter_to_string(counter: Counter[str]) -> str:
    return "; ".join(f"{key}:{value}" for key, value in sorted(counter.items()))


def report_warnings(item: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if item["read_error"]:
        return ["read_error"]
    headers = {normalize_header(h) for h in item["header"]}
    if not ({"event", "event_name"} & headers):
        warnings.append("missing_event_column")
    if "currency" not in headers:
        warnings.append("missing_currency_column")
    if not ({"datetime_utc", "datetime_local", "datetime", "date"} & headers):
        warnings.append("missing_date_time_columns")
    for key, warning in (
        ("ambiguous_midnight_rows", "midnight_timestamps_need_review"),
        ("missing_timestamp_rows", "missing_or_unparsed_timestamps"),
        ("naive_timestamp_rows", "timestamps_without_timezone"),
        ("duplicate_rows", "duplicate_rows"),
        ("missing_actual_rows", "actual_missing_in_some_rows"),
        ("missing_forecast_rows", "forecast_missing_in_some_rows"),
        ("malformed_rows", "rows_with_extra_columns"),
    ):
        if item[key]:
            warnings.append(warning)
    return warnings


def inventory_row(item: dict[str, Any]) -> dict[str, Any]:
    dates = item["date_values"]
    warnings = report_warnings(item)
    return {
        "category": item["category"],
        "file": item["file"],
        "size_bytes": item["bytes"],
        "rows": item["rows"],
        "currencies": counter_to_string(item["currencies"]),
        "event_names": "; ".join(sorted(item["event_names"])),
        "first_date_seen": min(dates) if dates else "",
        "last_date_seen": max(dates) if dates else "",
        "timestamp_sources": counter_to_string(item["timestamp_sources"]),
        "timezone_offsets": counter_to_string(item["timezone_offsets"]),
        "missing_currency_rows": item["missing_currency_rows"],
        "missing_event_rows": item["missing_event_rows"],
        "missing_timestamp_rows": item["missing_timestamp_rows"],
        "ambiguous_midnight_rows": item["ambiguous_midnight_rows"],
        "unparseable_timestamp_rows": item["unparseable_timestamp_rows"],
        "naive_timestamp_rows": item["naive_timestamp_rows"],
        "missing_actual_rows": item["missing_actual_rows"],
        "missing_forecast_rows": item["missing_forecast_rows"],
        "missing_previous_rows": item["missing_previous_rows"],
        "duplicate_rows": item["duplicate_rows"],
        "malformed_rows": item["malformed_rows"],
        "delimiter": repr(item["delimiter"]),
        "headers": " | ".join(item["header"]),
        "status": "ERROR" if item["read_error"] else ("REVIEW" if warnings else "OK"),
        "warnings": "; ".join(warnings),
        "read_error": item["read_error"],
    }


def load_catalog(catalog_path: Path, scanned: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not catalog_path.is_file():
        return [{"file": "event_catalog.csv", "status": "NOT_FOUND", "details": str(catalog_path)}]
    rows: list[dict[str, Any]] = []
    with catalog_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            relative = clean(row.get("file", "")).replace("\\", "/")
            if not relative:
                continue
            actual = scanned.get(relative)
            try:
                catalog_count = int(clean(row.get("row_count", "0")) or 0)
            except ValueError:
                catalog_count = -1
            actual_count = actual["rows"] if actual else None
            status: list[str] = []
            if actual is None:
                status.append("CATALOG_FILE_NOT_FOUND")
            elif catalog_count != actual_count:
                status.append("ROW_COUNT_MISMATCH")

            catalog_currencies = {
                value.strip().upper()
                for value in clean(row.get("currencies", "")).split(",") if value.strip()
            }
            actual_currencies = set(actual["currencies"]) if actual else set()
            if actual is not None and catalog_currencies != actual_currencies:
                status.append("CURRENCY_MISMATCH")

            catalog_first = clean(row.get("first_date", ""))
            catalog_last = clean(row.get("last_date", ""))
            actual_dates = actual["date_values"] if actual else []
            actual_first = min(actual_dates) if actual_dates else ""
            actual_last = max(actual_dates) if actual_dates else ""
            if actual is not None and (catalog_first != actual_first or catalog_last != actual_last):
                status.append("DATE_RANGE_MISMATCH")

            rows.append({
                "file": relative,
                "catalog_rows": catalog_count,
                "actual_rows": actual_count if actual_count is not None else "",
                "catalog_currencies": ",".join(sorted(catalog_currencies)),
                "actual_currencies": ",".join(sorted(actual_currencies)),
                "catalog_first_date": catalog_first,
                "actual_first_date": actual_first,
                "catalog_last_date": catalog_last,
                "actual_last_date": actual_last,
                "status": "; ".join(status) if status else "OK",
            })

    catalog_files = {row["file"] for row in rows}
    for relative in sorted(set(scanned) - catalog_files):
        actual = scanned[relative]
        dates = actual["date_values"]
        rows.append({
            "file": relative,
            "catalog_rows": "",
            "actual_rows": actual["rows"],
            "catalog_currencies": "",
            "actual_currencies": ",".join(sorted(actual["currencies"])),
            "catalog_first_date": "",
            "actual_first_date": min(dates) if dates else "",
            "catalog_last_date": "",
            "actual_last_date": max(dates) if dates else "",
            "status": "NOT_IN_CATALOG",
        })
    return rows


def check_manifest(manifest_path: Path, scanned_files: set[str]) -> list[dict[str, str]]:
    if not manifest_path.is_file():
        return [{"file": ".calendar_reports_manifest.json", "status": "NOT_FOUND", "details": str(manifest_path)}]
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [{"file": ".calendar_reports_manifest.json", "status": "READ_ERROR", "details": str(exc)}]

    generated = {
        clean(item).replace("\\", "/")
        for item in payload.get("generated_files", []) if clean(item)
    }
    rows = [
        {"file": relative, "status": "MISSING_FROM_DISK", "details": "Listed in manifest but no matching CSV was found"}
        for relative in sorted(generated - scanned_files)
    ]
    rows.extend(
        {"file": relative, "status": "NOT_IN_MANIFEST", "details": "CSV exists but is not listed in generated_files"}
        for relative in sorted(scanned_files - generated)
    )
    if not rows:
        rows.append({"file": "", "status": "OK", "details": "Manifest and report CSV files match"})
    return rows


def scan_xau_files(xau_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not xau_root.is_dir():
        return [], {"path": str(xau_root), "status": "NOT_FOUND", "files": 0, "dates": 0, "unrecognized_csv_files": 0}
    by_date: dict[str, dict[str, list[Path]]] = defaultdict(lambda: {"BID": [], "ASK": []})
    total_csv = 0
    recognized = 0
    for path in xau_root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() != ".csv":
            continue
        total_csv += 1
        match = XAU_FILENAME_RE.search(path.name)
        if not match:
            continue
        recognized += 1
        side, date_value = match.group(1).upper(), match.group(2)
        by_date[date_value][side].append(path)

    rows: list[dict[str, Any]] = []
    paired = 0
    for date_value, sides in sorted(by_date.items()):
        bid_files, ask_files = sides["BID"], sides["ASK"]
        status = "PAIRED" if bid_files and ask_files else ("MISSING_ASK" if bid_files else "MISSING_BID")
        if status == "PAIRED":
            paired += 1
        rows.append({
            "date": date_value,
            "bid_files": len(bid_files),
            "ask_files": len(ask_files),
            "bid_bytes": sum(path.stat().st_size for path in bid_files),
            "ask_bytes": sum(path.stat().st_size for path in ask_files),
            "status": status,
            "bid_paths": " ; ".join(path.relative_to(xau_root).as_posix() for path in bid_files),
            "ask_paths": " ; ".join(path.relative_to(xau_root).as_posix() for path in ask_files),
        })
    return rows, {
        "path": str(xau_root), "status": "OK", "files": recognized,
        "dates": len(by_date), "paired_dates": paired,
        "unpaired_dates": len(by_date) - paired,
        "unrecognized_csv_files": total_csv - recognized,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_report(summary: dict[str, Any], inventory: list[dict[str, Any]], catalog_rows: list[dict[str, Any]], manifest_rows: list[dict[str, str]]) -> str:
    lines = [
        "# Research Data Discovery Report", "",
        f"Generated: {summary['generated_at_utc']}", "",
        "## Overview", "",
        f"- Economic report CSV files scanned: **{summary['report_files']}**",
        f"- Data rows scanned: **{summary['total_report_rows']}**",
        f"- Categories: **{summary['categories']}**",
        f"- Currencies found: **{', '.join(summary['currencies']) or 'none'}**",
        f"- Rows with a selected non-midnight timestamp: **{summary['rows_with_selected_timestamp']}**",
        f"- Ambiguous midnight-only rows: **{summary['ambiguous_midnight_rows']}**",
        f"- Rows with missing/unparsed timestamps: **{summary['missing_or_unparsed_timestamp_rows']}**",
        f"- Duplicate rows detected: **{summary['duplicate_rows']}**", "",
        "## Timestamp source coverage", "",
    ]
    for key, value in sorted(summary["timestamp_source_counts"].items()):
        lines.append(f"- `{key}`: {value}")
    if not summary["timestamp_source_counts"]:
        lines.append("- No usable timestamps found.")
    lines += ["", "## Currency row counts", ""]
    for key, value in sorted(summary["currency_row_counts"].items()):
        lines.append(f"- `{key}`: {value}")
    if not summary["currency_row_counts"]:
        lines.append("- No currency values found.")

    warning_items = [row for row in inventory if row["status"] != "OK"]
    lines += ["", "## Files requiring review", ""]
    if warning_items:
        lines += ["| File | Rows | Status | Warnings |", "|---|---:|---|---|"]
        for row in warning_items[:40]:
            lines.append(f"| `{row['file']}` | {row['rows']} | {row['status']} | {row['warnings'] or row['read_error']} |")
        if len(warning_items) > 40:
            lines.append(f"\nShowing first 40 of {len(warning_items)} files requiring review. See report_inventory.csv for all files.")
    else:
        lines.append("No file-level warnings were detected by the discovery checks.")

    catalog_issues = [row for row in catalog_rows if row.get("status") != "OK"]
    manifest_issues = [row for row in manifest_rows if row.get("status") != "OK"]
    lines += [
        "", "## Catalog and manifest", "",
        f"- Catalog comparison issues: **{len(catalog_issues)}**",
        f"- Manifest consistency issues: **{len(manifest_issues)}**", "",
        "## Interpretation notes", "",
        "- This is an inventory and first-pass structural check, not a final validation of release timestamps.",
        "- Midnight-only timestamps are flagged because they may represent missing release times; they are not automatically corrected.",
        "- Timestamp offsets are recorded as metadata. This script does not decide whether they match the official release timezone.",
        "- The event catalog is compared with the CSV files; scanned CSV contents are the source of truth.",
        "- No source CSV, cache, or quote file is modified.", "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover and inventory XAUUSD research CSV data.")
    parser.add_argument("--economic-data-dir", type=Path, default=DEFAULT_ECONOMIC_DATA)
    parser.add_argument("--calendar-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--no-cache", action="store_true", help="Skip Forex Factory cache scanning")
    parser.add_argument("--xau-root", type=Path, default=None, help="Optional XAUUSD M1 BID/ASK data root")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    economic_root = args.economic_data_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not economic_root.is_dir():
        raise DiscoveryError(f"Economic data directory does not exist: {economic_root}")

    report_files = sorted(
        path for path in economic_root.rglob("*.csv")
        if path.name.casefold() not in EXCLUDED_REPORT_FILES
    )
    scanned_items = [scan_csv(path, economic_root) for path in report_files]
    scanned_map = {item["file"]: item for item in scanned_items}
    inventory = [inventory_row(item) for item in scanned_items]

    catalog_rows = load_catalog(economic_root / "event_catalog.csv", scanned_map)
    manifest_rows = check_manifest(economic_root / ".calendar_reports_manifest.json", set(scanned_map))

    auxiliary_rows: list[dict[str, Any]] = []
    cache_summary: dict[str, Any] | None = None
    if not args.no_cache and args.calendar_cache:
        cache_path = args.calendar_cache.expanduser().resolve()
        if cache_path.is_file():
            cache_item = scan_csv(cache_path)
            cache_summary = {
                "path": str(cache_path),
                "status": "OK" if not cache_item["read_error"] else "ERROR",
                "rows": cache_item["rows"],
                "currencies": dict(cache_item["currencies"]),
                "first_date_seen": min(cache_item["date_values"]) if cache_item["date_values"] else "",
                "last_date_seen": max(cache_item["date_values"]) if cache_item["date_values"] else "",
                "read_error": cache_item["read_error"],
            }
            auxiliary_rows.append({
                "source": "forex_factory_cache", "path": str(cache_path),
                "rows": cache_item["rows"], "currencies": counter_to_string(cache_item["currencies"]),
                "first_date_seen": cache_summary["first_date_seen"],
                "last_date_seen": cache_summary["last_date_seen"],
                "status": cache_summary["status"], "read_error": cache_item["read_error"],
            })
        else:
            cache_summary = {"path": str(cache_path), "status": "NOT_FOUND"}
            auxiliary_rows.append({
                "source": "forex_factory_cache", "path": str(cache_path), "rows": 0,
                "currencies": "", "first_date_seen": "", "last_date_seen": "",
                "status": "NOT_FOUND", "read_error": "",
            })

    xau_rows: list[dict[str, Any]] = []
    xau_summary: dict[str, Any] | None = None
    if args.xau_root is not None:
        xau_rows, xau_summary = scan_xau_files(args.xau_root.expanduser().resolve())

    total_rows = sum(item["rows"] for item in scanned_items)
    currency_counts: Counter[str] = Counter()
    timestamp_source_counts: Counter[str] = Counter()
    for item in scanned_items:
        currency_counts.update(item["currencies"])
        timestamp_source_counts.update(item["timestamp_sources"])

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "economic_data_dir": str(economic_root), "output_dir": str(output_dir),
        "report_files": len(scanned_items), "total_report_rows": total_rows,
        "categories": len({item["category"] for item in scanned_items}),
        "currencies": sorted(currency_counts), "currency_row_counts": dict(currency_counts),
        "timestamp_source_counts": dict(timestamp_source_counts),
        "rows_with_selected_timestamp": sum(sum(item["timestamp_sources"].values()) for item in scanned_items),
        "ambiguous_midnight_rows": sum(item["ambiguous_midnight_rows"] for item in scanned_items),
        "missing_or_unparsed_timestamp_rows": sum(item["missing_timestamp_rows"] for item in scanned_items),
        "duplicate_rows": sum(item["duplicate_rows"] for item in scanned_items),
        "files_with_warnings": sum(1 for row in inventory if row["status"] != "OK"),
        "catalog_issue_count": sum(1 for row in catalog_rows if row.get("status") != "OK"),
        "manifest_issue_count": sum(1 for row in manifest_rows if row.get("status") != "OK"),
        "forex_factory_cache": cache_summary, "xauusd_quotes": xau_summary,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "report_inventory.csv", inventory)
    write_csv(output_dir / "catalog_comparison.csv", catalog_rows)
    write_csv(output_dir / "manifest_check.csv", manifest_rows)
    write_csv(output_dir / "auxiliary_sources.csv", auxiliary_rows)
    if args.xau_root is not None:
        write_csv(output_dir / "xauusd_quote_inventory.csv", xau_rows)
    (output_dir / "discovery_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "discovery_report.md").write_text(
        markdown_report(summary, inventory, catalog_rows, manifest_rows), encoding="utf-8"
    )

    print("Research data discovery completed.")
    print(f"Report files: {summary['report_files']}")
    print(f"Rows: {summary['total_report_rows']}")
    print(f"Currencies: {', '.join(summary['currencies']) or 'none'}")
    print(f"Files requiring review: {summary['files_with_warnings']}")
    print(f"Midnight-only timestamps: {summary['ambiguous_midnight_rows']}")
    print(f"Missing/unparsed timestamps: {summary['missing_or_unparsed_timestamp_rows']}")
    print(f"Catalog issues: {summary['catalog_issue_count']}; manifest issues: {summary['manifest_issue_count']}")
    print(f"Output: {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DiscoveryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
