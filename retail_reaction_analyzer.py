"""\
US Core Retail Sales -> XAUUSD / reconstructed DXY historical reaction analyzer
================================================================================

Reads:
  1) The normalized event file produced by retail_calendar_builder.py
  2) Daily XAUUSD M1 BID and ASK files (ISO timestamps, usually UTC)
  3) HistData M1 files for EURUSD, GBPUSD, USDCAD, USDCHF, USDJPY and USDSEK

Creates CSV/Markdown reports. No database, pandas, or third-party packages required.

Important source-time conventions:
  * XAU files: timezone-aware timestamps are converted to UTC. Naive timestamps use
    XAU_NAIVE_TIMEZONE (UTC by default).
  * HistData: timestamps are fixed EST (UTC-5) WITHOUT DST adjustment. This is NOT
    America/New_York. Convert with a fixed -05:00 offset year-round.

The reconstructed DXY uses ICE's six-pair formula and HistData bid closes. It is an
analytical reconstruction, not the official ICE-published index.

Run:
    py retail_reaction_analyzer.py
"""

from __future__ import annotations

import csv
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# =============================================================================
# 1. USER CONFIGURATION
# =============================================================================

# Output of the previous retail_calendar_builder.py script.
CALENDAR_EVENTS_CSV = Path(
    r"E:\work\XAU_gold\mvp_tg_bot\mvp_retail\retail\us_core_retail_sales.csv"
)

# Daily XAU template. The script substitutes {side}=BID/ASK, {year}, {month}, {date}.
# Example: ...\raw\ASK\2026\01\XAUUSD_M1_ASK_2026-01-02.csv
XAU_FILE_TEMPLATE = (
    r"E:\work\XAU_gold\XAUUSD\raw\{side}\{year}\{month}\XAUUSD_M1_{side}_{date}.csv"
)

# HistData yearly M1 layout, one folder per pair.
# Example: ...\histdata\EURUSD\DAT_ASCII_EURUSD_M1_2025.csv
FX_FILE_TEMPLATE = (
    r"E:\work\XAU_gold\XAUUSD\data\external\histdata\{pair}\DAT_ASCII_{pair}_M1_{year}.csv"
)

FX_PAIRS = ("EURUSD", "GBPUSD", "USDCAD", "USDCHF", "USDJPY", "USDSEK")

# This is a historical-only run. Raise the end year after the 2026 FX files are added.
ANALYSIS_START_DATE = date(2007, 1, 1)
ANALYSIS_END_DATE = date(2025, 12, 31)

# HistData explicitly uses EST (UTC-5) all year, without DST adjustments.
HISTDATA_FIXED_OFFSET_HOURS = -5

# XAU timestamps in the supplied example carry +00:00. If any XAU file has naive
# timestamps, use this timezone for those rows.
XAU_NAIVE_TIMEZONE = timezone.utc

# One row per minute is expected. A 2-minute fallback is allowed only for selecting
# fixed-horizon close values / DXY component closes, never for XAU coverage counting.
MAX_XAU_HORIZON_FALLBACK_MINUTES = 2
MAX_FX_STALE_MINUTES = 2

# Events below these raw window-coverage ratios are excluded from the relevant
# aggregate price statistics, but their available data remains in event-level files.
MIN_XAU_WINDOW_COVERAGE = 0.85
MIN_DXY_WINDOW_COVERAGE = 0.85

# Returns inside +/- this number are classified as flat. 0.02 means 0.02 percent,
# i.e. two basis points, not 2 percent.
FLAT_THRESHOLD_PCT = 0.02

# Response windows: close-to-close at exactly these elapsed minutes from the release.
HORIZONS = (1, 5, 15, 30, 60, 120)

# Window exported for every event, including the pre-release segment.
WINDOW_START_MINUTE = -30
WINDOW_END_MINUTE = 120

# Approximate macro-economic blocks for comparison. These are organizational buckets,
# not objectively fixed business-cycle dates. Adjust if you prefer different regimes.
ECONOMIC_PERIODS = (
    (2007, 2009, "2007-2009"),
    (2010, 2013, "2010-2013"),
    (2014, 2016, "2014-2016"),
    (2017, 2019, "2017-2019"),
    (2020, 2022, "2020-2022"),
    (2023, 2025, "2023-2025"),
)

OUTPUT_DIR_NAME = "retail_reaction_analysis"
EVENT_OUTPUT_NAME = "event_reactions.csv"
WINDOW_OUTPUT_NAME = "event_window_bars.csv"
YEAR_OUTPUT_NAME = "annual_summary.csv"
PERIOD_OUTPUT_NAME = "period_summary.csv"
QA_OUTPUT_NAME = "data_quality_report.txt"
REPORT_OUTPUT_NAME = "analysis_report.md"

# =============================================================================
# 2. CONSTANTS / DATA STRUCTURES
# =============================================================================

UTC = timezone.utc
HISTDATA_TZ = timezone(timedelta(hours=HISTDATA_FIXED_OFFSET_HOURS), name="HistData_EST_fixed")
try:
    NEW_YORK_TZ = ZoneInfo("America/New_York")
    CHISINAU_TZ = ZoneInfo("Europe/Chisinau")
except ZoneInfoNotFoundError as exc:
    raise SystemExit(
        "Не найдена база часовых поясов. На Windows выполни: py -m pip install tzdata"
    ) from exc
DXY_CONSTANT = 50.14348112
DXY_EXPONENTS = {
    "EURUSD": -0.576,
    "USDJPY": 0.136,
    "GBPUSD": -0.119,
    "USDCAD": 0.091,
    "USDSEK": 0.042,
    "USDCHF": 0.036,
}


@dataclass
class Event:
    event_id: str
    release_date: date
    release_utc: datetime
    actual_raw: str
    forecast_raw: str
    previous_raw: str
    actual: Optional[float]
    forecast: Optional[float]
    previous: Optional[float]
    headline_actual_raw: str
    headline_forecast_raw: str
    headline_previous_raw: str
    headline_actual: Optional[float]
    headline_forecast: Optional[float]
    headline_previous: Optional[float]
    source_files: str
    source_value_conflicts: str
    original_row: Dict[str, str]
    previous_release_actual: Optional[float] = None
    previous_release_date: str = ""
    previous_revision: Optional[float] = None
    previous_revision_days: str = ""


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float


# =============================================================================
# 3. GENERIC HELPERS
# =============================================================================


def clean(value: object) -> str:
    return "" if value is None else str(value).replace("\ufeff", "").strip()


