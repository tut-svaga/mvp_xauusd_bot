#!/usr/bin/env python3
"""Split Forex Factory calendar cache into event-specific CSV files.

The source cache remains the single source of truth. This script only rebuilds
category/event CSV files and a small event_catalog.csv index; it does not fetch
calendar data or modify the source cache.

Usage:
    py create_calendar_reports.py
    py create_calendar_reports.py --input "data/forex_factory_cache.csv"
    py create_calendar_reports.py --input "data/forex_factory_cache.csv" \
        --output-dir "data/economic_data"
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
from datetime import datetime
from pathlib import Path
from typing import Iterable


SOURCE_FILENAME = "forex_factory_cache.csv"
OUTPUT_FOLDER_NAME = "economic_data"
MANIFEST_FILENAME = ".calendar_reports_manifest.json"
CATALOG_FILENAME = "event_catalog.csv"

# Patterns are checked in order. More specific groups should stay above
# broader groups (for example, GDP Price Index belongs to growth, not inflation).
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
    """Collapse repeated whitespace while keeping the source wording readable."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def column_value(row: dict[str, str | None], *names: str) -> str:
    """Return the first non-empty value among case-insensitive column names."""
    for name in names:
        for key, value in row.items():
            if key is not None and str(key).strip().casefold() == name.casefold():
                if value is not None and normalize_text(value):
                    return normalize_text(value)
    return ""


def classify_event(event_name: str) -> str:
    text = normalize_text(event_name).casefold()
    for category, patterns in CATEGORY_RULES:
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            return category
    return "other"


def make_slug(value: str) -> str:
    slug = value.casefold()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "unknown_event"


def extract_date(value: str) -> str:
    """Extract YYYY-MM-DD without converting time zones or changing source data."""
    if not value:
        return ""
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", value)
    if match:
        return match.group(1)
    # Handle common date-only formats found in calendar exports.
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def get_event_date(row: dict[str, str | None]) -> str:
    # Prefer an explicitly UTC timestamp for catalog bounds. The raw source
    # columns themselves are written unchanged to each event CSV.
    for column in ("datetime_utc", "DateTime", "datetime_local", "date"):
        value = column_value(row, column)
        extracted = extract_date(value)
        if extracted:
            return extracted
    return ""


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
        "Could not automatically locate forex_factory_cache.csv. "
        "Pass its path with --input. Checked:\n  - " + checked
    )


def safe_remove_previous_outputs(output_dir: Path) -> None:
    """Remove only files recorded by this script's previous manifest."""
    manifest_path = output_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"WARNING: cannot read old manifest; stale files may remain: {manifest_path}")
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


@dataclass
class EventGroup:
    category: str
    event_name: str
    slug: str
    rows: list[list[str]] = field(default_factory=list)
    currencies: set[str] = field(default_factory=set)
    dates: list[str] = field(default_factory=list)


