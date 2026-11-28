"""Shared data models used by Discovery modules."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    file: str = ""
    row: int | None = None
    field: str = ""


@dataclass
class CsvRecord:
    row_number: int
    values: dict[str, str]


@dataclass
class CsvFile:
    path: Path
    relative_path: str
    category: str
    headers: list[str] = field(default_factory=list)
    delimiter: str = ","
    records: list[CsvRecord] = field(default_factory=list)
    byte_size: int = 0
    read_error: str = ""


@dataclass
class FileReport:
    file: str
    category: str
    size_bytes: int
    rows: int
    headers: list[str]
    delimiter: str
    currencies: Counter[str] = field(default_factory=Counter)
    event_names: set[str] = field(default_factory=set)
    timestamp_sources: Counter[str] = field(default_factory=Counter)
    timezone_offsets: Counter[str] = field(default_factory=Counter)
    first_date: str = ""
    last_date: str = ""
    issues: list[Issue] = field(default_factory=list)
    read_error: str = ""

    @property
    def status(self) -> str:
        if self.read_error or any(i.severity == "ERROR" for i in self.issues):
            return "ERROR"
        return "REVIEW" if self.issues else "OK"


@dataclass
class DiscoveryResult:
    generated_at_utc: str
    economic_data_dir: str
    output_dir: str
    files: list[FileReport] = field(default_factory=list)
    catalog_rows: list[dict[str, Any]] = field(default_factory=list)
    manifest_rows: list[dict[str, Any]] = field(default_factory=list)
    auxiliary_rows: list[dict[str, Any]] = field(default_factory=list)
    xau_quote_rows: list[dict[str, Any]] = field(default_factory=list)
    xau_summary: dict[str, Any] | None = None
    cache_summary: dict[str, Any] | None = None
    issues: list[Issue] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        currencies: Counter[str] = Counter()
        sources: Counter[str] = Counter()
        for item in self.files:
            currencies.update(item.currencies)
            sources.update(item.timestamp_sources)
        return {
            "generated_at_utc": self.generated_at_utc,
            "economic_data_dir": self.economic_data_dir,
            "output_dir": self.output_dir,
            "report_files": len(self.files),
            "total_report_rows": sum(item.rows for item in self.files),
            "categories": len({item.category for item in self.files}),
            "currencies": sorted(currencies),
            "currency_row_counts": dict(sorted(currencies.items())),
            "timestamp_source_counts": dict(sorted(sources.items())),
            "rows_with_selected_timestamp": sum(sources.values()),
            "files_with_warnings": sum(item.status != "OK" for item in self.files),
            "catalog_issue_count": sum(row.get("status") != "OK" for row in self.catalog_rows),
            "manifest_issue_count": sum(row.get("status") != "OK" for row in self.manifest_rows),
            "forex_factory_cache": self.cache_summary,
            "xauusd_quotes": self.xau_summary,
            "issues": len(self.issues) + sum(len(item.issues) for item in self.files),
        }