def parse_number(value: object) -> Optional[float]:
    s = clean(value)
    if not s or s.casefold() in {"-", "—", "n/a", "na", "none", "null", "nan"}:
        return None
    s = s.replace("%", "").replace(",", "").replace(" ", "")
    try:
        result = float(s)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def parse_datetime(value: object) -> Optional[datetime]:
    s = re.sub(r"\s+", " ", clean(value))
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(second=0, microsecond=0)


def pct_change(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if current is None or base is None or base == 0:
        return None
    return (current / base - 1.0) * 100.0


def fmt(value: Optional[float], digits: int = 6) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.{digits}f}"


def as_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def find_period(year: int) -> str:
    for first, last, label in ECONOMIC_PERIODS:
        if first <= year <= last:
            return label
    return "unassigned"


def safe_div(a: float, b: float) -> Optional[float]:
    return a / b if b else None


def read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл календаря: {path}")
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"Не удалось прочитать CSV: {path}")


def first_value(row: Dict[str, str], *keys: str) -> str:
    for key in keys:
        value = clean(row.get(key, ""))
        if value:
            return value
    return ""


# =============================================================================
# 4. LOAD AND ENRICH THE CALENDAR EVENTS
# =============================================================================


def load_events(path: Path) -> Tuple[List[Event], List[str]]:
    rows = read_csv_dicts(path)
    events: List[Event] = []
    excluded: List[str] = []

    for line_no, row in enumerate(rows, start=2):
        raw_date = first_value(row, "release_date", "date")
        try:
            release_date = date.fromisoformat(raw_date[:10])
        except ValueError:
            excluded.append(f"Строка {line_no}: некорректная дата '{raw_date}'")
            continue

        if not (ANALYSIS_START_DATE <= release_date <= ANALYSIS_END_DATE):
            excluded.append(f"{release_date.isoformat()}: вне исторического диапазона")
            continue

        release_utc = parse_datetime(first_value(row, "scheduled_datetime_utc", "datetime_utc"))
        if release_utc is None:
            # Calendar builder's scheduled time is 08:30 New York; use this only as
            # a fallback when the normalized UTC column is absent.
            excluded.append(f"{release_date.isoformat()}: нет scheduled_datetime_utc")
            continue

        actual_raw = first_value(row, "actual", "actual_numeric")
        forecast_raw = first_value(row, "forecast", "forecast_numeric")
        previous_raw = first_value(row, "previous", "previous_numeric")
        headline_actual_raw = first_value(row, "headline_retail_actual")
        headline_forecast_raw = first_value(row, "headline_retail_forecast")
        headline_previous_raw = first_value(row, "headline_retail_previous")

        event = Event(
            event_id=release_date.isoformat(),
            release_date=release_date,
            release_utc=release_utc,
            actual_raw=actual_raw,
            forecast_raw=forecast_raw,
            previous_raw=previous_raw,
            actual=parse_number(first_value(row, "actual_numeric", "actual")),
            forecast=parse_number(first_value(row, "forecast_numeric", "forecast")),
            previous=parse_number(first_value(row, "previous_numeric", "previous")),
            headline_actual_raw=headline_actual_raw,
            headline_forecast_raw=headline_forecast_raw,
            headline_previous_raw=headline_previous_raw,
            headline_actual=parse_number(headline_actual_raw),
            headline_forecast=parse_number(headline_forecast_raw),
            headline_previous=parse_number(headline_previous_raw),
            source_files=first_value(row, "source_files"),
            source_value_conflicts=first_value(row, "source_value_conflicts"),
            original_row=row,
        )
        events.append(event)

    events.sort(key=lambda e: (e.release_utc, e.event_id))

    # Derive revisions: the current release's `previous` value versus the previous
    # Core Retail release's `actual` value as it appeared in that earlier event row.
    # Only compare plausible consecutive monthly releases (20-45 days apart).
    for index, current in enumerate(events):
        if index == 0:
            continue
        prior = events[index - 1]
        gap = (current.release_date - prior.release_date).days
        current.previous_release_actual = prior.actual
        current.previous_release_date = prior.release_date.isoformat()
        current.previous_revision_days = str(gap)
        if 20 <= gap <= 45 and current.previous is not None and prior.actual is not None:
            current.previous_revision = current.previous - prior.actual

    return events, excluded


def compare_sign(actual: Optional[float], forecast: Optional[float], positive: str, negative: str, same: str) -> str:
    if actual is None or forecast is None:
        return "missing"
    diff = actual - forecast
    if abs(diff) < 1e-10:
        return same
    return positive if diff > 0 else negative


def surprise_sign(event: Event) -> str:
    return compare_sign(event.actual, event.forecast, "beat", "miss", "inline")


def revision_sign(event: Event) -> str:
    if event.previous_revision is None:
        return "missing"
    if abs(event.previous_revision) < 1e-10:
        return "revision_inline"
    return "revision_up" if event.previous_revision > 0 else "revision_down"


def headline_surprise_sign(event: Event) -> str:
    return compare_sign(event.headline_actual, event.headline_forecast, "beat", "miss", "inline")


# =============================================================================
# 5. XAUUSD DAILY FILES (UTC)
# =============================================================================


def xau_path(side: str, day: date) -> Path:
    raw = XAU_FILE_TEMPLATE.format(
        side=side,
        year=f"{day.year:04d}",
        month=f"{day.month:02d}",
        date=day.isoformat(),
    )
    return Path(raw)


def parse_xau_timestamp(value: str) -> Optional[datetime]:
    s = re.sub(r"\s+", " ", clean(value))
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=XAU_NAIVE_TIMEZONE)
    return dt.astimezone(UTC).replace(second=0, microsecond=0)


def read_xau_day(path: Path, needed_starts: set[datetime]) -> Tuple[Dict[datetime, Candle], int, str]:
    """Read only the timestamps needed for event windows from one daily XAU CSV."""
    if not path.exists():
        return {}, 0, "MISSING_FILE"

    output: Dict[datetime, Candle] = {}
    rows_seen = 0
    last_error = ""
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    return {}, 0, "NO_HEADER"
                name_map = {name.strip().casefold(): name for name in reader.fieldnames}
                ts_col = name_map.get("timestamp") or name_map.get("datetime") or name_map.get("date")
                open_col = name_map.get("open")
                high_col = name_map.get("high")
                low_col = name_map.get("low")
                close_col = name_map.get("close")
                if not all((ts_col, open_col, high_col, low_col, close_col)):
                    return {}, 0, "MISSING_REQUIRED_COLUMNS"

                for row in reader:
                    rows_seen += 1
                    ts = parse_xau_timestamp(row.get(ts_col, ""))
                    if ts is None or ts not in needed_starts:
                        continue
                    values = [parse_number(row.get(c, "")) for c in (open_col, high_col, low_col, close_col)]
                    if any(v is None for v in values):
                        continue
                    output[ts] = Candle(*[float(v) for v in values])
                return output, rows_seen, encoding
        except UnicodeDecodeError as exc:
            last_error = str(exc)
            continue
        except (OSError, csv.Error) as exc:
            return {}, rows_seen, f"READ_ERROR: {exc}"
    return {}, rows_seen, f"ENCODING_ERROR: {last_error}"


