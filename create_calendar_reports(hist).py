#!/usr/bin/env python3
r"""Build per-event CSV files from Forex Factory calendar sources.

Modes:
  rebuild - replace generated event files using one source (use once for the
            2007-2025 historical database).
  append  - merge a source into existing event files (use for the live cache).

The output schema is kept identical to the current 21-column
forex_factory_cache.csv. Historical 8-column rows are mapped into that schema;
columns absent from the historical source remain empty.

Examples (PowerShell):
  # One-time historical import: REPLACES the generated event CSVs.
  py .\create_calendar_reports.py --mode rebuild `
      --input ".\path\to\historical_2007_2025.csv" `
      --output-dir ".\untitled\data\economic_data"

  # Regular update: ADDS new rows and updates matching existing events.
  py .\create_calendar_reports.py --mode append `
      --input ".\untitled\data\forex_factory_cache.csv" `
      --output-dir ".\untitled\data\economic_data"

If --mode is omitted, append is used for safety.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SOURCE_FILENAME = "forex_factory_cache.csv"
OUTPUT_FOLDER_NAME = "economic_data"
MANIFEST_FILENAME = ".calendar_reports_manifest.json"
CATALOG_FILENAME = "event_catalog.csv"

# This is the schema of the live cache supplied for this project.
# All event files use it, even when the one-time historical source has 8 columns.
OUTPUT_FIELDS = [
    "DateTime", "Currency", "Impact", "Event", "Actual", "Forecast", "Previous", "Detail",
    "date", "time", "datetime_local", "datetime_utc", "currency", "impact", "event",
    "actual", "forecast", "previous", "source", "source_url", "detail_url",
]

# More specific patterns are checked before broader ones.
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "monetary_policy",
        (
            r"\bfomc\b|federal funds rate|fed interest rate|fed rate decision",
            r"interest rate decision|monetary policy|central bank rate",
            r"central bank statement|policy statement|meeting minutes|beige book",
            r"press conference|monetary policy report|fed chair|fed speaks|\bspeech\b",
            r"asset purchase|quantitative easing|balance sheet",
        ),
    ),
    (
        "bonds_and_rates",
        (
            r"\bbond auction\b|treasury auction|treasury bill auction|\bt-bill auction\b",
            r"\btreasury yields?\b|government bond yields?|\b10-y\b|\b30-y\b|\b2-y\b",
            r"bond yields?|government bond|treasury note|treasury bond",
        ),
    ),
    (
        "inflation",
        (
            r"\bcore pce\b|\bpce price index\b|personal consumption expenditures price",
            r"\bcore cpi\b|\bcpi\b|consumer price index|harmonised? index of consumer prices|hicp",
            r"\bcore ppi\b|\bppi\b|producer price index",
            r"import prices?|export prices?|inflation rate|inflation expectations?",
            r"price index|price pressures?",
        ),
    ),
    (
        "employment",
        (
            r"non.?farm payroll|non.?farm employment|employment change|payrolls?",
            r"unemployment claims?|jobless claims?|initial claims?|continuing claims?",
            r"unemployment rate|employment rate|jobless rate|claimant count",
            r"average hourly earnings|hourly wages?|wage growth|employment cost index|labor cost",
            r"\badp\b.*employment|employment.*\badp\b|jolts|job openings",
            r"labor market|labour market|employment index|redundancies",
        ),
    ),
    (
        "growth",
        (
            r"\bgdp\b|gross domestic product|gdp price index|gdp deflator",
            r"retail sales|core retail sales",
            r"durable goods|factory orders|manufacturing orders|goods orders",
            r"industrial production|capacity utilization|capacity utilisation",
            r"business inventories|wholesale inventories|inventories m/m",
            r"productivity|unit labor costs|unit labour costs",
        ),
    ),
    (
        "business_activity",
        (
            r"\bism\b|purchasing managers.? index|\bpmi\b",
            r"manufacturing index|services? index|non.?manufacturing",
            r"business confidence|business climate|factory activity",
            r"regional manufacturing|empire state manufacturing|philly fed",
            r"industrial confidence|economic activity index",
        ),
    ),
    (
        "consumer",
        (
            r"consumer confidence|consumer sentiment|university of michigan",
            r"conference board|consumer expectations|consumer credit",
            r"personal income|personal spending|personal consumption",
            r"savings rate|household spending|household consumption",
        ),
    ),
    (
        "housing",
        (
            r"building permits?|housing starts?|new home sales|existing home sales",
            r"pending home sales|home price|house price|case.?shiller|\bnahb\b",
            r"mortgage applications?|mortgage rate|construction spending",
        ),
    ),
    (
        "trade",
        (
            r"trade balance|trade deficit|trade surplus|current account",
            r"exports?|imports?|foreign trade|international trade",
        ),
    ),
    (
        "energy",
        (
            r"crude oil inventories|crude oil stocks|\beia\b.*inventor",
            r"natural gas storage|natural gas inventories|oil production",
            r"oil rig count|opec|petroleum status report|gasoline inventories",
        ),
    ),
)


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def row_value(row: dict[str, str], name: str) -> str:
    """Get a column by exact name first, then by case-insensitive name."""
    value = row.get(name)
    if value is not None and normalize_text(value):
        return str(value)
    target = name.casefold()
    for key, candidate in row.items():
        if key is not None and str(key).casefold() == target:
            if candidate is not None and normalize_text(candidate):
                return str(candidate)
    return ""


def normalize_event_name(value: str) -> str:
    return normalize_text(value).casefold()


def classify_event(event_name: str) -> str:
    text = normalize_text(event_name).casefold()
    for category, patterns in CATEGORY_RULES:
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            return category
    return "other"


def make_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", normalize_text(value).casefold()).strip("_")
    return slug or "unknown_event"


def extract_date(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", value)
    if match:
        return match.group(1)
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def canonicalize_row(source_row: dict[str, str | None]) -> dict[str, str]:
    """Map either the 8-column archive or 21-column cache to one stable schema."""
    result: dict[str, str] = {}
    for field_name in OUTPUT_FIELDS:
        value = source_row.get(field_name)
        if value is None or not normalize_text(value):
            # Case-insensitive fallback is useful for differently capitalized headers.
            value = ""
            for key, candidate in source_row.items():
                if key is not None and str(key).casefold() == field_name.casefold():
                    if candidate is not None and normalize_text(candidate):
                        value = candidate
                        break
        result[field_name] = "" if value is None else str(value)
    return result


def event_date(row: dict[str, str]) -> str:
    for field_name in ("datetime_utc", "DateTime", "datetime_local", "date"):
        found = extract_date(row.get(field_name, ""))
        if found:
            return found
    return ""


def normalized_timestamp(row: dict[str, str]) -> str:
    """Return a timezone-normalized timestamp for matching archive/cache rows."""
    candidates = (
        row.get("datetime_utc", ""),
        row.get("DateTime", ""),
        row.get("datetime_local", ""),
    )
    for value in candidates:
        value = normalize_text(value)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed.isoformat(timespec="seconds")
        except ValueError:
            continue
    date_part = normalize_text(row.get("date", ""))
    time_part = normalize_text(row.get("time", ""))
    if date_part:
        return f"{date_part}T{time_part}" if time_part else date_part
    return ""


def row_identity(row: dict[str, str]) -> str:
    timestamp = normalized_timestamp(row)
    currency = normalize_text(row.get("Currency") or row.get("currency", "")).upper()
    event_name = normalize_event_name(row.get("Event") or row.get("event", ""))
    if timestamp and event_name:
        identity_text = "|".join((timestamp, currency, event_name))
    else:
        # Fallback for malformed/incomplete rows: exact canonical row identity.
        identity_text = "|".join(row.get(field_name, "") for field_name in OUTPUT_FIELDS)
    return hashlib.sha256(identity_text.encode("utf-8")).hexdigest()


def merge_rows(old: dict[str, str], new: dict[str, str]) -> tuple[dict[str, str], bool]:
    """Keep existing values where the update is blank; prefer non-empty new data."""
    merged = dict(old)
    changed = False
    for field_name in OUTPUT_FIELDS:
        new_value = new.get(field_name, "")
        if normalize_text(new_value) and new_value != merged.get(field_name, ""):
            merged[field_name] = new_value
            changed = True
    return merged, changed


def discover_input(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Input CSV not found: {path}")
        return path

    script_dir = Path(__file__).resolve().parent
    candidates = (
        script_dir / "data" / SOURCE_FILENAME,
        script_dir / SOURCE_FILENAME,
        script_dir / "untitled" / "data" / SOURCE_FILENAME,
        script_dir.parent / "data" / SOURCE_FILENAME,
        script_dir.parent / "untitled" / "data" / SOURCE_FILENAME,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    checked = "\n  - ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "Could not automatically locate forex_factory_cache.csv. Pass --input. Checked:\n  - "
        + checked
    )


def remove_previous_generated_files(output_dir: Path) -> None:
    """In rebuild mode, remove only paths listed in this script's manifest."""
    manifest_path = output_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"WARNING: could not read manifest; untracked old files may remain: {manifest_path}")
        return

    for relative_name in manifest.get("generated_files", []):
        candidate = (output_dir / relative_name).resolve()
        try:
            candidate.relative_to(output_dir.resolve())
        except ValueError:
            print(f"WARNING: skipped unsafe manifest path: {relative_name}")
            continue
        if candidate.is_file():
            candidate.unlink()


