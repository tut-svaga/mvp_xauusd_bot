#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage C corrected: historical XAUUSD reactions for eight US reports.

For these eight US macro releases, event time is reconstructed as 08:30
America/New_York on the calendar date in the source CSV. This avoids treating
midnight placeholders (00:00) as real release times. DST is applied by ZoneInfo.

Requirements:
    pip install pandas

Expected quote layout:
    <quotes_root>/BID/YYYY/MM/XAUUSD_M1_BID_YYYY-MM-DD.csv
    <quotes_root>/ASK/YYYY/MM/XAUUSD_M1_ASK_YYYY-MM-DD.csv

Dukascopy M1 timestamps are candle START times; the script uses the candle
close at timestamp + 1 minute. Baseline is the last completed close at or before
the release time; horizons are the last completed closes at or before +5/+15m.
Prices are analysed using the BID/ASK midpoint.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd


# ---------------------------------------------------------------------------
# Default paths. They can be overridden through command-line arguments.
# ---------------------------------------------------------------------------
DEFAULT_CALENDAR_ROOT = Path(
    r"E:\work\XAU_gold\mvp_tg_bot\mvp_retail\data\economic_data"
)
DEFAULT_QUOTES_ROOT = Path(r"E:\work\XAU_gold\XAUUSD\raw")
DEFAULT_OUTPUT_ROOT = Path(
    r"E:\work\XAU_gold\mvp_tg_bot\mvp_retail\output_stage_c_corrected"
)

REPORTS = [
    {
        "name": "Core PCE Price Index m/m",
        "slug": "core_pce_price_index_m_m",
        "relative_path": Path("inflation") / "core_pce_price_index_m_m.csv",
    },
    {
        "name": "Prelim GDP q/q",
        "slug": "prelim_gdp_q_q",
        "relative_path": Path("growth") / "prelim_gdp_q_q.csv",
    },
    {
        "name": "Prelim GDP Price Index q/q",
        "slug": "prelim_gdp_price_index_q_q",
        "relative_path": Path("inflation") / "prelim_gdp_price_index_q_q.csv",
    },
    {
        "name": "Core Durable Goods Orders m/m",
        "slug": "core_durable_goods_orders_m_m",
        "relative_path": Path("growth") / "core_durable_goods_orders_m_m.csv",
    },
    {
        "name": "Durable Goods Orders m/m",
        "slug": "durable_goods_orders_m_m",
        "relative_path": Path("growth") / "durable_goods_orders_m_m.csv",
    },
    {
        "name": "Personal Income m/m",
        "slug": "personal_income_m_m",
        "relative_path": Path("consumer") / "personal_income_m_m.csv",
    },
    {
        "name": "Personal Spending m/m",
        "slug": "personal_spending_m_m",
        "relative_path": Path("consumer") / "personal_spending_m_m.csv",
    },
    {
        "name": "Unemployment Claims",
        "slug": "unemployment_claims",
        "relative_path": Path("employment") / "unemployment_claims.csv",
    },
]

UTC = timezone.utc
GROUPS = ("below_forecast", "inline", "above_forecast")


@dataclass
class EventRecord:
    report_name: str
    report_slug: str
    source_file: str
    source_row: int
    raw_datetime: str
    timestamp_source: str
    event_time_utc: pd.Timestamp
    actual: str
    forecast: str
    previous: str
    actual_num: Optional[float]
    forecast_num: Optional[float]
    result_group: Optional[str]


class DataProblem(Exception):
    """Raised for an input file that cannot be interpreted safely."""


def norm_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def find_col(columns, aliases) -> Optional[str]:
    lookup = {norm_name(c): c for c in columns}
    for alias in aliases:
        if norm_name(alias) in lookup:
            return lookup[norm_name(alias)]
    return None


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return not text or text.lower() in {"nan", "none", "nat", "null"}


