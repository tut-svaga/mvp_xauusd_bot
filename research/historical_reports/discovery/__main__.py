"""Command-line entry point: python -m research.historical_reports.discovery."""
from __future__ import annotations
from datetime import datetime

import argparse
import sys
from pathlib import Path

from .exporter import export_result
from .runner import run_discovery

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ECONOMIC_DATA = REPO_ROOT / "data" / "economic_data"
DEFAULT_CACHE = REPO_ROOT / "data" / "forex_factory_cache.csv"
DEFAULT_OUTPUT = ( REPO_ROOT / "artifacts" / "research" / "discovery" / f"{datetime.now():%Y-%m-%d}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover and validate XAUUSD historical research data.")
    parser.add_argument("--economic-data-dir", type=Path, default=DEFAULT_ECONOMIC_DATA)
    parser.add_argument("--calendar-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--no-cache", action="store_true", help="Skip Forex Factory cache inspection")
    parser.add_argument("--xau-root", type=Path, default=None, help="Optional XAUUSD M1 BID/ASK root directory")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_discovery(
            economic_data_dir=args.economic_data_dir,
            output_dir=args.output_dir,
            calendar_cache=None if args.no_cache else args.calendar_cache,
            xau_root=args.xau_root,
        )
        export_result(result, args.output_dir.expanduser().resolve())
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary = result.summary()
    print("Research data discovery completed.")
    print(f"Report files: {summary['report_files']}")
    print(f"Rows: {summary['total_report_rows']}")
    print(f"Currencies: {', '.join(summary['currencies']) or 'none'}")
    print(f"Files requiring review: {summary['files_requiring_review']}")
    print(f"Catalog issues: {summary['catalog_issue_count']}; manifest issues: {summary['manifest_issue_count']}")
    print(f"Output: {args.output_dir.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
