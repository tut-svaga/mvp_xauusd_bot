"""Inventory XAUUSD M1 BID/ASK quote files by trading date."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

FILENAME_RE = re.compile(r"XAUUSD_M1_(BID|ASK)_(\d{4}-\d{2}-\d{2})\.csv$", re.IGNORECASE)


def scan_xau_quotes(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not root.is_dir():
        return [], {"path": str(root), "status": "NOT_FOUND", "files": 0, "dates": 0, "unrecognized_csv_files": 0}
    grouped: dict[str, dict[str, list[Path]]] = defaultdict(lambda: {"BID": [], "ASK": []})
    total_csv = recognized = 0
    for path in root.rglob("*.csv"):
        total_csv += 1
        match = FILENAME_RE.search(path.name)
        if match:
            recognized += 1
            grouped[match.group(2)][match.group(1).upper()].append(path)
    rows: list[dict[str, Any]] = []
    paired = 0
    for day, sides in sorted(grouped.items()):
        bid, ask = sides["BID"], sides["ASK"]
        status = "PAIRED" if bid and ask else "MISSING_ASK" if bid else "MISSING_BID"
        paired += status == "PAIRED"
        rows.append({
            "date": day, "bid_files": len(bid), "ask_files": len(ask),
            "bid_bytes": sum(p.stat().st_size for p in bid),
            "ask_bytes": sum(p.stat().st_size for p in ask), "status": status,
            "bid_paths": ";".join(p.relative_to(root).as_posix() for p in bid),
            "ask_paths": ";".join(p.relative_to(root).as_posix() for p in ask),
        })
    return rows, {
        "path": str(root), "status": "OK", "files": recognized, "dates": len(grouped),
        "paired_dates": paired, "unpaired_dates": len(grouped) - paired,
        "unrecognized_csv_files": total_csv - recognized,
    }
