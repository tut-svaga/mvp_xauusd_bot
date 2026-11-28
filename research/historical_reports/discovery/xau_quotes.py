"""Inventory XAUUSD M1 BID/ASK quote files by trading date."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

FILENAME_RE = re.compile(r"XAUUSD_M1_(BID|ASK)_(\d{4}-\d{2}-\d{2})\.csv$", re.IGNORECASE)


def scan_xau_quotes(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not root.is_dir():
        return [], {
            "path": str(root), "status": "NOT_FOUND", "files": 0,
            "dates": 0, "paired_dates": 0, "unpaired_dates": 0,
            "unrecognized_csv_files": 0,
        }

    grouped: dict[str, dict[str, list[Path]]] = defaultdict(lambda: {"BID": [], "ASK": []})
    total_csv = recognized = invalid_date_names = 0
    for path in root.rglob("*.csv"):
        total_csv += 1
        match = FILENAME_RE.fullmatch(path.name)
        if not match:
            continue
        day = match.group(2)
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            invalid_date_names += 1
            continue
        recognized += 1
        grouped[day][match.group(1).upper()].append(path)

    rows: list[dict[str, Any]] = []
    paired = 0
    for day, sides in sorted(grouped.items()):
        bid, ask = sides["BID"], sides["ASK"]
        if len(bid) > 1 and len(ask) > 1:
            status = "DUPLICATE_BID_AND_ASK"
        elif len(bid) > 1:
            status = "DUPLICATE_BID"
        elif len(ask) > 1:
            status = "DUPLICATE_ASK"
        elif bid and ask:
            status = "PAIRED"
        elif bid:
            status = "MISSING_ASK"
        else:
            status = "MISSING_BID"
        paired += status == "PAIRED"
        rows.append({
            "date": day, "bid_files": len(bid), "ask_files": len(ask),
            "bid_bytes": sum(p.stat().st_size for p in bid),
            "ask_bytes": sum(p.stat().st_size for p in ask), "status": status,
            "bid_paths": ";".join(p.relative_to(root).as_posix() for p in bid),
            "ask_paths": ";".join(p.relative_to(root).as_posix() for p in ask),
        })

    if not recognized:
        status = "NO_RECOGNIZED_FILES"
    elif paired == len(grouped):
        status = "OK"
    else:
        status = "PARTIAL"

    return rows, {
        "path": str(root), "status": status, "files": recognized,
        "total_csv_files": total_csv, "dates": len(grouped),
        "paired_dates": paired, "unpaired_dates": len(grouped) - paired,
        "unrecognized_csv_files": total_csv - recognized,
        "invalid_date_filenames": invalid_date_names,
    }
