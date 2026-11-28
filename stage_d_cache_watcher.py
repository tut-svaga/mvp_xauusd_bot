#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watch Forex Factory cache and export Actual/Forecast pairs for Stage D.

The script does not download or modify Forex Factory data. It reads an existing
CSV cache, waits until all eight configured releases have non-empty Actual and
Forecast values for the requested date, then atomically writes a JSON file that
can be passed to stage_d_weighted_forecast.py via --input-json.

Supports both a header row and the positional 21-column layout shown in the
project's Forex Factory cache sample.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("stage_d_cache_watcher")

REPORTS = [
    "Core PCE Price Index m/m",
    "Prelim GDP q/q",
    "Prelim GDP Price Index q/q",
    "Core Durable Goods Orders m/m",
    "Durable Goods Orders m/m",
    "Personal Income m/m",
    "Personal Spending m/m",
    "Unemployment Claims",
]

# Positional layout from the sample supplied in the conversation:
# 0 datetime, 1 currency, 2 impact, 3 title, 4 actual, 5 forecast, 6 previous,
# ... 8 date, ... 14 repeated title, 15 repeated actual, 16 repeated forecast,
# 17 repeated previous, ...
POSITIONAL = {
    "datetime": [0, 10, 11, 8],
    "title": [3, 14],
    "actual": [4, 15],
    "forecast": [5, 16],
    "previous": [6, 17],
}

HEADER_ALIASES = {
    "datetime": {"datetime", "datetimeutc", "eventtime", "eventtimeutc", "timestamp", "timestamputc", "date"},
    "title": {"event", "title", "name", "eventname", "report", "reportname"},
    "actual": {"actual", "actualvalue", "result"},
    "forecast": {"forecast", "forecastvalue", "consensus"},
    "previous": {"previous", "previousvalue", "prior"},
}


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def is_filled(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() not in {"", "-", "--", "—", "n/a", "na", "tbd", "null", "none"}


def parse_date(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    # ISO timestamp, including trailing Z or an explicit UTC offset.
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    # Plain ISO date.
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def detect_header(first_row: list[str]) -> bool:
    if not first_row:
        return False
    # The first cell in the supplied cache is an ISO timestamp, not a header.
    return parse_date(first_row[0]) is None


def header_indices(header: list[str], field: str) -> list[int]:
    aliases = HEADER_ALIASES[field]
    out = []
    for i, cell in enumerate(header):
        n = norm(cell)
        if n in aliases or any(n.startswith(a) and n[len(a):].isdigit() for a in aliases):
            out.append(i)
    return out


def get_values(row: list[str], indices: list[int]) -> list[str]:
    return [row[i].strip() for i in indices if i < len(row) and row[i] is not None]


def first_filled(values: list[str]) -> str:
    for value in values:
        if is_filled(value):
            return value.strip()
    return ""


def event_date_for_row(row: list[str], datetime_indices: list[int]) -> Optional[date]:
    # Try configured datetime/date columns first, then common positional fields.
    indices = list(dict.fromkeys(datetime_indices + POSITIONAL["datetime"]))
    for i in indices:
        if i < len(row):
            d = parse_date(row[i])
            if d is not None:
                return d
    return None


def read_cache(cache_path: Path, target_date: date) -> tuple[dict[str, dict[str, str]], list[str], int]:
    if not cache_path.exists():
        raise FileNotFoundError(f"Cache file not found: {cache_path}")

    with cache_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    if not rows:
        raise ValueError(f"Cache file is empty: {cache_path}")

    has_header = detect_header(rows[0])
    header = rows[0] if has_header else []
    data_rows = rows[1:] if has_header else rows

    indices = {}
    for field in ("datetime", "title", "actual", "forecast", "previous"):
        found = header_indices(header, field) if has_header else []
        indices[field] = found if found else POSITIONAL[field]

    wanted = {norm(name): name for name in REPORTS}
    found: dict[str, dict[str, str]] = {}
    matched_rows = 0

    for row in data_rows:
        if not row:
            continue
        row_date = event_date_for_row(row, indices["datetime"])
        if row_date != target_date:
            continue

        title = first_filled(get_values(row, indices["title"]))
        report_name = wanted.get(norm(title))
        if report_name is None:
            continue
        matched_rows += 1

        record = {
            "actual": first_filled(get_values(row, indices["actual"])),
            "forecast": first_filled(get_values(row, indices["forecast"])),
            "previous": first_filled(get_values(row, indices["previous"])),
        }
        # Prefer a row with Actual, then one with Forecast. This handles a cache
        # containing an older blank snapshot and a newer updated snapshot.
        old = found.get(report_name)
        score = int(is_filled(record["actual"])) * 2 + int(is_filled(record["forecast"]))
        old_score = -1 if old is None else int(is_filled(old["actual"])) * 2 + int(is_filled(old["forecast"]))
        if score >= old_score:
            found[report_name] = record

    missing = []
    for name in REPORTS:
        record = found.get(name)
        if not record or not is_filled(record.get("forecast")):
            missing.append(f"{name} (Forecast отсутствует)")
        elif not is_filled(record.get("actual")):
            missing.append(f"{name} (Actual ещё не опубликован)")

    return found, missing, matched_rows


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    temp_path.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wait for Actual values in Forex Factory cache and export Stage D input JSON."
    )
    parser.add_argument("--cache", required=True, type=Path, help="Path to forex_factory_cache.csv")
    parser.add_argument("--date", required=True, help="Release date in YYYY-MM-DD, e.g. 2026-11-25")
    parser.add_argument("--output-json", required=True, type=Path, help="Output JSON for Stage D --input-json")
    parser.add_argument("--once", action="store_true", help="Check cache once and exit; do not wait")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="Polling interval in watch mode (default: 5)")
    parser.add_argument("--timeout-minutes", type=float, default=75.0, help="Stop waiting after this many minutes (default: 75; 0 = no timeout)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s: %(message)s")

    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        parser.error("--date must use YYYY-MM-DD")

    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be greater than 0")
    if args.timeout_minutes < 0:
        parser.error("--timeout-minutes cannot be negative")

    start = time.monotonic()
    attempt = 0

    while True:
        attempt += 1
        try:
            found, missing, matched_rows = read_cache(args.cache, target_date)
        except Exception as exc:
            LOG.error("Cannot read cache: %s", exc)
            return 1

        LOG.info("Scan %d: date=%s; matching cache rows=%d; reports with rows=%d/8",
                 attempt, target_date.isoformat(), matched_rows, len(found))

        if not missing and all(is_filled(found[name]["actual"]) and is_filled(found[name]["forecast"]) for name in REPORTS):
            payload = {
                name: {
                    "actual": found[name]["actual"],
                    "forecast": found[name]["forecast"],
                    "previous": found[name].get("previous", ""),
                }
                for name in REPORTS
            }
            atomic_write_json(args.output_json, payload)
            LOG.info("All 8 Actual/Forecast pairs are ready. Stage D input saved: %s", args.output_json)
            for name in REPORTS:
                LOG.info("%s: Actual=%s | Forecast=%s", name, payload[name]["actual"], payload[name]["forecast"])
            return 0

        LOG.info("Still waiting for %d field(s):", len(missing))
        for item in missing:
            LOG.info("  - %s", item)

        if args.once:
            LOG.warning("Check completed; no output JSON was written because the full set is not ready.")
            return 2

        elapsed = time.monotonic() - start
        if args.timeout_minutes > 0 and elapsed >= args.timeout_minutes * 60:
            LOG.error("Timeout after %.1f minutes; the full set of 8 reports was not ready.", args.timeout_minutes)
            return 3

        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