def parse_datetime_value(value: Any, naive_timezone: str, assume_utc: bool = False) -> pd.Timestamp:
    """Parse a timestamp and return a UTC-aware pandas Timestamp."""
    if is_blank(value):
        raise ValueError("empty timestamp")

    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise ValueError(f"invalid timestamp: {value!r}")

    if ts.tzinfo is None:
        if assume_utc:
            ts = ts.tz_localize("UTC")
        else:
            try:
                ZoneInfo(naive_timezone)
            except ZoneInfoNotFoundError as exc:
                raise DataProblem(
                    f"Unknown timezone {naive_timezone!r}. Install/update tzdata or choose a valid IANA timezone."
                ) from exc
            ts = ts.tz_localize(
                naive_timezone,
                ambiguous="NaT",
                nonexistent="shift_forward",
            )
            if pd.isna(ts):
                raise ValueError(
                    f"ambiguous local timestamp {value!r} in {naive_timezone}; provide an explicit UTC offset"
                )
    return ts.tz_convert("UTC")


def parse_event_time(
    row: pd.Series,
    cols: dict,
    naive_timezone: str,
    release_timezone: str,
    release_time: str,
):
    """Reconstruct the scheduled release timestamp from the calendar DATE.

    Do not trust the time component in DateTime: the source has many rows with
    00:00 placeholders, which the previous analyzer incorrectly interpreted as
    real event times. The eight configured US releases are scheduled for
    08:30 New York time. We preserve the source calendar date and apply the
    correct DST-aware New York offset.
    """
    raw_value = ""
    source_label = ""
    event_date = None

    # Prefer DateTime's written calendar date, without converting its offset.
    dt_col = cols.get("datetime")
    if dt_col and not is_blank(row.get(dt_col)):
        raw_value = str(row[dt_col]).strip()
        try:
            event_date = pd.Timestamp(raw_value).date()
            source_label = "DateTime_calendar_date"
        except Exception:
            event_date = None

    # Some exports provide a separate local datetime or date field.
    if event_date is None:
        local_col = cols.get("datetime_local")
        if local_col and not is_blank(row.get(local_col)):
            raw_value = str(row[local_col]).strip()
            try:
                event_date = pd.Timestamp(raw_value).date()
                source_label = "datetime_local_calendar_date"
            except Exception:
                event_date = None

    if event_date is None:
        date_col = cols.get("date")
        if date_col and not is_blank(row.get(date_col)):
            raw_value = str(row[date_col]).strip()
            try:
                event_date = pd.Timestamp(raw_value).date()
                source_label = "date_column"
            except Exception:
                event_date = None

    # Last resort: derive the New York calendar date from explicit UTC.
    if event_date is None:
        utc_col = cols.get("datetime_utc")
        if utc_col and not is_blank(row.get(utc_col)):
            raw_value = str(row[utc_col]).strip()
            ts = parse_datetime_value(raw_value, naive_timezone, assume_utc=True)
            event_date = ts.tz_convert(release_timezone).date()
            source_label = "datetime_utc_calendar_date"

    if event_date is None:
        raise ValueError("no usable calendar date for scheduled release time")

    try:
        release_clock = time.fromisoformat(release_time)
    except ValueError as exc:
        raise DataProblem(f"Invalid --release-time {release_time!r}; expected HH:MM") from exc

    try:
        ZoneInfo(release_timezone)
    except ZoneInfoNotFoundError as exc:
        raise DataProblem(f"Unknown release timezone {release_timezone!r}") from exc

    local_naive = pd.Timestamp(datetime.combine(event_date, release_clock))
    local_aware = local_naive.tz_localize(
        release_timezone,
        ambiguous="NaT",
        nonexistent="shift_forward",
    )
    if pd.isna(local_aware):
        raise ValueError(
            f"scheduled time {event_date} {release_time} is ambiguous in {release_timezone}"
        )
    event_utc = local_aware.tz_convert("UTC")
    return event_utc, raw_value, f"{source_label}_assumed_{release_time}_{release_timezone}"


def parse_numeric(value: Any) -> Optional[float]:
    """Parse calendar values such as 0.3%, 201K, 1.2M, or -0.1."""
    if is_blank(value):
        return None
    text = str(value).strip().replace("−", "-").replace("–", "-")
    if text.lower() in {"n/a", "na", "--", "-", "tbd", "null"}:
        return None

    # Remove thousands separators while retaining decimal points and suffixes.
    text = text.replace(",", "")
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None

    suffix_match = re.search(r"\s*([KMB])\b", text[match.end():], flags=re.IGNORECASE)
    if suffix_match:
        suffix = suffix_match.group(1).upper()
        number *= {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0}[suffix]
    if not math.isfinite(number):
        return None
    return number