def read_existing_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            return []
        return [canonicalize_row(row) for row in reader if row]


def choose_event_path(output_dir: Path, category: str, event_name: str, used_paths: set[str]) -> str:
    base = f"{category}/{make_slug(event_name)}.csv"
    candidate = base
    target = (output_dir / candidate).resolve()
    existing_event_name = ""
    if target.is_file():
        existing_rows = read_existing_rows(target)
        if existing_rows:
            existing_event_name = existing_rows[0].get("Event") or existing_rows[0].get("event", "")
    same_existing_event = normalize_event_name(existing_event_name) == normalize_event_name(event_name)
    if candidate.casefold() in used_paths or (target.exists() and not same_existing_event):
        event_key = normalize_event_name(event_name)
        suffix = hashlib.sha1(event_key.encode("utf-8")).hexdigest()[:8]
        candidate = f"{category}/{make_slug(event_name)}_{suffix}.csv"
    used_paths.add(candidate.casefold())
    return candidate


@dataclass
class EventGroup:
    category: str
    event_name: str
    relative_path: str
    rows: list[dict[str, str]] = field(default_factory=list)


def load_source_groups(input_path: Path, output_dir: Path) -> tuple[dict[str, EventGroup], int, int, set[str]]:
    groups: dict[str, EventGroup] = {}
    used_paths: set[str] = set()
    total_rows = 0
    malformed_rows = 0

    with input_path.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.DictReader(source_file, restkey="__extra_fields__")
        if not reader.fieldnames:
            raise ValueError("CSV is empty or has no header row.")
        header_names = {str(name).strip().casefold() for name in reader.fieldnames if name is not None}
        if "event" not in header_names:
            raise ValueError(
                "The CSV header does not contain Event/event. "
                f"Detected columns: {reader.fieldnames}"
            )
        if not ("datetime" in header_names or "datetime_utc" in header_names or "date" in header_names):
            raise ValueError("The CSV needs a DateTime, datetime_utc or date column.")

        for raw_row in reader:
            if not raw_row:
                continue
            if raw_row.get("__extra_fields__"):
                malformed_rows += 1
            row = canonicalize_row(raw_row)
            event_name = normalize_text(row.get("Event") or row.get("event", "")) or "Unknown event"
            event_key = normalize_event_name(event_name)
            category = classify_event(event_name)

            if event_key not in groups:
                relative_path = choose_event_path(output_dir, category, event_name, used_paths)
                groups[event_key] = EventGroup(
                    category=category,
                    event_name=event_name,
                    relative_path=relative_path,
                )
            groups[event_key].rows.append(row)
            total_rows += 1

    if not groups:
        raise ValueError("No calendar events were found in the input CSV.")
    return groups, total_rows, malformed_rows, used_paths


