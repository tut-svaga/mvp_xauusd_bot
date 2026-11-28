"""Coordinate discovery modules and assemble the final result."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .catalog import check_manifest, compare_catalog
from .currencies import validate_currencies
from .dates import validate_dates
from .events import validate_events
from .models import DiscoveryResult, FileReport, Issue
from .scanner import discover_csv_files, scan_csv
from .schema import validate_schema
from .times import validate_times
from .xau_quotes import scan_xau_quotes


def _analyze(path: Path, root: Path) -> FileReport:
    source = scan_csv(path, root)
    report = FileReport(
        file=source.relative_path, category=source.category, size_bytes=source.byte_size,
        rows=len(source.records), headers=source.headers, delimiter=source.delimiter,
        read_error=source.read_error,
    )
    if source.read_error:
        report.issues.append(Issue("ERROR", "CSV_READ_ERROR", source.read_error, source.relative_path))
        return report

    report.issues.extend(validate_schema(source))
    date_issues, date_values = validate_dates(source)
    report.issues.extend(date_issues)
    report.first_date = min((d.isoformat() for d in date_values), default="")
    report.last_date = max((d.isoformat() for d in date_values), default="")
    time_issues, sources, zones = validate_times(source)
    report.issues.extend(time_issues)
    report.timestamp_sources.update(sources)
    report.timezone_offsets.update(zones)
    currency_issues, currency_counts = validate_currencies(source)
    report.issues.extend(currency_issues)
    report.currencies.update(currency_counts)
    event_issues, event_names = validate_events(source)
    report.issues.extend(event_issues)
    report.event_names.update(event_names)
    return report


def run_discovery(
    economic_data_dir: Path,
    output_dir: Path,
    calendar_cache: Path | None = None,
    xau_root: Path | None = None,
) -> DiscoveryResult:
    root = economic_data_dir.expanduser().resolve()
    output = output_dir.expanduser().resolve()
    paths = discover_csv_files(root)
    reports = [_analyze(path, root) for path in paths]
    result = DiscoveryResult(
        generated_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        economic_data_dir=str(root), output_dir=str(output), files=reports,
        catalog_rows=compare_catalog(root, reports),
        manifest_rows=check_manifest(root, {item.file for item in reports}),
    )

    if calendar_cache is not None:
        cache_path = calendar_cache.expanduser().resolve()
        if cache_path.is_file():
            cache = scan_csv(cache_path)
            currencies: Counter[str] = Counter()
            first_dates: list[str] = []
            for record in cache.records:
                currency = record.values.get("currency", "").upper()
                if currency:
                    currencies[currency] += 1
                raw_date = record.values.get("date", "") or record.values.get("datetime_utc", "")
                if raw_date:
                    first_dates.append(raw_date[:10])
            result.cache_summary = {
                "path": str(cache_path), "status": "ERROR" if cache.read_error else "OK",
                "rows": len(cache.records), "currencies": dict(currencies),
                "first_date_seen": min(first_dates, default=""),
                "last_date_seen": max(first_dates, default=""), "read_error": cache.read_error,
            }
            result.auxiliary_rows.append({
                "source": "forex_factory_cache", "path": str(cache_path),
                "rows": len(cache.records), "currencies": "; ".join(f"{k}:{v}" for k, v in sorted(currencies.items())),
                "first_date_seen": min(first_dates, default=""), "last_date_seen": max(first_dates, default=""),
                "status": result.cache_summary["status"], "read_error": cache.read_error,
            })
        else:
            result.cache_summary = {"path": str(cache_path), "status": "NOT_FOUND"}
            result.auxiliary_rows.append({
                "source": "forex_factory_cache", "path": str(cache_path), "rows": 0,
                "currencies": "", "first_date_seen": "", "last_date_seen": "",
                "status": "NOT_FOUND", "read_error": "",
            })

    if xau_root is not None:
        result.xau_quote_rows, result.xau_summary = scan_xau_quotes(xau_root.expanduser().resolve())
    return result
