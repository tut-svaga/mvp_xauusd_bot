#!/usr/bin/env python3
"""Download Dukascopy XAUUSD M1 BID/ASK candles into the existing daily CSV layout.

Default range: 2026-10-10 through the current UTC date (inclusive).
The script uses only the Python standard library.

Target layout:
  E:\\work\\XAU_gold\\XAUUSD\\raw\\BID\\YYYY\\MM\\XAUUSD_M1_BID_YYYY-MM-DD.csv
  E:\\work\\XAU_gold\\XAUUSD\\raw\\ASK\\YYYY\\MM\\XAUUSD_M1_ASK_YYYY-MM-DD.csv

Dukascopy's M1 candle endpoint returns LZMA-compressed 24-byte records.
Record layout: seconds-from-UTC-day-start, open, close, low, high, volume.
XAUUSD prices are integer thousandths (divide by 1000).
"""

from __future__ import annotations

import csv
import lzma
from email.utils import parsedate_to_datetime
import math
import struct
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Iterable

# ---------------------------- CONFIGURATION ----------------------------

RAW_ROOT = Path(r"E:\work\XAU_gold\XAUUSD\raw")
START_DATE = date(2026, 10, 10)  # Last known existing file is 2026-10-09.
END_DATE = None                 # None = today in UTC, inclusive.

# Re-download yesterday and today so a partially populated latest file can be updated.
# Set to -1 to never refresh existing files; set to 0 to refresh only today.
REFRESH_RECENT_DAYS = 1

REQUEST_TIMEOUT_SECONDS = 12
REQUEST_PAUSE_SECONDS = 1.0
MAX_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 6
FAIL_FAST_AFTER_CONSECUTIVE_ERRORS = 4
PRICE_SCALE = 1000.0            # XAUUSD has 3 decimal places on Dukascopy.
USER_AGENT = "Mozilla/5.0 (compatible; XAUUSD-M1-History-Downloader/1.0)"

SIDES = ("BID", "ASK")
RECORD = struct.Struct(">IIIIIf")  # 5 x uint32 + float32 = 24 bytes
BASE_URL = "https://datafeed.dukascopy.com/datafeed"
UTC = timezone.utc


# ------------------------------- HELPERS -------------------------------

def log(message: str) -> None:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def get_date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def target_file(side: str, day: date) -> Path:
    return (
        RAW_ROOT
        / side
        / f"{day.year:04d}"
        / f"{day.month:02d}"
        / f"XAUUSD_M1_{side}_{day.isoformat()}.csv"
    )


def dukascopy_url(side: str, day: date) -> str:
    # Dukascopy uses zero-based months: January=00, October=09.
    return (
        f"{BASE_URL}/XAUUSD/{day.year:04d}/{day.month - 1:02d}/"
        f"{day.day:02d}/{side}_candles_min_1.bi5"
    )