def write_event_file(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as target_file:
        writer = csv.DictWriter(target_file, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_catalog_and_manifest(output_dir: Path) -> tuple[Path, list[str], dict[str, int]]:
    generated_files: list[str] = []
    catalog_rows: list[list[str]] = []
    category_counts: dict[str, int] = defaultdict(int)

    for path in sorted(output_dir.glob("*/*.csv")):
        if path.name == CATALOG_FILENAME:
            continue
        rows = read_existing_rows(path)
        if not rows:
            continue
        event_name = normalize_text(rows[0].get("Event") or rows[0].get("event", "")) or path.stem
        event_key = normalize_event_name(event_name)
        category = path.parent.name
        currencies = {
            normalize_text(row.get("Currency") or row.get("currency", "")).upper()
            for row in rows
        }
        currencies.discard("")
        dates = [event_date(row) for row in rows]
        dates = sorted(date for date in dates if date)
        relative_path = path.relative_to(output_dir).as_posix()
        generated_files.append(relative_path)
        category_counts[category] += len(rows)
        catalog_rows.append(
            [
                category,
                event_name,
                event_key,
                relative_path,
                str(len(rows)),
                ",".join(sorted(currencies)),
                dates[0] if dates else "",
                dates[-1] if dates else "",
            ]
        )

    catalog_path = output_dir / CATALOG_FILENAME
    with catalog_path.open("w", encoding="utf-8-sig", newline="") as catalog_file:
        writer = csv.writer(catalog_file)
        writer.writerow(
            ["category", "event", "event_key", "file", "row_count", "currencies", "first_date", "last_date"]
        )
        writer.writerows(sorted(catalog_rows, key=lambda item: (item[0], item[1].casefold())))
    generated_files.append(CATALOG_FILENAME)

    manifest_path = output_dir / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps({"generated_files": generated_files}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return catalog_path, generated_files, category_counts


def build_reports(input_path: Path, output_dir: Path, mode: str) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    if mode == "rebuild":
        remove_previous_generated_files(output_dir)

    groups, total_rows, malformed_rows, _ = load_source_groups(input_path, output_dir)
    added_rows = 0
    updated_rows = 0
    duplicate_rows = 0

    for group in groups.values():
        target = output_dir / group.relative_path
        incoming_by_id: dict[str, dict[str, str]] = {}
        for row in group.rows:
            key = row_identity(row)
            if key in incoming_by_id:
                merged, changed = merge_rows(incoming_by_id[key], row)
                incoming_by_id[key] = merged
                if changed:
                    updated_rows += 1
                else:
                    duplicate_rows += 1
            else:
                incoming_by_id[key] = row

        if mode == "append":
            existing_rows = read_existing_rows(target)
            combined: dict[str, dict[str, str]] = {}
            for old_row in existing_rows:
                combined[row_identity(old_row)] = old_row

            for key, new_row in incoming_by_id.items():
                if key in combined:
                    merged, changed = merge_rows(combined[key], new_row)
                    combined[key] = merged
                    if changed:
                        updated_rows += 1
                    else:
                        duplicate_rows += 1
                else:
                    combined[key] = new_row
                    added_rows += 1
            rows_to_write = list(combined.values())
        else:
            rows_to_write = list(incoming_by_id.values())
            added_rows += len(rows_to_write)

        rows_to_write.sort(key=lambda row: (normalized_timestamp(row), row.get("Currency", ""), row.get("Event", "")))
        write_event_file(target, rows_to_write)

    catalog_path, generated_files, category_counts = build_catalog_and_manifest(output_dir)

    print(f"Mode:         {mode}")
    print(f"Source:       {input_path}")
    print(f"Output:       {output_dir}")
    print(f"Source rows:  {total_rows:,}")
    print(f"Event files:  {len(generated_files) - 1:,}")
    if mode == "append":
        print(f"Added rows:   {added_rows:,}")
        print(f"Updated rows: {updated_rows:,}")
        print(f"Duplicates:   {duplicate_rows:,}")
    else:
        print(f"Written rows: {added_rows:,}")
    print(f"Catalog:      {catalog_path}")
    if malformed_rows:
        print(
            f"WARNING: {malformed_rows:,} rows had extra CSV fields beyond the header. "
            "Check the source CSV; extra fields were ignored."
        )
    print("\nRows by category:")
    for category, count in sorted(category_counts.items()):
        print(f"  {category:<20} {count:>10,}")
    if mode == "rebuild":
        print("\nRebuild complete: generated event files were replaced from this source.")
    else:
        print("\nAppend complete: existing event history was preserved; new/changed rows were merged.")
    return 0


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split Forex Factory calendar data into categorized per-event CSV files."
    )
    parser.add_argument(
        "--input",
        dest="input_csv",
        help="Input calendar CSV (historical 8-column export or current 21-column cache).",
    )
    parser.add_argument(
        "--output-dir",
        help="Output folder. Default: economic_data next to the input CSV.",
    )
    parser.add_argument(
        "--mode",
        choices=("rebuild", "append"),
        default="append",
        help="rebuild replaces generated event files; append merges into existing files (default).",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        input_path = discover_input(args.input_csv)
        output_dir = (
            Path(args.output_dir).expanduser().resolve()
            if args.output_dir
            else input_path.parent / OUTPUT_FOLDER_NAME
        )
        if output_dir == input_path.parent:
            raise ValueError("Output directory cannot be the same directory as the source CSV folder.")
        if input_path.parent == output_dir or input_path.resolve().is_relative_to(output_dir.resolve()):
            raise ValueError("Output directory cannot contain the source CSV.")
        return build_reports(input_path, output_dir, args.mode)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