def build_reports(input_path: Path, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_remove_previous_outputs(output_dir)

    groups: dict[str, EventGroup] = {}
    used_paths: set[str] = set()
    total_rows = 0
    malformed_rows = 0

    # utf-8-sig removes a possible BOM from the first header field.
    with input_path.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.DictReader(source_file, restkey="__extra_fields__")
        if not reader.fieldnames:
            raise ValueError("CSV is empty or has no header row.")

        fieldnames = [str(name) if name is not None else "" for name in reader.fieldnames]
        if not any(name.strip().casefold() == "event" for name in fieldnames):
            raise ValueError(
                "The CSV header does not contain an Event/event column. "
                f"Detected columns: {fieldnames}"
            )

        for row in reader:
            if not row:
                continue
            total_rows += 1

            event_name = column_value(row, "Event", "event") or "Unknown event"
            event_key = normalize_text(event_name).casefold()
            category = classify_event(event_name)

            if event_key not in groups:
                base_slug = make_slug(event_name)
                relative_path = f"{category}/{base_slug}.csv"
                if relative_path.casefold() in used_paths:
                    short_hash = hashlib.sha1(event_key.encode("utf-8")).hexdigest()[:8]
                    relative_path = f"{category}/{base_slug}_{short_hash}.csv"
                used_paths.add(relative_path.casefold())
                groups[event_key] = EventGroup(
                    category=category,
                    event_name=event_name,
                    slug=Path(relative_path).name,
                )

            group = groups[event_key]
            # DictReader's keys retain the original header spelling; reconstruct
            # output in precisely the same column order as the cache.
            output_row: list[str] = []
            for original_name in reader.fieldnames:
                value = row.get(original_name, "")
                output_row.append("" if value is None else str(value))
            if row.get("__extra_fields__"):
                malformed_rows += 1

            group.rows.append(output_row)
            currency = column_value(row, "Currency", "currency").upper()
            if currency:
                group.currencies.add(currency)
            event_date = get_event_date(row)
            if event_date:
                group.dates.append(event_date)

    if not groups:
        raise ValueError("No calendar events were found in the input CSV.")

    generated_files: list[str] = []
    catalog_rows: list[list[str]] = []
    category_counts: dict[str, int] = defaultdict(int)

    # The event CSVs contain the original cache columns and original values.
    # No time-zone conversion, normalization, filtering, or deduplication is
    # applied to source rows here.
    with input_path.open("r", encoding="utf-8-sig", newline="") as source_file:
        source_reader = csv.reader(source_file)
        original_header = next(source_reader)
        output_header = [str(name) if name is not None else "" for name in original_header]

    for event_key, group in sorted(groups.items(), key=lambda item: (item[1].category, item[1].event_name.casefold())):
        # The stored slug includes a collision suffix when needed.
        relative_path = f"{group.category}/{group.slug}"
        target = output_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)

        with target.open("w", encoding="utf-8-sig", newline="") as target_file:
            writer = csv.writer(target_file)
            writer.writerow(output_header)
            writer.writerows(group.rows)

        generated_files.append(relative_path.replace("\\", "/"))
        category_counts[group.category] += len(group.rows)
        date_values = sorted(group.dates)
        catalog_rows.append(
            [
                group.category,
                group.event_name,
                event_key,
                relative_path.replace("\\", "/"),
                str(len(group.rows)),
                ",".join(sorted(group.currencies)),
                date_values[0] if date_values else "",
                date_values[-1] if date_values else "",
            ]
        )

    catalog_path = output_dir / CATALOG_FILENAME
    with catalog_path.open("w", encoding="utf-8-sig", newline="") as catalog_file:
        writer = csv.writer(catalog_file)
        writer.writerow(
            [
                "category",
                "event",
                "event_key",
                "file",
                "row_count",
                "currencies",
                "first_date",
                "last_date",
            ]
        )
        writer.writerows(catalog_rows)
    generated_files.append(CATALOG_FILENAME)

    manifest_path = output_dir / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps({"generated_files": generated_files}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Source:       {input_path}")
    print(f"Output:       {output_dir}")
    print(f"Rows read:    {total_rows:,}")
    print(f"Unique events:{len(groups):>8,}")
    print(f"Event CSVs:   {len(groups):,}")
    print(f"Catalog:      {catalog_path}")
    if malformed_rows:
        print(
            f"WARNING: {malformed_rows:,} rows contained extra CSV fields. "
            "Those extra fields are not part of the declared header and were not written."
        )
    print("\nRows by category:")
    for category, count in sorted(category_counts.items()):
        print(f"  {category:<20} {count:>10,}")
    print("\nDone. The source cache was not modified.")
    return 0


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split forex_factory_cache.csv into categorized event CSV files."
    )
    parser.add_argument(
        "--input",
        dest="input_csv",
        help="Path to forex_factory_cache.csv. If omitted, common project locations are checked.",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory. Default: economic_data next to the input CSV.",
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
            raise ValueError("Output directory cannot be the same directory as the source cache.")
        return build_reports(input_path, output_dir)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