def retry_delay(headers, fallback: float) -> float:
    """Read Retry-After as seconds or HTTP date; otherwise use fallback."""
    value = headers.get("Retry-After") if headers else None
    if not value:
        return fallback
    try:
        return max(1.0, float(value.strip()))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(1.0, (retry_at - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return fallback


def download_bytes(url: str) -> bytes | None:
    """Download one file with limited retries; never hammer a failing endpoint."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # Weekend / holiday / no recorded candles.

            retryable = exc.code in (408, 425, 429, 500, 502, 503, 504)
            if not retryable or attempt == MAX_ATTEMPTS:
                raise

            # 429/503 often mean the public feed is throttling or temporarily unavailable.
            fallback = RETRY_BACKOFF_SECONDS * attempt
            delay = retry_delay(exc.headers, fallback)
            log(f"HTTP {exc.code}; one controlled retry in {delay:.0f}s")
            time.sleep(delay)
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"Network error after {MAX_ATTEMPTS} attempts: {exc}") from exc
            delay = RETRY_BACKOFF_SECONDS * attempt
            log(f"Network error; one controlled retry in {delay}s: {exc}")
            time.sleep(delay)

    return None


def decode_m1(payload: bytes, day: date) -> list[tuple[datetime, float, float, float, float, float]]:
    if not payload:
        return []

    try:
        raw = lzma.decompress(payload, format=lzma.FORMAT_AUTO)
    except lzma.LZMAError as exc:
        raise ValueError(f"Cannot decompress Dukascopy BI5 payload: {exc}") from exc

    if len(raw) % RECORD.size != 0:
        raise ValueError(
            f"Unexpected BI5 payload length {len(raw)}; not divisible by {RECORD.size}"
        )

    day_start = datetime.combine(day, dt_time.min, tzinfo=UTC)
    now_floor = datetime.now(UTC).replace(second=0, microsecond=0)
    rows = []

    for pos in range(0, len(raw), RECORD.size):
        seconds, open_raw, close_raw, low_raw, high_raw, volume = RECORD.unpack_from(raw, pos)
        if seconds >= 24 * 60 * 60:
            continue

        timestamp = day_start + timedelta(seconds=seconds)
        # Do not store the currently-forming minute.
        if timestamp >= now_floor:
            continue

        open_price = open_raw / PRICE_SCALE
        close_price = close_raw / PRICE_SCALE
        low_price = min(low_raw, high_raw) / PRICE_SCALE
        high_price = max(low_raw, high_raw) / PRICE_SCALE

        if not all(math.isfinite(x) for x in (open_price, close_price, low_price, high_price, volume)):
            continue
        if min(open_price, close_price) < low_price - 1e-9:
            continue
        if max(open_price, close_price) > high_price + 1e-9:
            continue
        # Dukascopy fills minutes without trades with flat, zero-volume candles.
        # They are not real quotes and must not enter the XAUUSD archive.
        if volume <= 0:
            continue

        rows.append((timestamp, open_price, high_price, low_price, close_price, float(volume)))

    rows.sort(key=lambda row: row[0])

    # De-duplicate timestamps defensively. The later record wins.
    unique: dict[datetime, tuple[datetime, float, float, float, float, float]] = {}
    for row in rows:
        unique[row[0]] = row
    return list(unique.values())


def write_csv(path: Path, rows: list[tuple[datetime, float, float, float, float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for timestamp, open_price, high_price, low_price, close_price, volume in rows:
            writer.writerow([
                timestamp.isoformat(sep=" ", timespec="seconds"),
                f"{open_price:.3f}",
                f"{high_price:.3f}",
                f"{low_price:.3f}",
                f"{close_price:.3f}",
                f"{volume:.8f}",
            ])

    temp_path.replace(path)


def sanitize_existing_csv(path: Path) -> tuple[int, int, bool]:
    """Remove zero-volume filler rows from files created by an older script version.

    Returns (removed_rows, remaining_rows, file_deleted). Only acts on the expected
    six-column daily CSV format; unknown formats are left untouched.
    """
    if not path.exists():
        return 0, 0, False

    try:
        with path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            fieldnames = reader.fieldnames
            if not fieldnames or not {"timestamp", "open", "high", "low", "close", "volume"}.issubset(fieldnames):
                return 0, 0, False
            rows = list(reader)
    except (OSError, csv.Error, UnicodeError):
        return 0, 0, False

    kept = []
    for row in rows:
        try:
            volume = float(row.get("volume", ""))
        except (TypeError, ValueError):
            # Do not modify a file whose volume column cannot be interpreted safely.
            return 0, len(rows), False
        if math.isfinite(volume) and volume > 0:
            kept.append(row)

    removed = len(rows) - len(kept)
    if removed == 0:
        return 0, len(rows), False

    if not kept:
        path.unlink(missing_ok=True)
        return removed, 0, True

    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(kept)
        temp_path.replace(path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise

    return removed, len(kept), False


def should_refresh(day: date, today: date) -> bool:
    if REFRESH_RECENT_DAYS < 0:
        return False
    return day >= today - timedelta(days=REFRESH_RECENT_DAYS)


# -------------------------------- MAIN ---------------------------------

def main() -> int:
    today = datetime.now(UTC).date()
    end = END_DATE or today

    if START_DATE > end:
        log(f"START_DATE {START_DATE} is after END_DATE {end}; nothing to do.")
        return 0

    log("Dukascopy XAUUSD M1 backfill started")
    log(f"Range: {START_DATE} -> {end} UTC (inclusive)")
    log(f"Destination: {RAW_ROOT}")
    log("Sides: BID + ASK (sequential; no parallel requests)")
    log(f"Refresh existing recent files: {REFRESH_RECENT_DAYS} day(s)")
    log(f"Timeout={REQUEST_TIMEOUT_SECONDS}s; attempts={MAX_ATTEMPTS}; pause={REQUEST_PAUSE_SECONDS}s")
    log("Zero-volume filler candles are excluded; Saturdays are not requested.")

    totals = {"downloaded": 0, "skipped_existing": 0, "no_data": 0, "empty": 0, "failed": 0}
    errors: list[str] = []
    consecutive_errors = 0
    stopped_early = False

    try:
        for day in get_date_range(START_DATE, end):
            # XAUUSD does not trade on Saturdays. Older script versions may have
            # saved Dukascopy's zero-volume filler rows for this date; clean those.
            if day.weekday() == 5:
                for side in SIDES:
                    destination = target_file(side, day)
                    if destination.exists():
                        removed, remaining, deleted = sanitize_existing_csv(destination)
                        if removed:
                            action = "deleted" if deleted else f"kept {remaining} real bars"
                            log(f"CLEANED {side} {day}: removed {removed} zero-volume filler rows; {action}")
                    totals["no_data"] += 1
                log(f"NO SESSION {day}: Saturday; skipping BID and ASK")
                continue

            for side in SIDES:
                destination = target_file(side, day)
                refresh = should_refresh(day, today)

                if destination.exists():
                    removed, remaining, deleted = sanitize_existing_csv(destination)
                    if removed:
                        action = "deleted all-zero-volume file" if deleted else f"kept {remaining} real bars"
                        log(f"CLEANED {side} {day}: removed {removed} zero-volume filler rows; {action}")

                if destination.exists() and not refresh:
                    totals["skipped_existing"] += 1
                    continue

                url = dukascopy_url(side, day)
                try:
                    payload = download_bytes(url)
                    if payload is None:
                        totals["no_data"] += 1
                        consecutive_errors = 0
                        log(f"NO DATA {side} {day} (404; likely weekend/holiday)")
                    elif not payload:
                        totals["empty"] += 1
                        consecutive_errors = 0
                        log(f"EMPTY {side} {day}")
                    else:
                        rows = decode_m1(payload, day)
                        if not rows:
                            totals["empty"] += 1
                            consecutive_errors = 0
                            log(f"EMPTY AFTER DECODE {side} {day}; existing file kept if present")
                        else:
                            write_csv(destination, rows)
                            totals["downloaded"] += 1
                            consecutive_errors = 0
                            log(f"SAVED {side} {day}: {len(rows):,} bars -> {destination}")

                except Exception as exc:
                    totals["failed"] += 1
                    consecutive_errors += 1
                    message = f"FAILED {side} {day}: {type(exc).__name__}: {exc}"
                    errors.append(message)
                    log(message)

                    # Repeated 5xx/timeouts usually indicate the feed is unavailable or
                    # throttling. Stop early instead of spending many minutes retrying.
                    if consecutive_errors >= FAIL_FAST_AFTER_CONSECUTIVE_ERRORS:
                        stopped_early = True
                        log(
                            f"STOPPING EARLY: {consecutive_errors} consecutive failed downloads. "
                            "Successful daily files are already saved; rerun later to resume."
                        )
                        raise StopIteration

                time.sleep(REQUEST_PAUSE_SECONDS)

    except StopIteration:
        pass
    except KeyboardInterrupt:
        log("INTERRUPTED by user. Completed files remain saved; rerun later to resume.")
        return 130

    log("---------------- SUMMARY ----------------")
    for key, value in totals.items():
        log(f"{key}: {value}")
    if errors:
        log("Failed items (they will be retried on the next run if their files are absent):")
        for item in errors:
            log(item)
    if stopped_early:
        log("Run stopped early because the datafeed repeatedly failed. Rerun when Dukascopy is responding.")
        return 2
    if errors:
        log("Finished with failed dates. Rerun later to retry the missing files.")
        return 1

    log("Finished. Existing daily files outside the refresh window were not overwritten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
