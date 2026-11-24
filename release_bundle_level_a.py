#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds corrected USD release bundles for the eight selected US indicators.

Important:
- The calendar DATE is taken from the source CSV.
- The scheduled release time is normalized to 08:30 America/New_York.
- The resulting timestamp is converted to UTC with historical DST rules.
- This script does not silently alter the source CSV files.
- It creates a corrected working dataset and diagnostics under the output folder.

Python 3.9+ (zoneinfo is built in).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
SCHEDULED_TIME = time(8, 30)

TARGETS = {
    "core_pce": {
        "path": Path("inflation/core_pce_price_index_m_m.csv"),
        "name": "Core PCE Price Index m/m",
        "aliases": {"core pce price index m/m"},
    },
    "prelim_gdp": {
        "path": Path("growth/prelim_gdp_q_q.csv"),
        "name": "Prelim GDP q/q",
        "aliases": {"prelim gdp q/q", "preliminary gdp q/q"},
    },
    "prelim_gdp_price": {
        "path": Path("inflation/prelim_gdp_price_index_q_q.csv"),
        "name": "Prelim GDP Price Index q/q",
        "aliases": {"prelim gdp price index q/q", "preliminary gdp price index q/q"},
    },
    "core_durable": {
        "path": Path("growth/core_durable_goods_orders_m_m.csv"),
        "name": "Core Durable Goods Orders m/m",
        "aliases": {"core durable goods orders m/m", "core durable goods orders"},
    },
    "durable": {
        "path": Path("growth/durable_goods_orders_m_m.csv"),
        "name": "Durable Goods Orders m/m",
        "aliases": {"durable goods orders m/m", "durable goods orders"},
    },
    "personal_spending": {
        "path": Path("consumer/personal_spending_m_m.csv"),
        "name": "Personal Spending m/m",
        "aliases": {"personal spending m/m", "personal consumption expenditures m/m"},
    },
    "personal_income": {
        "path": Path("consumer/personal_income_m_m.csv"),
        "name": "Personal Income m/m",
        "aliases": {"personal income m/m"},
    },
    "claims": {
        "path": Path("employment/unemployment_claims.csv"),
        "name": "Unemployment Claims",
        "aliases": {"unemployment claims", "initial jobless claims", "initial claims"},
    },
}

def norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip()).casefold()

def parse_date(row: dict) -> date | None:
    # Use the wall-calendar date from DateTime first; do not convert it to UTC
    # before extracting the date, because the source field may use an odd offset.
    candidates = [row.get("DateTime"), row.get("date"), row.get("datetime_local"),
                  row.get("datetime_utc")]
    for value in candidates:
        if not value:
            continue
        s = str(value).strip()
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
    return None

def parse_source_datetime(row: dict) -> datetime | None:
    for key in ("DateTime", "datetime_utc", "datetime_local"):
        value = row.get(key)
        if not value:
            continue
        try:
            return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            continue
    return None

def read_csv(path: Path):
    # utf-8-sig handles BOM; replacement avoids aborting on isolated bad bytes.
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            return
        for row in reader:
            yield {str(k).strip(): (v.strip() if isinstance(v, str) else v)
                   for k, v in row.items() if k is not None}