def classify_result(actual: Optional[float], forecast: Optional[float], tolerance: float) -> Optional[str]:
    if actual is None or forecast is None:
        return None
    difference = actual - forecast
    if difference > tolerance:
        return "above_forecast"
    if difference < -tolerance:
        return "below_forecast"
    return "inline"


def load_report_events(
    report: dict,
    calendar_root: Path,
    naive_timezone: str,
    equal_tolerance: float,
    release_timezone: str,
    release_time: str,
) -> tuple[list[EventRecord], dict]:
    path = calendar_root / report["relative_path"]
    if not path.exists():
        raise DataProblem(f"Report file not found: {path}")

    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except Exception as exc:
        raise DataProblem(f"Cannot read report CSV {path}: {exc}") from exc
    if frame.empty:
        return [], {"rows": 0, "events_loaded": 0, "skipped_bad_time": 0, "rows_missing_actual_or_forecast": 0}

    cols = {
        "datetime": find_col(frame.columns, ["DateTime", "datetime"]),
        "datetime_local": find_col(frame.columns, ["datetime_local", "local_datetime"]),
        "datetime_utc": find_col(frame.columns, ["datetime_utc", "utc_datetime"]),
        "date": find_col(frame.columns, ["date"]),
        "time": find_col(frame.columns, ["time"]),
        "currency": find_col(frame.columns, ["Currency", "currency"]),
        "actual": find_col(frame.columns, ["Actual", "actual"]),
        "forecast": find_col(frame.columns, ["Forecast", "forecast"]),
        "previous": find_col(frame.columns, ["Previous", "previous"]),
    }
    if not cols["actual"] or not cols["forecast"]:
        raise DataProblem(f"{path}: missing Actual or Forecast columns; found {list(frame.columns)}")

    records: list[EventRecord] = []
    skipped_bad_time = 0
    skipped_missing_values = 0
    skipped_non_usd = 0
    time_sources: dict[str, int] = {}

    for index, row in frame.iterrows():
        # If a Currency column exists, retain only USD rows.
        if cols["currency"] and not is_blank(row.get(cols["currency"])):
            currency = str(row[cols["currency"]]).strip().upper()
            if currency != "USD":
                skipped_non_usd += 1
                continue

        try:
            event_time, raw_datetime, time_source = parse_event_time(
                row, cols, naive_timezone, release_timezone, release_time
            )
        except Exception as exc:
            skipped_bad_time += 1
            logging.warning("%s row %d: skipping event with invalid time (%s)", path.name, index + 2, exc)
            continue

        actual_raw = str(row.get(cols["actual"], "")).strip()
        forecast_raw = str(row.get(cols["forecast"], "")).strip()
        previous_raw = str(row.get(cols["previous"], "")).strip() if cols["previous"] else ""
        actual_num = parse_numeric(actual_raw)
        forecast_num = parse_numeric(forecast_raw)
        result_group = classify_result(actual_num, forecast_num, equal_tolerance)
        if result_group is None:
            skipped_missing_values += 1

        records.append(
            EventRecord(
                report_name=report["name"],
                report_slug=report["slug"],
                source_file=str(path),
                source_row=index + 2,
                raw_datetime=raw_datetime,
                timestamp_source=time_source,
                event_time_utc=event_time,
                actual=actual_raw,
                forecast=forecast_raw,
                previous=previous_raw,
                actual_num=actual_num,
                forecast_num=forecast_num,
                result_group=result_group,
            )
        )
        time_sources[time_source] = time_sources.get(time_source, 0) + 1

    records.sort(key=lambda x: x.event_time_utc)
    meta = {
        "rows": int(len(frame)),
        "events_loaded": len(records),
        "skipped_bad_time": skipped_bad_time,
        "rows_missing_actual_or_forecast": skipped_missing_values,
        "skipped_non_usd": skipped_non_usd,
        "timestamp_sources": time_sources,
        "scheduled_release_timezone": release_timezone,
        "scheduled_release_time": release_time,
    }
    return records, meta