def load_xau(events: Sequence[Event]) -> Tuple[Dict[str, Dict[datetime, Candle]], Dict[str, object]]:
    """Load BID/ASK candles around all event windows; daily files are cached."""
    required_starts_by_day: Dict[date, set[datetime]] = defaultdict(set)
    for event in events:
        for close_offset in range(WINDOW_START_MINUTE, WINDOW_END_MINUTE + 1):
            bar_start = event.release_utc + timedelta(minutes=close_offset - 1)
            required_starts_by_day[bar_start.date()].add(bar_start)

    all_data: Dict[str, Dict[datetime, Candle]] = {"BID": {}, "ASK": {}}
    file_status: Dict[str, str] = {}
    counts: Counter = Counter()

    for side in ("BID", "ASK"):
        for day, needed in sorted(required_starts_by_day.items()):
            path = xau_path(side, day)
            bars, rows_seen, status = read_xau_day(path, needed)
            all_data[side].update(bars)
            file_status[f"{side}|{day.isoformat()}"] = f"{status}; rows={rows_seen}; matched={len(bars)}; path={path}"
            counts[f"{side}_files_found"] += int(status not in {"MISSING_FILE", "NO_HEADER", "MISSING_REQUIRED_COLUMNS"} and not status.startswith(("READ_ERROR", "ENCODING_ERROR")))
            counts[f"{side}_files_missing"] += int(status == "MISSING_FILE")
            counts[f"{side}_bars_loaded"] += len(bars)

    stats = {"file_status": file_status, "counts": dict(counts), "required_day_count": len(required_starts_by_day)}
    return all_data, stats


def mid_candle(bid: Optional[Candle], ask: Optional[Candle]) -> Optional[Candle]:
    if bid is None or ask is None:
        return None
    return Candle(
        (bid.open + ask.open) / 2.0,
        (bid.high + ask.high) / 2.0,
        (bid.low + ask.low) / 2.0,
        (bid.close + ask.close) / 2.0,
    )


# =============================================================================
# 6. HISTDATA M1 FILES (FIXED EST, UTC-5 YEAR-ROUND)
# =============================================================================


def fx_path(pair: str, year: int) -> Path:
    return Path(FX_FILE_TEMPLATE.format(pair=pair, year=f"{year:04d}"))


