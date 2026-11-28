"""Currency-code validation and mixed-currency detection."""
from __future__ import annotations

import re

from .models import CsvFile, Issue

# ISO-4217 codes plus common Forex Factory labels used in calendar exports.
KNOWN_CURRENCIES = set("""
USD EUR GBP JPY CHF CAD AUD NZD CNY CNH HKD SGD KRW INR MXN BRL RUB ZAR
TRY SEK NOK DKK PLN CZK HUF ILS SAR AED THB IDR MYR PHP TWD
""".split())


def validate_currencies(csv_file: CsvFile) -> tuple[list[Issue], dict[str, int]]:
    issues: list[Issue] = []
    counts: dict[str, int] = {}
    for record in csv_file.records:
        raw = record.values.get("currency", "").strip().upper()
        if not raw or raw in {"-", "—", "N/A", "NA"}:
            issues.append(Issue("WARNING", "MISSING_CURRENCY", "Currency is empty", csv_file.relative_path, record.row_number, "currency"))
            continue
        if not re.fullmatch(r"[A-Z]{3}", raw) or raw not in KNOWN_CURRENCIES:
            issues.append(Issue("WARNING", "UNKNOWN_CURRENCY", f"Unrecognized currency code: {raw}", csv_file.relative_path, record.row_number, "currency"))
        counts[raw] = counts.get(raw, 0) + 1
    if len(counts) > 1:
        issues.append(Issue("INFO", "MIXED_CURRENCIES", f"File contains multiple currencies: {', '.join(sorted(counts))}", csv_file.relative_path, field="currency"))
    return issues, counts
