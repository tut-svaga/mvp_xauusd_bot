"""Write Discovery outputs; source data remains read-only."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .models import DiscoveryResult, FileReport


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _counter(counter: Counter[str]) -> str:
    return "; ".join(f"{key}:{value}" for key, value in sorted(counter.items()))


def _file_row(item: FileReport) -> dict[str, Any]:
    return {
        "category": item.category, "file": item.file, "size_bytes": item.size_bytes,
        "rows": item.rows, "currencies": _counter(item.currencies),
        "event_names": "; ".join(sorted(item.event_names)),
        "first_date_seen": item.first_date, "last_date_seen": item.last_date,
        "timestamp_sources": _counter(item.timestamp_sources),
        "timezone_offsets": _counter(item.timezone_offsets),
        "status": item.status,
        "issues": "; ".join(f"{i.code}: {i.message}" for i in item.issues),
        "read_error": item.read_error,
        "delimiter": repr(item.delimiter), "headers": " | ".join(item.headers),
    }


def _markdown(result: DiscoveryResult, summary: dict[str, Any]) -> str:
    lines = [
        "# Research Data Discovery Report", "",
        f"Generated: {result.generated_at_utc}", "",
        "## Overview", "",
        f"- Economic report CSV files scanned: **{summary['report_files']}**",
        f"- Data rows scanned: **{summary['total_report_rows']}**",
        f"- Categories: **{summary['categories']}**",
        f"- Currencies: **{', '.join(summary['currencies']) or 'none'}**",
        f"- Files requiring review: **{summary['files_with_warnings']}**",
        f"- Catalog issues: **{summary['catalog_issue_count']}**",
        f"- Manifest issues: **{summary['manifest_issue_count']}**", "",
        "## Files requiring review", "",
    ]
    flagged = [item for item in result.files if item.status != "OK"]
    if flagged:
        lines += ["| File | Rows | Status | Issues |", "|---|---:|---|---|"]
        for item in flagged[:60]:
            detail = "; ".join(issue.code for issue in item.issues) or item.read_error
            lines.append(f"| `{item.file}` | {item.rows} | {item.status} | {detail} |")
        if len(flagged) > 60:
            lines.append(f"Only the first 60 of {len(flagged)} files are shown; see report_inventory.csv.")
    else:
        lines.append("No file-level warnings were detected.")
    lines += [
        "", "## Timestamp handling", "",
        "- Midnight-only timestamps are flagged for manual review, never auto-corrected.",
        "- Timezone offsets are recorded, not silently converted or assumed.",
        "- A naive value in a field named datetime_utc is labelled UTC_IMPLICIT.",
        "- Calendar gaps are informational: weekends and holidays may explain them.",
        "- Discovery does not modify source CSVs or quote files.", "",
    ]
    return "\n".join(lines)


def export_result(result: DiscoveryResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = result.summary()
    _write_csv(output_dir / "report_inventory.csv", [_file_row(item) for item in result.files])
    _write_csv(output_dir / "catalog_comparison.csv", result.catalog_rows)
    _write_csv(output_dir / "manifest_check.csv", result.manifest_rows)
    _write_csv(output_dir / "auxiliary_sources.csv", result.auxiliary_rows)
    if result.xau_summary is not None:
        _write_csv(output_dir / "xauusd_quote_inventory.csv", result.xau_quote_rows)
    (output_dir / "discovery_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "discovery_report.md").write_text(_markdown(result, summary), encoding="utf-8")