def parse_quote_timestamp(series: pd.Series) -> pd.Series:
    """Parse quote timestamps, supporting ISO/date strings and Unix seconds/ms."""
    values = series.astype(str).str.strip()
    numeric = pd.to_numeric(values, errors="coerce")
    numeric_share = float(numeric.notna().mean()) if len(numeric) else 0.0
    if numeric_share > 0.95:
        median_value = float(numeric.dropna().median())
        if median_value > 1e12:
            result = pd.to_datetime(numeric, unit="ms", utc=True, errors="coerce")
        elif median_value > 1e9:
            result = pd.to_datetime(numeric, unit="s", utc=True, errors="coerce")
        else:
            # Compact timestamp such as YYYYMMDDHHMM is handled as text below.
            result = pd.to_datetime(values, utc=True, errors="coerce")
    else:
        result = pd.to_datetime(values, utc=True, errors="coerce")
    return result


def detect_csv_separator(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        sample = handle.read(8192)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        return ","


def load_quote_side(path: Path, side: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    separator = detect_csv_separator(path)
    try:
        probe = pd.read_csv(path, sep=separator, header=None, nrows=2, dtype=str, encoding="utf-8-sig")
    except Exception as exc:
        raise DataProblem(f"Cannot inspect quote file {path}: {exc}") from exc
    if probe.empty:
        return pd.DataFrame(columns=["bar_close_time", f"{side.lower()}_close"])

    first_row = [norm_name(x) for x in probe.iloc[0].tolist()]
    known_headers = {
        "timestamp", "datetime", "datetimeutc", "date", "time", "gmttime",
        "opentime", "open", "high", "low", "close", "closeprice", "volume",
        "bidclose", "askclose", "price",
    }
    has_header = any(value in known_headers for value in first_row)

    try:
        if has_header:
            frame = pd.read_csv(path, sep=separator, header=0, dtype=str, encoding="utf-8-sig")
        else:
            frame = pd.read_csv(path, sep=separator, header=None, dtype=str, encoding="utf-8-sig")
    except Exception as exc:
        raise DataProblem(f"Cannot read quote file {path}: {exc}") from exc

    if frame.empty:
        return pd.DataFrame(columns=["bar_close_time", f"{side.lower()}_close"])

    if has_header:
        timestamp_col = find_col(
            frame.columns,
            ["timestamp", "datetime", "datetime_utc", "utc_time", "gmt_time", "time", "date_time", "opentime"],
        )
        date_col = find_col(frame.columns, ["date"])
        time_col = find_col(frame.columns, ["time"])
        close_col = find_col(
            frame.columns,
            ["close", "closeprice", "close_price", "bidclose", "askclose", "price"],
        )

        if date_col is not None and time_col is not None and date_col != time_col and (timestamp_col is None or timestamp_col == time_col):
            raw_time = frame[date_col].astype(str).str.strip() + " " + frame[time_col].astype(str).str.strip()
        elif timestamp_col is not None:
            raw_time = frame[timestamp_col]
        else:
            # Fall back to the first field only when it parses as a timestamp.
            raw_time = frame.iloc[:, 0]

        if close_col is None:
            # Headered OHLC data normally has Close as the fifth field.
            if frame.shape[1] >= 5:
                close_col = frame.columns[4]
            elif frame.shape[1] == 2:
                close_col = frame.columns[1]
            else:
                raise DataProblem(
                    f"Cannot identify Close column in {path}. Columns: {list(frame.columns)}"
                )
        raw_close = frame[close_col]
    else:
        # Standard headerless Dukascopy export: timestamp, open, high, low, close, volume.
        raw_time = frame.iloc[:, 0]
        if frame.shape[1] >= 5:
            raw_close = frame.iloc[:, 4]
        elif frame.shape[1] == 2:
            raw_close = frame.iloc[:, 1]
        else:
            raise DataProblem(
                f"Unsupported headerless quote structure in {path}: {frame.shape[1]} columns"
            )

    timestamps = parse_quote_timestamp(raw_time)
    close_values = pd.to_numeric(
        raw_close.astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    )
    result = pd.DataFrame({"timestamp": timestamps, f"{side.lower()}_close": close_values})
    result = result.dropna(subset=["timestamp", f"{side.lower()}_close"])
    result["bar_close_time"] = result["timestamp"] + pd.Timedelta(minutes=1)
    result = result[["bar_close_time", f"{side.lower()}_close"]]
    result = result.drop_duplicates(subset=["bar_close_time"], keep="last")
    return result.sort_values("bar_close_time").reset_index(drop=True)


class QuoteRepository:
    def __init__(self, root: Path, max_gap_minutes: float):
        self.root = root
        self.max_gap = pd.Timedelta(minutes=max_gap_minutes)
        self.cache: dict[date, pd.DataFrame] = {}
        self.missing: set[tuple[str, date]] = set()

    def _daily_path(self, side: str, day: date) -> Path:
        return (
            self.root
            / side
            / f"{day.year:04d}"
            / f"{day.month:02d}"
            / f"XAUUSD_M1_{side}_{day.isoformat()}.csv"
        )

    def load_day(self, day: date) -> pd.DataFrame:
        if day in self.cache:
            return self.cache[day]

        bid_path = self._daily_path("BID", day)
        ask_path = self._daily_path("ASK", day)
        if not bid_path.exists() or not ask_path.exists():
            absent = []
            if not bid_path.exists():
                absent.append(str(bid_path))
            if not ask_path.exists():
                absent.append(str(ask_path))
            logging.debug("Quote date %s missing: %s", day, "; ".join(absent))
            empty = pd.DataFrame(
                columns=["bar_close_time", "bid_close", "ask_close", "mid_close", "spread"]
            )
            self.cache[day] = empty
            return empty

        bid = load_quote_side(bid_path, "BID")
        ask = load_quote_side(ask_path, "ASK")
        if bid.empty or ask.empty:
            merged = pd.DataFrame(
                columns=["bar_close_time", "bid_close", "ask_close", "mid_close", "spread"]
            )
        else:
            # Exact timestamp matching avoids pairing quotes from different minutes.
            merged = pd.merge(bid, ask, on="bar_close_time", how="inner")
            merged["mid_close"] = (merged["bid_close"] + merged["ask_close"]) / 2.0
            merged["spread"] = merged["ask_close"] - merged["bid_close"]
            merged = merged.sort_values("bar_close_time").reset_index(drop=True)

        if len(bid) and len(ask) and len(merged) < min(len(bid), len(ask)) * 0.8:
            logging.warning(
                "%s: only %d of %d possible BID/ASK minute timestamps matched",
                day,
                len(merged),
                min(len(bid), len(ask)),
            )

        self.cache[day] = merged
        return merged

    def get_window(self, event_time: pd.Timestamp) -> pd.DataFrame:
        start_day = (event_time - pd.Timedelta(days=1)).date()
        end_day = (event_time + pd.Timedelta(days=1)).date()
        chunks = []
        day = start_day
        while day <= end_day:
            part = self.load_day(day)
            if not part.empty:
                chunks.append(part)
            day += timedelta(days=1)
        if not chunks:
            return pd.DataFrame(
                columns=["bar_close_time", "bid_close", "ask_close", "mid_close", "spread"]
            )
        result = pd.concat(chunks, ignore_index=True)
        result = result.drop_duplicates(subset=["bar_close_time"], keep="last")
        return result.sort_values("bar_close_time").reset_index(drop=True)

    def price_at_or_before(self, quotes: pd.DataFrame, target: pd.Timestamp) -> Optional[dict]:
        if quotes.empty:
            return None
        times = quotes["bar_close_time"]
        position = int(times.searchsorted(target, side="right") - 1)
        if position < 0:
            return None
        row = quotes.iloc[position]
        age = target - row["bar_close_time"]
        if age < pd.Timedelta(0) or age > self.max_gap:
            return None
        return {
            "bar_close_time": row["bar_close_time"],
            "bid": float(row["bid_close"]),
            "ask": float(row["ask_close"]),
            "mid": float(row["mid_close"]),
            "spread": float(row["spread"]),
            "age_seconds": float(age.total_seconds()),
        }


def direction_of(change: Optional[float]) -> Optional[str]:
    if change is None or pd.isna(change):
        return None
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def analyse_event(event: EventRecord, quotes_repo: QuoteRepository) -> dict:
    window = quotes_repo.get_window(event.event_time_utc)
    baseline = quotes_repo.price_at_or_before(window, event.event_time_utc)
    row: dict[str, Any] = {
        "report": event.report_name,
        "report_slug": event.report_slug,
        "source_file": event.source_file,
        "source_row": event.source_row,
        "raw_datetime": event.raw_datetime,
        "timestamp_source": event.timestamp_source,
        "event_time_utc": event.event_time_utc.isoformat(),
        "actual": event.actual,
        "forecast": event.forecast,
        "previous": event.previous,
        "actual_numeric": event.actual_num,
        "forecast_numeric": event.forecast_num,
        "actual_minus_forecast": (
            event.actual_num - event.forecast_num
            if event.actual_num is not None and event.forecast_num is not None
            else None
        ),
        "result_group": event.result_group,
        "baseline_quote_time_utc": None,
        "baseline_bid": None,
        "baseline_ask": None,
        "baseline_mid": None,
        "baseline_spread": None,
        "baseline_age_seconds": None,
    }

    if baseline:
        row.update(
            {
                "baseline_quote_time_utc": baseline["bar_close_time"].isoformat(),
                "baseline_bid": baseline["bid"],
                "baseline_ask": baseline["ask"],
                "baseline_mid": baseline["mid"],
                "baseline_spread": baseline["spread"],
                "baseline_age_seconds": baseline["age_seconds"],
            }
        )

    for horizon in (5, 15):
        target_time = event.event_time_utc + pd.Timedelta(minutes=horizon)
        target = quotes_repo.price_at_or_before(window, target_time)
        prefix = f"m{horizon}"
        if target and baseline:
            change = target["mid"] - baseline["mid"]
            row.update(
                {
                    f"{prefix}_quote_time_utc": target["bar_close_time"].isoformat(),
                    f"{prefix}_bid": target["bid"],
                    f"{prefix}_ask": target["ask"],
                    f"{prefix}_mid": target["mid"],
                    f"{prefix}_spread": target["spread"],
                    f"{prefix}_age_seconds": target["age_seconds"],
                    f"{prefix}_change_usd": change,
                    f"{prefix}_abs_change_usd": abs(change),
                    f"{prefix}_direction": direction_of(change),
                }
            )
        else:
            row.update(
                {
                    f"{prefix}_quote_time_utc": None,
                    f"{prefix}_bid": None,
                    f"{prefix}_ask": None,
                    f"{prefix}_mid": None,
                    f"{prefix}_spread": None,
                    f"{prefix}_age_seconds": None,
                    f"{prefix}_change_usd": None,
                    f"{prefix}_abs_change_usd": None,
                    f"{prefix}_direction": None,
                }
            )
    return row


def safe_float(value) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def summarize_group(events_frame: pd.DataFrame, report_name: str, group: str, horizon: int) -> dict:
    change_col = f"m{horizon}_change_usd"
    subset = events_frame[events_frame["result_group"] == group] if not events_frame.empty else pd.DataFrame()
    if subset.empty or change_col not in subset.columns:
        changes = pd.Series(dtype=float)
    else:
        changes = pd.to_numeric(subset[change_col], errors="coerce").dropna()

    n = int(len(changes))
    if n:
        ups = int((changes > 0).sum())
        downs = int((changes < 0).sum())
        flats = int((changes == 0).sum())
        mean_change = float(changes.mean())
        median_change = float(changes.median())
        mean_abs = float(changes.abs().mean())
        q25 = float(changes.quantile(0.25))
        q75 = float(changes.quantile(0.75))
        p_up = 100.0 * ups / n
        p_down = 100.0 * downs / n
        p_flat = 100.0 * flats / n
    else:
        ups = downs = flats = 0
        mean_change = median_change = mean_abs = q25 = q75 = None
        p_up = p_down = p_flat = None

    group_events = int((events_frame["result_group"] == group).sum()) if not events_frame.empty else 0
    return {
        "report": report_name,
        "result_group": group,
        "horizon_minutes": horizon,
        "events_in_group": group_events,
        "events_with_quote_result": n,
        "events_without_quote_result": max(group_events - n, 0),
        "up_count": ups,
        "down_count": downs,
        "flat_count": flats,
        "p_up_pct": p_up,
        "p_down_pct": p_down,
        "p_flat_pct": p_flat,
        "mean_change_usd": mean_change,
        "median_change_usd": median_change,
        "mean_abs_change_usd": mean_abs,
        "q25_change_usd": q25,
        "q75_change_usd": q75,
    }


def build_template(
    report: dict,
    events_frame: pd.DataFrame,
    stats_frame: pd.DataFrame,
    metadata: dict,
    equal_tolerance: float,
) -> dict:
    groups: dict[str, Any] = {}
    for group in GROUPS:
        group_events = events_frame[events_frame["result_group"] == group] if not events_frame.empty else pd.DataFrame()
        horizons = {}
        for horizon in (5, 15):
            stat_rows = stats_frame[
                (stats_frame["result_group"] == group)
                & (stats_frame["horizon_minutes"] == horizon)
            ]
            horizons[str(horizon)] = stat_rows.iloc[0].to_dict() if not stat_rows.empty else {}
        groups[group] = {
            "events_in_group": int(len(group_events)),
            "horizons": horizons,
        }

    return {
        "schema_version": "1.0",
        "report": report["name"],
        "slug": report["slug"],
        "source_csv": str(report["relative_path"]),
        "method": {
            "classification": "Compare numeric Actual with numeric Forecast; above/below/inline.",
            "inline_tolerance": equal_tolerance,
            "price": "(BID close + ASK close) / 2",
            "baseline": "Last completed M1 candle close at or before event timestamp.",
            "horizons": "Last completed M1 candle close at or before event timestamp + N minutes.",
            "direction": "up if midpoint change > 0; down if < 0; flat if == 0.",
            "price_unit": "USD per troy ounce, as represented by XAUUSD quotes.",
        },
        "data_quality": metadata,
        "groups": groups,
    }


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def write_json(path: Path, payload: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(json_safe(payload), handle, ensure_ascii=False, indent=2, allow_nan=False)


def run(args) -> int:
    calendar_root = Path(args.calendar_root)
    quotes_root = Path(args.quotes_root)
    output_root = Path(args.output_dir)

    if not calendar_root.exists():
        raise DataProblem(
            f"Calendar directory does not exist: {calendar_root}\n"
            "Pass the actual directory containing inflation/, growth/, consumer/, and employment/."
        )
    if not quotes_root.exists():
        raise DataProblem(f"Quotes directory does not exist: {quotes_root}")

    output_reports = output_root / "reports"
    output_stats = output_root / "statistics"
    output_templates = output_root / "templates"
    for folder in (output_reports, output_stats, output_templates):
        folder.mkdir(parents=True, exist_ok=True)

    quote_repo = QuoteRepository(quotes_root, args.max_gap_minutes)
    all_stats = []
    all_templates = {}
    run_report_meta = {}

    logging.info("Calendar root: %s", calendar_root)
    logging.info("Quotes root:   %s", quotes_root)
    logging.info("Output root:   %s", output_root)
    logging.info("Scheduled release time: %s %s", args.release_time, args.release_timezone)
    logging.info("M1 maximum quote staleness: %.2f minutes", args.max_gap_minutes)

    for report in REPORTS:
        logging.info("\n=== %s ===", report["name"])
        events, input_meta = load_report_events(
            report,
            calendar_root,
            args.naive_event_timezone,
            args.equal_tolerance,
            args.release_timezone,
            args.release_time,
        )
        if not events:
            logging.warning("No usable historical events found for %s", report["name"])
            events_frame = pd.DataFrame()
            stats_rows = [
                summarize_group(events_frame, report["name"], group, horizon)
                for group in GROUPS
                for horizon in (5, 15)
            ]
            stats_frame = pd.DataFrame(stats_rows)
            metadata = {**input_meta, "events_with_quotes": 0}
        else:
            event_rows = []
            for i, event in enumerate(events, start=1):
                result = analyse_event(event, quote_repo)
                event_rows.append(result)
                if i % 100 == 0:
                    logging.info("  processed %d/%d events", i, len(events))
            events_frame = pd.DataFrame(event_rows)
            stats_rows = [
                summarize_group(events_frame, report["name"], group, horizon)
                for group in GROUPS
                for horizon in (5, 15)
            ]
            stats_frame = pd.DataFrame(stats_rows)
            metadata = {
                **input_meta,
                "events_with_quotes_m5": int(events_frame["m5_change_usd"].notna().sum()),
                "events_with_quotes_m15": int(events_frame["m15_change_usd"].notna().sum()),
                "events_with_baseline": int(events_frame["baseline_mid"].notna().sum()),
            }

        # Per-report outputs.
        events_path = output_reports / f"{report['slug']}_events.csv"
        stats_path = output_stats / f"{report['slug']}_group_stats.csv"
        events_frame.to_csv(events_path, index=False, encoding="utf-8-sig")
        stats_frame.to_csv(stats_path, index=False, encoding="utf-8-sig")

        template = build_template(report, events_frame, stats_frame, metadata, args.equal_tolerance)
        template_path = output_templates / f"{report['slug']}.json"
        write_json(template_path, template)
        all_templates[report["slug"]] = template
        all_stats.append(stats_frame)
        run_report_meta[report["slug"]] = {
            "input": str(calendar_root / report["relative_path"]),
            "metadata": metadata,
            "events_csv": str(events_path),
            "group_stats_csv": str(stats_path),
            "template_json": str(template_path),
        }

        logging.info(
            "Events: %d | groups: below=%d, inline=%d, above=%d | quotes M5=%s, M15=%s",
            len(events),
            int((events_frame["result_group"] == "below_forecast").sum()) if not events_frame.empty else 0,
            int((events_frame["result_group"] == "inline").sum()) if not events_frame.empty else 0,
            int((events_frame["result_group"] == "above_forecast").sum()) if not events_frame.empty else 0,
            metadata.get("events_with_quotes_m5", 0),
            metadata.get("events_with_quotes_m15", 0),
        )

    if all_stats:
        combined_stats = pd.concat(all_stats, ignore_index=True)
    else:
        combined_stats = pd.DataFrame()
    combined_stats_path = output_root / "stage_c_all_group_stats.csv"
    combined_stats.to_csv(combined_stats_path, index=False, encoding="utf-8-sig")

    templates_path = output_root / "stage_c_templates.json"
    write_json(
        templates_path,
        {
            "schema_version": "1.0",
            "stage": "C",
            "description": "Independent historical XAUUSD reaction templates; no cross-report weights applied.",
            "reports": all_templates,
        },
    )

    manifest = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "calendar_root": str(calendar_root),
        "quotes_root": str(quotes_root),
        "output_root": str(output_root),
        "settings": {
            "naive_event_timezone": args.naive_event_timezone,
            "event_time_rule": "calendar date + scheduled release time; time component in source DateTime is ignored",
            "release_timezone": args.release_timezone,
            "release_time": args.release_time,
            "quote_timezone": "UTC",
            "quote_bar_timestamp_semantics": "M1 candle start; close_time = timestamp + 1 minute",
            "max_quote_gap_minutes": args.max_gap_minutes,
            "actual_forecast_inline_tolerance": args.equal_tolerance,
            "price_calculation": "mid=(BID close + ASK close)/2",
            "horizons_minutes": [5, 15],
        },
        "reports": run_report_meta,
        "quote_days_cached": len(quote_repo.cache),
        "quote_days_with_bid_ask_data": sum(1 for df in quote_repo.cache.values() if not df.empty),
        "outputs": {
            "all_group_stats_csv": str(combined_stats_path),
            "all_templates_json": str(templates_path),
        },
    }
    manifest_path = output_root / "stage_c_run_manifest.json"
    write_json(manifest_path, manifest)

    logging.info("\nFinished.")
    logging.info("Combined statistics: %s", combined_stats_path)
    logging.info("All templates:       %s", templates_path)
    logging.info("Run manifest:        %s", manifest_path)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Calculate separate historical XAUUSD reactions for eight USD economic reports."
    )
    parser.add_argument("--calendar-root", default=str(DEFAULT_CALENDAR_ROOT), help="Folder containing inflation/, growth/, consumer/, employment/.")
    parser.add_argument("--quotes-root", default=str(DEFAULT_QUOTES_ROOT), help="Root containing BID/YYYY/MM and ASK/YYYY/MM.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT), help="Output folder for CSV and JSON results.")
    parser.add_argument(
        "--naive-event-timezone",
        default="America/New_York",
        help="Fallback timezone for legacy parsing paths.",
    )
    parser.add_argument(
        "--release-timezone",
        default="America/New_York",
        help="Timezone used to reconstruct the scheduled release time. Default: America/New_York.",
    )
    parser.add_argument(
        "--release-time",
        default="08:30",
        help="Scheduled local release time for the eight selected reports. Default: 08:30.",
    )
    parser.add_argument(
        "--max-gap-minutes",
        type=float,
        default=2.0,
        help="Reject a quote if its close timestamp is older than this many minutes relative to target time.",
    )
    parser.add_argument(
        "--equal-tolerance",
        type=float,
        default=0.0,
        help="Absolute numeric tolerance for classifying Actual ~= Forecast. Default 0 means exact equality.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        return run(args)
    except DataProblem as exc:
        logging.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        logging.warning("Interrupted by user.")
        return 130
    except Exception:
        logging.exception("Unexpected failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())