def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(dict.fromkeys(k for row in rows for k in row.keys()))
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--economic-data", required=True, type=Path,
                    help="Root folder containing economic_data CSV files")
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    root = args.economic_data.resolve()
    out = args.output.resolve()
    events_by_utc = defaultdict(list)
    quality = []
    corrected_rows = []
    source_files = []

    for key, spec in TARGETS.items():
        path = root / spec["path"]
        if not path.exists():
            quality.append({"file": str(path), "issue": "target_file_missing"})
            continue
        source_files.append(path.resolve())
        rows_seen = 0
        for line_no, row in enumerate(read_csv(path), start=2):
            rows_seen += 1
            currency = norm(row.get("Currency") or row.get("currency"))
            if currency != "usd":
                quality.append({"file": str(path), "line": line_no,
                                "issue": "non_usd_row_skipped",
                                "currency": row.get("Currency") or row.get("currency", "")})
                continue

            event_text = row.get("Event") or row.get("event") or ""
            if norm(event_text) not in spec["aliases"]:
                quality.append({"file": str(path), "line": line_no,
                                "issue": "event_name_mismatch",
                                "expected": spec["name"], "found": event_text})
                # File path is the primary indicator, so retain row but make mismatch visible.

            d = parse_date(row)
            if d is None:
                quality.append({"file": str(path), "line": line_no,
                                "issue": "date_unparseable",
                                "DateTime": row.get("DateTime", "")})
                continue

            original_dt = parse_source_datetime(row)
            corrected_local = datetime.combine(d, SCHEDULED_TIME, tzinfo=NY)
            corrected_utc = corrected_local.astimezone(UTC)
            original_utc = (original_dt.astimezone(UTC)
                            if original_dt and original_dt.tzinfo else None)

            item = {
                "target_key": key,
                "indicator": spec["name"],
                "source_file": str(path),
                "source_line": line_no,
                "date": d.isoformat(),
                "currency": "USD",
                "impact": row.get("Impact") or row.get("impact", ""),
                "event_source": event_text,
                "actual": row.get("Actual") or row.get("actual", ""),
                "forecast": row.get("Forecast") or row.get("forecast", ""),
                "previous": row.get("Previous") or row.get("previous", ""),
                "source_datetime": row.get("DateTime", ""),
                "source_datetime_utc": iso(original_utc) if original_utc else "",
                "corrected_datetime_new_york": iso(corrected_local),
                "corrected_datetime_utc": iso(corrected_utc),
                "time_was_changed": bool(original_utc and original_utc != corrected_utc),
                "time_was_missing_or_unparseable": original_dt is None,
            }
            corrected_rows.append(item)
            events_by_utc[corrected_utc].append(item)

        quality.append({"file": str(path), "issue": "file_processed",
                        "rows_seen": rows_seen})

    # Build one event per exact corrected timestamp. Never count each component
    # as a separate XAUUSD event.
    release_events = []
    composition_counts = Counter()
    for event_utc, components in sorted(events_by_utc.items()):
        keys = sorted({x["target_key"] for x in components})
        names = sorted({x["indicator"] for x in components})
        composition = " + ".join(names)
        composition_counts[composition] += 1
        duplicate_keys = [k for k, n in Counter(x["target_key"] for x in components).items() if n > 1]
        local = event_utc.astimezone(NY)
        release_events.append({
            "event_datetime_utc": iso(event_utc),
            "event_datetime_new_york": iso(local),
            "event_date": local.date().isoformat(),
            "component_count": len(keys),
            "bundle_composition": composition,
            "target_keys": "|".join(keys),
            "actual_available_count": sum(bool(x["actual"]) for x in components),
            "forecast_available_count": sum(bool(x["forecast"]) for x in components),
            "duplicate_target_keys": "|".join(duplicate_keys),
            "component_rows": len(components),
        })
        if duplicate_keys:
            quality.append({"issue": "duplicate_indicator_at_same_timestamp",
                            "event_datetime_utc": iso(event_utc),
                            "target_keys": "|".join(duplicate_keys)})

    # Composition cohorts: actual observed component sets, not assumed eras.
    comp_rows = []
    for composition, n in composition_counts.most_common():
        comp_rows.append({"bundle_composition": composition, "event_count": n})

    # Timeline of first occurrence and first run of 3 consecutive full bundles.
    full_set = set(TARGETS)
    full_events = []
    for ts, comps in sorted(events_by_utc.items()):
        present = {x["target_key"] for x in comps}
        if present == full_set:
            full_events.append(ts)
    sustained_start = ""
    if len(full_events) >= 3:
        # "Sustained" is deliberately operational: first of the first 3 full-bundle
        # occurrences. It does not claim a universal release cadence.
        sustained_start = iso(full_events[0])

    period_rows = [{
        "metric": "first_full_8_indicator_bundle",
        "value": iso(full_events[0]) if full_events else "",
        "definition": "First exact timestamp containing all 8 target indicators"
    }, {
        "metric": "first_3_full_bundle_occurrences",
        "value": sustained_start,
        "definition": "First occurrence in the first sequence of 3 full-bundle timestamps; inspect dates for cadence"
    }, {
        "metric": "full_bundle_occurrence_count",
        "value": len(full_events),
        "definition": "Count of exact timestamps containing all 8 target indicators"
    }]

    # Scan other CSVs only to flag external co-releases. Their timestamps are
    # not force-corrected because each indicator has its own release schedule.
    target_paths = set(source_files)
    external_by_time = defaultdict(list)
    for p in root.rglob("*.csv"):
        if p.resolve() in target_paths:
            continue
        try:
            for line_no, row in enumerate(read_csv(p), start=2):
                currency = norm(row.get("Currency") or row.get("currency"))
                if currency != "usd":
                    continue
                dt = parse_source_datetime(row)
                if not dt:
                    continue
                # Preserve source time; if naive, don't pretend it is UTC.
                if dt.tzinfo is None:
                    continue
                ts = dt.astimezone(UTC)
                event_name = row.get("Event") or row.get("event") or p.stem
                external_by_time[ts].append({
                    "source_file": str(p), "source_line": line_no,
                    "event": event_name
                })
        except Exception as exc:
            quality.append({"file": str(p), "issue": "external_scan_error",
                            "detail": str(exc)})

    external_rows = []
    for ev in release_events:
        ts = datetime.fromisoformat(ev["event_datetime_utc"])
        matches = external_by_time.get(ts, [])
        external_rows.append({
            "event_datetime_utc": ev["event_datetime_utc"],
            "target_bundle": ev["bundle_composition"],
            "external_usd_event_count": len(matches),
            "external_usd_events": " | ".join(x["event"] for x in matches),
        })

    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "corrected_target_events.csv", corrected_rows)
    write_csv(out / "release_events.csv", release_events)
    write_csv(out / "bundle_composition.csv", comp_rows)
    write_csv(out / "historical_periods.csv", period_rows)
    write_csv(out / "data_quality.csv", quality)
    write_csv(out / "external_co_releases.csv", external_rows)

    summary = {
        "economic_data_root": str(root),
        "output": str(out),
        "target_indicators": len(TARGETS),
        "corrected_indicator_rows": len(corrected_rows),
        "unique_bundle_timestamps": len(release_events),
        "full_8_indicator_bundles": len(full_events),
        "scheduled_release_time": "08:30 America/New_York",
        "timezone_conversion": "ZoneInfo America/New_York -> UTC (historical DST-aware)",
        "source_csv_modified": False,
        "warning": "Release dates are inherited from source files; date changes/reschedules are not inferred.",
    }
    (out / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "USD RELEASE BUNDLE — LEVEL A INPUT PREPARATION",
        "=" * 52,
        f"Target rows: {len(corrected_rows)}",
        f"Unique corrected timestamps: {len(release_events)}",
        f"Full 8-indicator timestamps: {len(full_events)}",
        "Scheduled time applied: 08:30 America/New_York",
        "",
        "Most common observed compositions:",
    ]
    for row in comp_rows[:20]:
        lines.append(f"- {row['event_count']:>5} | {row['bundle_composition']}")
    lines += [
        "",
        "IMPORTANT:",
        "- Dates were not changed.",
        "- All target indicators were assigned 08:30 New York local time.",
        "- UTC conversion uses historical New York DST rules.",
        "- External co-releases retain their original timestamps and are only flagged on exact UTC matches.",
        "- This stage prepares corrected event timestamps; it does not calculate XAUUSD price reactions.",
    ]
    (out / "report.txt").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nReports written to: {out}")

if __name__ == "__main__":
    main()