def parse_histdata_timestamp(value: str) -> Optional[datetime]:
    """Parse YYYYMMDD HHMMSS / YYYYMMDDHHMMSS as fixed EST (UTC-5, no DST)."""
    s = clean(value).strip('"')
    digits = re.sub(r"\s+", "", s)
    if not re.fullmatch(r"\d{14}", digits):
        return None
    try:
        local_fixed_est = datetime.strptime(digits, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return local_fixed_est.replace(tzinfo=HISTDATA_TZ).astimezone(UTC).replace(second=0, microsecond=0)


def split_histdata_line(line: str) -> List[str]:
    # HistData normally uses semicolons; some mirrors/exporters use commas.
    semis = line.count(";")
    commas = line.count(",")
    delimiter = ";" if semis > commas else ","
    return next(csv.reader([line], delimiter=delimiter))


def load_one_fx_file(path: Path, needed_local_est: set[datetime]) -> Tuple[Dict[datetime, float], Dict[str, int], str]:
    """Read only relevant rows; retain CLOSE (field 5 in HistData M1 bars)."""
    if not path.exists():
        return {}, {"rows_seen": 0, "matched": 0, "bad_rows": 0, "duplicates": 0}, "MISSING_FILE"

    rows_seen = matched = bad_rows = duplicates = 0
    output: Dict[datetime, float] = {}
    last_error = ""

    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rows_seen += 1
                    fields = split_histdata_line(line)
                    if len(fields) < 5:
                        bad_rows += 1
                        continue
                    ts_utc = parse_histdata_timestamp(fields[0])
                    if ts_utc is None:
                        # Header row is expected to fail timestamp parsing; skip it.
                        continue
                    local_naive = ts_utc.astimezone(HISTDATA_TZ).replace(tzinfo=None)
                    if local_naive not in needed_local_est:
                        continue
                    close_value = parse_number(fields[4])
                    if close_value is None or close_value <= 0:
                        bad_rows += 1
                        continue
                    if ts_utc in output:
                        duplicates += 1
                    output[ts_utc] = close_value
                    matched += 1
            return output, {"rows_seen": rows_seen, "matched": matched, "bad_rows": bad_rows, "duplicates": duplicates}, encoding
        except UnicodeDecodeError as exc:
            last_error = str(exc)
            continue
        except (OSError, csv.Error) as exc:
            return output, {"rows_seen": rows_seen, "matched": matched, "bad_rows": bad_rows, "duplicates": duplicates}, f"READ_ERROR: {exc}"
    return output, {"rows_seen": rows_seen, "matched": matched, "bad_rows": bad_rows, "duplicates": duplicates}, f"ENCODING_ERROR: {last_error}"


def load_fx(events: Sequence[Event]) -> Tuple[Dict[str, Dict[datetime, float]], Dict[str, object]]:
    """Load only HistData bars needed by all event windows and short gap fallbacks."""
    needed_utc_by_year: Dict[int, set[datetime]] = defaultdict(set)
    for event in events:
        for close_offset in range(WINDOW_START_MINUTE, WINDOW_END_MINUTE + 1):
            bar_start_utc = event.release_utc + timedelta(minutes=close_offset - 1)
            for stale in range(MAX_FX_STALE_MINUTES + 1):
                needed_utc_by_year[bar_start_utc.year].add(bar_start_utc - timedelta(minutes=stale))

    # HistData file years are determined after conversion to fixed EST, not UTC.
    needed_local_by_pair_year: Dict[int, set[datetime]] = defaultdict(set)
    for utc_times in needed_utc_by_year.values():
        for ts in utc_times:
            local = ts.astimezone(HISTDATA_TZ).replace(tzinfo=None)
            needed_local_by_pair_year[local.year].add(local)

    all_data: Dict[str, Dict[datetime, float]] = {pair: {} for pair in FX_PAIRS}
    file_status: Dict[str, str] = {}
    counts: Counter = Counter()

    for pair in FX_PAIRS:
        for year, needed_local in sorted(needed_local_by_pair_year.items()):
            path = fx_path(pair, year)
            series, file_stats, status = load_one_fx_file(path, needed_local)
            all_data[pair].update(series)
            file_status[f"{pair}|{year}"] = (
                f"{status}; rows={file_stats['rows_seen']}; matched={file_stats['matched']}; "
                f"bad={file_stats['bad_rows']}; duplicates={file_stats['duplicates']}; path={path}"
            )
            counts[f"{pair}_files_found"] += int(status not in {"MISSING_FILE"} and not status.startswith(("READ_ERROR", "ENCODING_ERROR")))
            counts[f"{pair}_files_missing"] += int(status == "MISSING_FILE")
            counts[f"{pair}_bars_loaded"] += len(series)
            counts[f"{pair}_bad_rows"] += file_stats["bad_rows"]
            counts[f"{pair}_duplicates"] += file_stats["duplicates"]

    stats = {"file_status": file_status, "counts": dict(counts), "required_year_count": len(needed_local_by_pair_year)}
    return all_data, stats


def asof_value(series: Dict[datetime, float], target: datetime, max_stale: int) -> Tuple[Optional[float], Optional[int]]:
    """Return latest value at/before target, only when no more than max_stale minutes old."""
    for age in range(max_stale + 1):
        candidate = target - timedelta(minutes=age)
        value = series.get(candidate)
        if value is not None:
            return value, age
    return None, None


def calculate_dxy(values: Dict[str, Optional[float]]) -> Optional[float]:
    if any(values.get(pair) is None or values[pair] <= 0 for pair in FX_PAIRS):
        return None
    result = DXY_CONSTANT
    for pair, exponent in DXY_EXPONENTS.items():
        price = values.get(pair)
        if price is None or price <= 0:
            return None
        result *= price ** exponent
    return result


# =============================================================================
# 7. BUILD MINUTE WINDOWS AND PER-EVENT METRICS
# =============================================================================


def event_window_rows(
    event: Event,
    xau: Dict[str, Dict[datetime, Candle]],
    fx: Dict[str, Dict[datetime, float]],
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for close_offset in range(WINDOW_START_MINUTE, WINDOW_END_MINUTE + 1):
        bar_start = event.release_utc + timedelta(minutes=close_offset - 1)
        bar_end = event.release_utc + timedelta(minutes=close_offset)

        bid = xau["BID"].get(bar_start)
        ask = xau["ASK"].get(bar_start)
        mid = mid_candle(bid, ask)

        pair_values: Dict[str, Optional[float]] = {}
        pair_ages: Dict[str, Optional[int]] = {}
        for pair in FX_PAIRS:
            value, age = asof_value(fx[pair], bar_start, MAX_FX_STALE_MINUTES)
            pair_values[pair] = value
            pair_ages[pair] = age

        dxy = calculate_dxy(pair_values)
        present_pairs = sum(value is not None for value in pair_values.values())
        ffilled_pairs = sum((age or 0) > 0 for age in pair_ages.values() if age is not None)

        row: Dict[str, str] = {
            "event_id": event.event_id,
            "release_date": event.release_date.isoformat(),
            "year": str(event.release_date.year),
            "period": find_period(event.release_date.year),
            "minutes_from_release": str(close_offset),
            "bar_start_utc": as_iso(bar_start),
            "bar_close_time_utc": as_iso(bar_end),
            "bar_close_time_new_york": bar_end.astimezone(NEW_YORK_TZ).isoformat(timespec="seconds"),
            "bar_close_time_chisinau": bar_end.astimezone(CHISINAU_TZ).isoformat(timespec="seconds"),
            "xau_bid_open": fmt(bid.open, 3) if bid else "",
            "xau_bid_high": fmt(bid.high, 3) if bid else "",
            "xau_bid_low": fmt(bid.low, 3) if bid else "",
            "xau_bid_close": fmt(bid.close, 3) if bid else "",
            "xau_ask_open": fmt(ask.open, 3) if ask else "",
            "xau_ask_high": fmt(ask.high, 3) if ask else "",
            "xau_ask_low": fmt(ask.low, 3) if ask else "",
            "xau_ask_close": fmt(ask.close, 3) if ask else "",
            "xau_mid_open": fmt(mid.open, 3) if mid else "",
            "xau_mid_high": fmt(mid.high, 3) if mid else "",
            "xau_mid_low": fmt(mid.low, 3) if mid else "",
            "xau_mid_close": fmt(mid.close, 3) if mid else "",
            "xau_spread_close": fmt(ask.close - bid.close, 5) if ask and bid else "",
            "dxy_reconstructed_close": fmt(dxy, 6) if dxy is not None else "",
            "fx_pairs_present": str(present_pairs),
            "fx_pairs_stale_filled": str(ffilled_pairs),
        }
        for pair in FX_PAIRS:
            row[f"{pair}_close"] = fmt(pair_values[pair], 7)
            row[f"{pair}_stale_minutes"] = "" if pair_ages[pair] is None else str(pair_ages[pair])
        rows.append(row)
    return rows


def find_close(rows_by_offset: Dict[int, Dict[str, str]], offset: int, field: str, max_fallback: int) -> Tuple[Optional[float], Optional[int]]:
    """Use exact close offset or the latest earlier one, never a future candle."""
    for age in range(max_fallback + 1):
        used_offset = offset - age
        row = rows_by_offset.get(used_offset)
        if row is None:
            continue
        value = parse_number(row.get(field, ""))
        if value is not None:
            return value, used_offset
    return None, None


def excursion_pct(
    rows_by_offset: Dict[int, Dict[str, str]],
    baseline: Optional[float],
    horizon: int,
    field: str,
    mode: str,
) -> Optional[float]:
    if baseline is None or baseline == 0:
        return None
    values: List[float] = []
    for offset in range(1, horizon + 1):
        value = parse_number(rows_by_offset.get(offset, {}).get(field, ""))
        if value is not None:
            values.append(value)
    if not values:
        return None
    extreme = max(values) if mode == "max" else min(values)
    return (extreme / baseline - 1.0) * 100.0


def build_event_summary(event: Event, window: List[Dict[str, str]]) -> Dict[str, str]:
    rows_by_offset = {int(row["minutes_from_release"]): row for row in window}

    # Baseline = close of the minute immediately before the release (close time t=0).
    bid_base, bid_base_offset = find_close(rows_by_offset, 0, "xau_bid_close", MAX_XAU_HORIZON_FALLBACK_MINUTES)
    ask_base, ask_base_offset = find_close(rows_by_offset, 0, "xau_ask_close", MAX_XAU_HORIZON_FALLBACK_MINUTES)
    mid_base, mid_base_offset = find_close(rows_by_offset, 0, "xau_mid_close", MAX_XAU_HORIZON_FALLBACK_MINUTES)
    dxy_base, dxy_base_offset = find_close(rows_by_offset, 0, "dxy_reconstructed_close", MAX_FX_STALE_MINUTES)

    bid_window_count = sum(bool(row["xau_bid_close"]) for row in window)
    ask_window_count = sum(bool(row["xau_ask_close"]) for row in window)
    mid_window_count = sum(bool(row["xau_mid_close"]) for row in window)
    dxy_window_count = sum(bool(row["dxy_reconstructed_close"]) for row in window)
    total_window_count = len(window)
    bid_coverage = bid_window_count / total_window_count if total_window_count else 0.0
    ask_coverage = ask_window_count / total_window_count if total_window_count else 0.0
    mid_coverage = mid_window_count / total_window_count if total_window_count else 0.0
    dxy_coverage = dxy_window_count / total_window_count if total_window_count else 0.0

    result: Dict[str, str] = {
        "event_id": event.event_id,
        "release_date": event.release_date.isoformat(),
        "year": str(event.release_date.year),
        "period": find_period(event.release_date.year),
        "scheduled_datetime_utc": as_iso(event.release_utc),
        "scheduled_datetime_new_york": event.release_utc.astimezone(NEW_YORK_TZ).isoformat(timespec="seconds"),
        "scheduled_datetime_chisinau": event.release_utc.astimezone(CHISINAU_TZ).isoformat(timespec="seconds"),
        "actual": event.actual_raw,
        "forecast": event.forecast_raw,
        "previous_as_reported_now": event.previous_raw,
        "actual_numeric": fmt(event.actual),
        "forecast_numeric": fmt(event.forecast),
        "previous_numeric": fmt(event.previous),
        "surprise_actual_minus_forecast": fmt(event.actual - event.forecast if event.actual is not None and event.forecast is not None else None),
        "surprise_sign": surprise_sign(event),
        "previous_revision_sign": revision_sign(event),
        "headline_surprise_sign": headline_surprise_sign(event),
        "previous_release_date": event.previous_release_date,
        "previous_release_actual_in_prior_row": fmt(event.previous_release_actual),
        "previous_value_revision": fmt(event.previous_revision),
        "days_between_releases_for_revision_check": event.previous_revision_days,
        "headline_retail_actual": event.headline_actual_raw,
        "headline_retail_forecast": event.headline_forecast_raw,
        "headline_retail_previous": event.headline_previous_raw,
        "headline_retail_surprise": fmt(event.headline_actual - event.headline_forecast if event.headline_actual is not None and event.headline_forecast is not None else None),
        "xau_bid_coverage_pct": fmt(bid_coverage * 100, 2),
        "xau_ask_coverage_pct": fmt(ask_coverage * 100, 2),
        "xau_mid_coverage_pct": fmt(mid_coverage * 100, 2),
        "dxy_coverage_pct": fmt(dxy_coverage * 100, 2),
        "xau_mid_quality": "OK" if mid_coverage >= MIN_XAU_WINDOW_COVERAGE else "LOW_COVERAGE",
        "dxy_quality": "OK" if dxy_coverage >= MIN_DXY_WINDOW_COVERAGE else "LOW_COVERAGE",
        "xau_baseline_mid": fmt(mid_base, 3),
        "xau_baseline_bid": fmt(bid_base, 3),
        "xau_baseline_ask": fmt(ask_base, 3),
        "xau_baseline_offset_used": str(mid_base_offset) if mid_base_offset is not None else "",
        "dxy_baseline": fmt(dxy_base, 6),
        "dxy_baseline_offset_used": str(dxy_base_offset) if dxy_base_offset is not None else "",
        "calendar_date_check": first_value(event.original_row, "date_check"),
        "calendar_time_check": first_value(event.original_row, "time_check"),
        "calendar_provided_utc_check": first_value(event.original_row, "provided_datetime_utc_check"),
        "calendar_raw_datetime": first_value(event.original_row, "raw_datetime"),
        "source_files": event.source_files,
        "source_value_conflicts": event.source_value_conflicts,
    }

    for horizon in HORIZONS:
        # XAU fixed-horizon closes (with at most 2-minute backward fallback).
        for field_name, source_field, base, prefix in (
            ("bid", "xau_bid_close", bid_base, "xau_bid"),
            ("ask", "xau_ask_close", ask_base, "xau_ask"),
            ("mid", "xau_mid_close", mid_base, "xau_mid"),
        ):
            price, used_offset = find_close(rows_by_offset, horizon, source_field, MAX_XAU_HORIZON_FALLBACK_MINUTES)
            quality_ok = mid_coverage >= MIN_XAU_WINDOW_COVERAGE if field_name == "mid" else (
                bid_coverage >= MIN_XAU_WINDOW_COVERAGE if field_name == "bid" else ask_coverage >= MIN_XAU_WINDOW_COVERAGE
            )
            result[f"{prefix}_return_pct_m{horizon}"] = fmt(pct_change(price, base) if quality_ok else None, 5)
            result[f"{prefix}_used_close_offset_m{horizon}"] = str(used_offset) if used_offset is not None else ""

        dxy_price, dxy_used_offset = find_close(rows_by_offset, horizon, "dxy_reconstructed_close", MAX_FX_STALE_MINUTES)
        result[f"dxy_return_pct_m{horizon}"] = fmt(pct_change(dxy_price, dxy_base) if dxy_coverage >= MIN_DXY_WINDOW_COVERAGE else None, 6)
        result[f"dxy_used_close_offset_m{horizon}"] = str(dxy_used_offset) if dxy_used_offset is not None else ""

        # Individual FX pair response from the same event-relative baseline.
        for pair in FX_PAIRS:
            base, _ = find_close(rows_by_offset, 0, f"{pair}_close", MAX_FX_STALE_MINUTES)
            price, used_offset = find_close(rows_by_offset, horizon, f"{pair}_close", MAX_FX_STALE_MINUTES)
            result[f"{pair}_return_pct_m{horizon}"] = fmt(pct_change(price, base), 6)
            result[f"{pair}_used_close_offset_m{horizon}"] = str(used_offset) if used_offset is not None else ""

        result[f"xau_mid_max_up_pct_m{horizon}"] = fmt(
            excursion_pct(rows_by_offset, mid_base, horizon, "xau_mid_high", "max") if mid_coverage >= MIN_XAU_WINDOW_COVERAGE else None,
            5,
        )
        result[f"xau_mid_max_down_pct_m{horizon}"] = fmt(
            excursion_pct(rows_by_offset, mid_base, horizon, "xau_mid_low", "min") if mid_coverage >= MIN_XAU_WINDOW_COVERAGE else None,
            5,
        )

    # Pre-release move: close at t=0 compared with close 30 minutes before release.
    pre30, pre30_offset = find_close(rows_by_offset, -30, "xau_mid_close", MAX_XAU_HORIZON_FALLBACK_MINUTES)
    result["xau_mid_pre_move_pct_m30_to_release"] = fmt(
        pct_change(mid_base, pre30) if mid_coverage >= MIN_XAU_WINDOW_COVERAGE else None, 5
    )
    result["xau_pre30_used_offset"] = str(pre30_offset) if pre30_offset is not None else ""

    return result


# =============================================================================
# 8. GROUP STATISTICS
# =============================================================================


def pearson_corr(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denom = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    return sum(a * b for a, b in zip(dx, dy)) / denom if denom else None


def classify_return(value: Optional[float]) -> str:
    if value is None:
        return "missing"
    if value > FLAT_THRESHOLD_PCT:
        return "up"
    if value < -FLAT_THRESHOLD_PCT:
        return "down"
    return "flat"


def summarize_bucket(rows: Sequence[Dict[str, str]], group_type: str, group_value: str, condition_field: str, condition: str) -> Dict[str, str]:
    selected = [row for row in rows if row.get(condition_field) == condition]
    result: Dict[str, str] = {
        "group_type": group_type,
        "group": group_value,
        "condition_field": condition_field,
        "condition": condition,
        "events_total": str(len(selected)),
        "flat_threshold_pct": fmt(FLAT_THRESHOLD_PCT, 4),
    }

    for horizon in HORIZONS:
        # Gold midpoint returns.
        gold_values: List[float] = []
        for row in selected:
            value = parse_number(row.get(f"xau_mid_return_pct_m{horizon}", ""))
            if value is not None:
                gold_values.append(value)
        counts = Counter(classify_return(v) for v in gold_values)
        n = len(gold_values)
        result[f"xau_n_m{horizon}"] = str(n)
        result[f"xau_mean_pct_m{horizon}"] = fmt(statistics.mean(gold_values) if n else None, 5)
        result[f"xau_median_pct_m{horizon}"] = fmt(statistics.median(gold_values) if n else None, 5)
        result[f"xau_up_pct_m{horizon}"] = fmt(counts["up"] / n * 100 if n else None, 2)
        result[f"xau_down_pct_m{horizon}"] = fmt(counts["down"] / n * 100 if n else None, 2)
        result[f"xau_flat_pct_m{horizon}"] = fmt(counts["flat"] / n * 100 if n else None, 2)

        dxy_values = [
            value for row in selected
            if (value := parse_number(row.get(f"dxy_return_pct_m{horizon}", ""))) is not None
        ]
        dxy_counts = Counter(classify_return(v) for v in dxy_values)
        dn = len(dxy_values)
        result[f"dxy_n_m{horizon}"] = str(dn)
        result[f"dxy_mean_pct_m{horizon}"] = fmt(statistics.mean(dxy_values) if dn else None, 6)
        result[f"dxy_median_pct_m{horizon}"] = fmt(statistics.median(dxy_values) if dn else None, 6)
        result[f"dxy_up_pct_m{horizon}"] = fmt(dxy_counts["up"] / dn * 100 if dn else None, 2)
        result[f"dxy_down_pct_m{horizon}"] = fmt(dxy_counts["down"] / dn * 100 if dn else None, 2)

    # The m5 correlations are descriptive only, not predictive claims.
    corr_pairs = (
        ("surprise_actual_minus_forecast", "xau_mid_return_pct_m5", "corr_surprise_xau_m5"),
        ("previous_value_revision", "xau_mid_return_pct_m5", "corr_revision_xau_m5"),
        ("dxy_return_pct_m5", "xau_mid_return_pct_m5", "corr_dxy_xau_m5"),
    )
    for x_key, y_key, out_key in corr_pairs:
        xs: List[float] = []
        ys: List[float] = []
        for row in selected:
            x = parse_number(row.get(x_key, ""))
            y = parse_number(row.get(y_key, ""))
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
        result[out_key] = fmt(pearson_corr(xs, ys), 4)
        result[out_key + "_n"] = str(len(xs))

    return result


def make_summaries(event_rows: Sequence[Dict[str, str]]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    annual: List[Dict[str, str]] = []
    period_rows: List[Dict[str, str]] = []
    by_year: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    by_period: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in event_rows:
        by_year[row["year"]].append(row)
        by_period[row["period"]].append(row)

    dimensions = (
        ("surprise_sign", ("beat", "miss", "inline", "missing"), "surprise"),
        ("previous_revision_sign", ("revision_up", "revision_down", "revision_inline", "missing"), "revision"),
        ("headline_surprise_sign", ("beat", "miss", "inline", "missing"), "headline"),
    )

    all_rows = list(event_rows)
    overall: List[Dict[str, str]] = []
    for field_name, conditions, label in dimensions:
        for condition in conditions:
            overall.append(summarize_bucket(all_rows, f"overall_{label}", "ALL", field_name, condition))

    for year in sorted(by_year):
        for field_name, conditions, label in dimensions:
            for condition in conditions:
                annual.append(summarize_bucket(by_year[year], f"year_{label}", year, field_name, condition))

    for period in [p[2] for p in ECONOMIC_PERIODS] + ["unassigned"]:
        if period not in by_period:
            continue
        for field_name, conditions, label in dimensions:
            for condition in conditions:
                period_rows.append(summarize_bucket(by_period[period], f"period_{label}", period, field_name, condition))

    return overall + annual, period_rows


# =============================================================================
# 9. OUTPUT / REPORTS
# =============================================================================


def write_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields: List[str] = []
    seen = set()
    for row in rows:
        for field in row:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: Sequence[Dict[str, str]], columns: Sequence[Tuple[str, str]]) -> str:
    headers = [label for _, label in columns]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        values = []
        for key, _ in columns:
            value = clean(row.get(key, ""))
            values.append(value if value else "—")
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def create_analysis_report(path: Path, events: Sequence[Event], event_rows: Sequence[Dict[str, str]], annual: Sequence[Dict[str, str]], period_rows: Sequence[Dict[str, str]], xau_stats: Dict[str, object], fx_stats: Dict[str, object], excluded: Sequence[str]) -> None:
    total = len(event_rows)
    xau_ok = sum(row.get("xau_mid_quality") == "OK" for row in event_rows)
    dxy_ok = sum(row.get("dxy_quality") == "OK" for row in event_rows)
    beat = sum(row.get("surprise_sign") == "beat" for row in event_rows)
    miss = sum(row.get("surprise_sign") == "miss" for row in event_rows)
    inline = sum(row.get("surprise_sign") == "inline" for row in event_rows)

    p5_rows = [r for r in period_rows if r["group_type"] == "period_surprise" and r["condition"] in {"beat", "miss"}]
    report_columns = [
        ("group", "Период"),
        ("condition", "Результат vs прогноз"),
        ("events_total", "Событий"),
        ("xau_n_m5", "XAU n (5m)"),
        ("xau_mean_pct_m5", "XAU среднее, % (5m)"),
        ("xau_median_pct_m5", "XAU медиана, % (5m)"),
        ("xau_up_pct_m5", "XAU вверх, % (5m)"),
        ("xau_down_pct_m5", "XAU вниз, % (5m)"),
        ("dxy_n_m5", "DXY n (5m)"),
        ("dxy_mean_pct_m5", "DXY среднее, % (5m)"),
    ]

    lines = [
        "# Core Retail Sales m/m — исторические реакции XAUUSD и DXY",
        "",
        f"Период данных: {ANALYSIS_START_DATE.isoformat()} — {ANALYSIS_END_DATE.isoformat()}",
        f"Всего релизов в календаре: **{total}**; beat: **{beat}**, miss: **{miss}**, inline: **{inline}**.",
        f"Окно котировок: от **{WINDOW_START_MINUTE}** до **+{WINDOW_END_MINUTE}** минут; основные горизонты: {', '.join(str(x) for x in HORIZONS)} минут.",
        f"Качество XAU midpoint: **{xau_ok}/{total}** релизов прошли порог {MIN_XAU_WINDOW_COVERAGE:.0%} покрытия.",
        f"Качество реконструированного DXY: **{dxy_ok}/{total}** релизов прошли порог {MIN_DXY_WINDOW_COVERAGE:.0%} покрытия.",
        "",
        "## Как считаются переменные",
        "- Surprise = Actual − Forecast; знак: beat / miss / inline.",
        "- Revision = значение Previous в текущем релизе − Actual из предыдущего релиза. Считается только при интервале 20–45 дней.",
        "- Временная метка M1 трактуется как начало свечи. База XAU/DXY — закрытие свечи, заканчивающейся в момент публикации (t=0).",
        "- Реакция +5m — закрытие свечи в момент t=+5m относительно этой базы. Для редкого пропуска допускается только предыдущая свеча не старше 2 минут; фактический offset записывается в CSV.",
        "- XAU midpoint = среднее BID и ASK на одной и той же минуте. Для основной статистики используется midpoint, а BID/ASK сохранены отдельно.",
        "- DXY рассчитан по шести HistData BID close. Если минутная свеча компонента отсутствует, разрешён перенос последнего значения не более чем на 2 минуты; индекс строится только при наличии всех 6 пар.",
        "- Событие допускается в групповые XAU/DXY-расчёты только при покрытии соответствующего окна не ниже заданного порога.",
        "",
        "## Общая статистика (5 минут)",
        markdown_table(
            [r for r in annual if r["group_type"] == "overall_surprise" and r["condition"] in {"beat", "miss", "inline"}],
            report_columns,
        ) if any(r["group_type"] == "overall_surprise" for r in annual) else "Недостаточно данных для общей таблицы.",
        "",
        "## Периодные сравнения (5 минут)",
        markdown_table(p5_rows, report_columns) if p5_rows else "Недостаточно данных для периодной таблицы.",
        "",
        "## Ограничения интерпретации",
        "- Таблица показывает исторические условные частоты и средние, а не причинно-следственный эффект и не готовый торговый сигнал.",
        "- Периодные блоки заданы для удобства сравнения; границы макроэкономических режимов условны.",
        "- Данные 2026 года сейчас исключены: конфигурация ограничена концом 2025 года, пока валютная история за 2026 год не добавлена.",
        "- Revisions могут быть пустыми, если календарь не содержит предыдущий релиз или между событиями слишком большой разрыв.",
        "",
        "## Детали качества данных",
        f"- Загружено XAU BID свечей: {xau_stats.get('counts', {}).get('BID_bars_loaded', 0)}.",
        f"- Загружено XAU ASK свечей: {xau_stats.get('counts', {}).get('ASK_bars_loaded', 0)}.",
        f"- Пропущено календарных строк/дат: {len(excluded)}.",
        "",
        "Подробности см. в `data_quality_report.txt`, `event_reactions.csv` и `event_window_bars.csv`.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_quality_report(path: Path, events: Sequence[Event], event_rows: Sequence[Dict[str, str]], xau_stats: Dict[str, object], fx_stats: Dict[str, object], excluded: Sequence[str]) -> None:
    lines = [
        "RETAIL REACTION ANALYZER — DATA QUALITY",
        "=" * 78,
        f"Calendar events analyzed: {len(events)}",
        f"Analysis range: {ANALYSIS_START_DATE} -> {ANALYSIS_END_DATE}",
        f"XAU window: {WINDOW_START_MINUTE} -> +{WINDOW_END_MINUTE} minutes",
        f"XAU min coverage: {MIN_XAU_WINDOW_COVERAGE:.1%}",
        f"DXY min coverage: {MIN_DXY_WINDOW_COVERAGE:.1%}",
        f"Events with calendar source conflicts: {sum(bool(e.source_value_conflicts) for e in events)}",
        f"XAU minute carry for horizon endpoint: {MAX_XAU_HORIZON_FALLBACK_MINUTES} minute(s), backward only",
        f"FX component stale limit: {MAX_FX_STALE_MINUTES} minute(s), backward only",
        "",
        "EVENT COVERAGE",
    ]
    for row in event_rows:
        lines.append(
            f"{row['release_date']} | surprise={row['surprise_sign']} | "
            f"XAU_mid={row['xau_mid_coverage_pct']}% ({row['xau_mid_quality']}) | "
            f"DXY={row['dxy_coverage_pct']}% ({row['dxy_quality']}) | "
            f"revision={row['previous_value_revision'] or 'n/a'}"
        )

    lines.extend(["", "XAU FILE STATISTICS"])
    for key, value in xau_stats.get("counts", {}).items():
        lines.append(f"{key}: {value}")
    lines.extend(["", "XAU FILE STATUS"])
    for key, value in sorted(xau_stats.get("file_status", {}).items()):
        lines.append(f"{key}: {value}")

    lines.extend(["", "FX / HISTDATA STATISTICS"])
    for key, value in fx_stats.get("counts", {}).items():
        lines.append(f"{key}: {value}")
    lines.extend(["", "FX FILE STATUS"])
    for key, value in sorted(fx_stats.get("file_status", {}).items()):
        lines.append(f"{key}: {value}")

    lines.extend(["", "CALENDAR ROWS EXCLUDED"])
    lines.extend(excluded if excluded else ["none"])
    lines.extend([
        "",
        "NOTES",
        "1. HistData timestamps are interpreted as fixed EST (UTC-5) without DST.",
        "2. XAU ISO timestamps with an offset are converted to UTC; naive timestamps use configured XAU_NAIVE_TIMEZONE.",
        "3. Missing XAU candles are not interpolated for coverage calculations.",
        "4. FX component values may be carried backward for at most the configured stale limit; no future value is used.",
        "5. DXY is not reweighted when one component is absent. A valid DXY point requires all six pairs.",
        "6. DXY is reconstructed from HistData Bid closes and is not the official ICE data feed.",
        "7. Raw source files are read-only; no source file is modified.",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_readme(path: Path) -> None:
    path.write_text(
        """RETAIL REACTION ANALYSIS OUTPUTS
=================================

1. event_reactions.csv
   One row per Core Retail Sales release. Contains Actual, Forecast, Previous,
   surprise, previous-value revision, event coverage, XAU BID/ASK/midpoint returns,
   DXY returns, six individual FX-pair returns, and intrawindow XAU excursions.

2. event_window_bars.csv
   Minute-level event-relative windows. `minutes_from_release` is the candle CLOSE
   time offset. Thus offset 0 is the close immediately before publication; offset +5
   is the close five minutes after publication. Each row retains the candle start UTC.

3. annual_summary.csv
   Overall statistics plus statistics by calendar year, grouped by Core surprise,
   previous-value revision sign, and headline Retail Sales surprise.

4. period_summary.csv
   Same statistics by configurable 3–4-year macro blocks.

5. analysis_report.md
   Human-readable summary and a compact comparison table for 5-minute reactions.

6. data_quality_report.txt
   Missing files, row coverage, stale FX values, event-level data quality, and excluded
   calendar entries.

Calculation details
-------------------
* XAU primary series is the midpoint of BID and ASK close values at matching timestamps.
  BID and ASK responses are also reported separately.
* Minute source timestamps are treated as candle start times. Baseline is the close at t=0, i.e. the candle that ends immediately at release time.
* Response horizons are close-to-close at +1, +5, +15, +30, +60 and +120 minutes.
* For a missing horizon candle, a previous close can be used only up to 2 minutes back;
  no forward-looking fill is used. The used offset is written to the event CSV.
* Revision estimate = current release `Previous` minus the `Actual` saved in the prior event row,
  only when release dates are 20–45 days apart. It is a proxy based on the calendar history.
* DXY = 50.14348112 * EURUSD^-0.576 * USDJPY^0.136 * GBPUSD^-0.119 *
  USDCAD^0.091 * USDSEK^0.042 * USDCHF^0.036. HistData bars are bid bars.
* HistData timestamps are fixed EST (UTC-5) with no DST adjustment. XAU timestamps
  are treated as UTC when an explicit +00:00 offset is present.
* DXY is calculated only when all six pair values are available (an earlier value may
  be carried backward for at most 2 minutes). The formula is not dynamically reweighted.
* The current config analyzes dates through 2025-12-31 and excludes 2026 until its FX
  minute files are added.

Run py retail_reaction_analyzer.py after adjusting the paths in the configuration.
No database or third-party Python packages are required.
""",
        encoding="utf-8",
    )


# =============================================================================
# 10. MAIN
# =============================================================================


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    output_dir = script_dir / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[1/6] Reading Core Retail Sales event calendar...")
    try:
        events, excluded = load_events(CALENDAR_EVENTS_CSV)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        print("Проверь CALENDAR_EVENTS_CSV в начале скрипта.")
        return 1

    if not events:
        print("[ERROR] В заданном диапазоне нет событий. Проверь календарь и даты.")
        return 1
    print(f"[OK] Events in range: {len(events)}")

    print("[2/6] Loading XAUUSD BID/ASK M1 windows...")
    xau_data, xau_stats = load_xau(events)
    print(
        f"[OK] XAU BID bars: {xau_stats['counts'].get('BID_bars_loaded', 0)}; "
        f"ASK bars: {xau_stats['counts'].get('ASK_bars_loaded', 0)}"
    )

    # DXY analysis: False = полностью пропустить загрузку HistData.
    ENABLE_DXY_ANALYSIS = False

    if ENABLE_DXY_ANALYSIS:
        print("[3/6] Loading six HistData FX M1 components...")
        fx_data, fx_stats = load_fx(events)

        for pair in FX_PAIRS:
            print(f"  {pair}: {fx_stats['counts'].get(pair + '_bars_loaded', 0)} relevant bars")
    else:
        print("[3/6] DXY analysis disabled. Skipping HistData loading...")
        fx_data = {pair: {} for pair in FX_PAIRS}
        fx_stats = {
            "counts": {},
            "file_status": {},
            "required_year_count": 0
        }

    print("[4/6] Building minute windows and event-level reactions...")
    all_window_rows: List[Dict[str, str]] = []
    event_summary_rows: List[Dict[str, str]] = []
    for index, event in enumerate(events, start=1):
        window = event_window_rows(event, xau_data, fx_data)
        all_window_rows.extend(window)
        event_summary_rows.append(build_event_summary(event, window))
        if index % 25 == 0 or index == len(events):
            print(f"  Processed {index}/{len(events)} events")

    print("[5/6] Calculating annual and multi-year statistics...")
    annual_rows, period_rows = make_summaries(event_summary_rows)

    print("[6/6] Writing reports...")
    write_csv(output_dir / EVENT_OUTPUT_NAME, event_summary_rows)
    write_csv(output_dir / WINDOW_OUTPUT_NAME, all_window_rows)
    write_csv(output_dir / YEAR_OUTPUT_NAME, annual_rows)
    write_csv(output_dir / PERIOD_OUTPUT_NAME, period_rows)
    write_quality_report(output_dir / QA_OUTPUT_NAME, events, event_summary_rows, xau_stats, fx_stats, excluded)
    create_analysis_report(output_dir / REPORT_OUTPUT_NAME, events, event_summary_rows, annual_rows, period_rows, xau_stats, fx_stats, excluded)
    write_readme(output_dir / "README.txt")

    xau_ok = sum(row["xau_mid_quality"] == "OK" for row in event_summary_rows)
    dxy_ok = sum(row["dxy_quality"] == "OK" for row in event_summary_rows)
    print("\nDONE")
    print(f"Events processed: {len(events)}")
    print(f"XAU midpoint quality OK: {xau_ok}/{len(events)}")
    print(f"DXY quality OK: {dxy_ok}/{len(events)}")
    print(f"Output folder: {output_dir}")
    print("Open analysis_report.md first; then inspect event_reactions.csv and data_quality_report.txt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
