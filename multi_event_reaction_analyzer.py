#!/usr/bin/env python3
"""Analyze each economic report separately against historical XAUUSD BID/ASK M1.

The only historical event source is data/economic_data/**/*.csv (one file per
report). Forex Factory cache files are deliberately NOT read by this script.
The live Java collector may use the current calendar cache to learn future
release times; that is a separate job.

For every report, this script builds seven surprise classes:
  beat_small, beat_medium, beat_large, inline,
  miss_small, miss_medium, miss_large.

Size boundaries are report-specific q33/q67 quantiles of |Actual-Forecast|,
calculated separately for beat and miss observations across the report's full
available history. Probability tables emphasize 2023-2026 and also retain
all-history / older-period context.

M1, M5 and M15 candles are analyzed. M5/M15 candles are aggregated from M1
bars for the event windows. Cumulative return from the pre-release baseline is
reported separately from the latest candle body return. At the same elapsed
minute, cumulative close-return is mathematically the same across frames when
all bars are complete; the candle-body/range/wick metrics carry the additional
M5/M15 information.

Standard library only; no pandas required.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc
HORIZONS = {
    "M1": (1, 5, 15, 30, 60, 120),
    "M5": (5, 15, 30, 60, 120),
    "M15": (15, 30, 60, 120),
}
PERIODS = {
    "P1_2007_2012": (2007, 2012),
    "P2_2013_2019": (2013, 2019),
    "P3_2020_2022": (2020, 2022),
    "P4_2023_2026": (2023, 2026),
}
SCENARIOS = (
    "beat_small", "beat_medium", "beat_large", "inline",
    "miss_small", "miss_medium", "miss_large",
)
EXCLUDED_FILENAMES = {
    "event_catalog.csv", "skipped_events.csv", "event_reactions.csv",
    "group_stats.csv", "run_summary.csv", "run_summary.json", "forex_factory_cache.csv",
}


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def cell(row: dict[str, Any], *names: str) -> str:
    # First check exact key spellings; then case-insensitively.
    for name in names:
        if clean(row.get(name)):
            return clean(row.get(name))
    lowered = {str(k).strip().casefold(): v for k, v in row.items() if k is not None}
    for name in names:
        if clean(lowered.get(name.casefold())):
            return clean(lowered.get(name.casefold()))
    return ""


def parse_datetime_value(value: str) -> datetime | None:
    value = clean(value)
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_release_time(row: dict[str, Any], naive_timezone: str, allow_midnight: bool) -> tuple[datetime | None, str]:
    # Individual economic_data files are the event-history source. Prefer the
    # explicit UTC field, then local timestamp with offset, then legacy DateTime.
    candidates = [
        ("datetime_utc", cell(row, "datetime_utc")),
        ("datetime_local", cell(row, "datetime_local")),
        ("DateTime", cell(row, "DateTime")),
    ]
    date_part, time_part = cell(row, "date"), cell(row, "time")
    if date_part and time_part:
        candidates.append(("date+time", f"{date_part}T{time_part}"))
    last_reason = "release_time_missing_or_unparseable"
    for source, raw in candidates:
        if not raw:
            continue
        parsed = parse_datetime_value(raw)
        if parsed is None:
            last_reason = f"unparseable_{source}: {raw}"
            continue
        if not allow_midnight and parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
            last_reason = f"midnight_time_ambiguous: {raw}"
            continue
        if parsed.tzinfo is None:
            if naive_timezone.casefold() == "skip":
                last_reason = f"timezone_missing: {raw}"
                continue
            try:
                from zoneinfo import ZoneInfo
                parsed = parsed.replace(tzinfo=ZoneInfo(naive_timezone))
            except Exception:
                last_reason = f"invalid_naive_timezone_{naive_timezone}: {raw}"
                continue
        return parsed.astimezone(UTC), ""
    return None, last_reason


def parse_numeric(value: str) -> float | None:
    """Parse numeric calendar values such as 0.3%, 1.2M, 125K, or (0.4%)."""
    s = clean(value).replace("−", "-").replace("–", "-")
    if not s or s.casefold() in {"-", "—", "n/a", "na", "none", "null", "tentative", "tba"}:
        return None
    negative_parentheses = s.startswith("(") and s.endswith(")")
    s = s.strip("() ").replace(",", "")
    match = re.search(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([KMBT])?", s, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    number *= {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[(match.group(2) or "").upper()]
    if negative_parentheses:
        number = -abs(number)
    return number if math.isfinite(number) else None


def normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean(value).casefold()).strip()


def report_id_for(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("").as_posix()
    return rel.casefold()


@dataclass
class Event:
    report_id: str
    report_name: str
    relative_file: str
    currency: str
    impact: str
    scheduled_utc: datetime
    actual: str = ""
    forecast: str = ""
    previous: str = ""
    detail: str = ""
    actual_num: float | None = None
    forecast_num: float | None = None
    previous_num: float | None = None
    surprise: float | None = None
    surprise_sign: str = "unknown"
    size_level: str = "unknown"
    previous_revision: float | None = None
    previous_revision_sign: str = "unknown"
    event_id: str = ""
    is_isolated: bool = True
    co_release_count: int = 1

    def __post_init__(self) -> None:
        if self.actual_num is None:
            self.actual_num = parse_numeric(self.actual)
        if self.forecast_num is None:
            self.forecast_num = parse_numeric(self.forecast)
        if self.previous_num is None:
            self.previous_num = parse_numeric(self.previous)
        if self.actual_num is not None and self.forecast_num is not None:
            self.surprise = self.actual_num - self.forecast_num
            if self.surprise > 1e-12:
                self.surprise_sign = "beat"
            elif self.surprise < -1e-12:
                self.surprise_sign = "miss"
            else:
                self.surprise_sign = "inline"
        event_key = "|".join((self.report_id, self.currency, self.scheduled_utc.isoformat(), self.actual, self.forecast))
        self.event_id = hashlib.sha1(event_key.encode("utf-8")).hexdigest()[:16]


def iter_csv_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return
        for row in reader:
            if row:
                yield {str(k): ("" if v is None else v) for k, v in row.items() if k is not None}


def load_events(economic_root: Path, naive_timezone: str, allow_midnight: bool) -> tuple[list[Event], list[dict[str, str]], dict[str, int]]:
    if not economic_root.is_dir():
        raise FileNotFoundError(f"economic_data folder not found: {economic_root}")
    events: list[Event] = []
    skipped: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    seen_by_report: set[tuple[str, str, str, str, str]] = set()
    files = sorted(p for p in economic_root.rglob("*.csv") if p.name.casefold() not in EXCLUDED_FILENAMES)
    counts["report_files_found"] = len(files)
    for path in files:
        rid = report_id_for(path, economic_root)
        fallback_name = path.stem.replace("_", " ")
        file_events = 0
        try:
            for row in iter_csv_rows(path):
                counts["rows_seen"] += 1
                event_name = cell(row, "Event", "event", "event_name") or fallback_name
                scheduled, reason = parse_release_time(row, naive_timezone, allow_midnight)
                if scheduled is None:
                    skipped.append({"report_id": rid, "source_file": str(path), "event": event_name, "reason": reason})
                    continue
                currency = cell(row, "Currency", "currency").upper()
                actual = cell(row, "Actual", "actual")
                forecast = cell(row, "Forecast", "forecast")
                previous = cell(row, "Previous", "previous")
                dedup_key = (rid, scheduled.isoformat(), currency, actual, forecast)
                if dedup_key in seen_by_report:
                    counts["duplicate_rows_removed"] += 1
                    continue
                seen_by_report.add(dedup_key)
                events.append(Event(
                    report_id=rid,
                    report_name=event_name,
                    relative_file=path.relative_to(economic_root).as_posix(),
                    currency=currency,
                    impact=cell(row, "Impact", "impact"),
                    scheduled_utc=scheduled,
                    actual=actual,
                    forecast=forecast,
                    previous=previous,
                    detail=cell(row, "Detail", "detail"),
                ))
                file_events += 1
        except (OSError, csv.Error, UnicodeError) as exc:
            skipped.append({"report_id": rid, "source_file": str(path), "event": fallback_name, "reason": f"read_error: {exc}"})
        if file_events:
            counts["reports_with_events"] += 1
        else:
            counts["empty_or_unreadable_report_files"] += 1
    counts["events_loaded"] = len(events)
    counts["rows_skipped"] = len(skipped)
    return sorted(events, key=lambda e: (e.scheduled_utc, e.report_id)), skipped, dict(counts)


class DailyPriceCache:
    """Small date-LRU cache for daily M1 BID or ASK files."""
    def __init__(self, root: Path, side: str, max_days: int = 8) -> None:
        self.root, self.side, self.max_days = root, side, max_days
        self.cache: OrderedDict[date, dict[datetime, dict[str, float]]] = OrderedDict()
        self.missing: set[date] = set()
        self.files_loaded = 0
        self.rows_loaded = 0

    def _path(self, day: date) -> Path:
        return self.root / f"{day.year:04d}" / f"{day.month:02d}" / f"XAUUSD_M1_{self.side}_{day.isoformat()}.csv"

    def _load(self, day: date) -> dict[datetime, dict[str, float]]:
        if day in self.cache:
            self.cache.move_to_end(day)
            return self.cache[day]
        if day in self.missing:
            return {}
        path = self._path(day)
        data: dict[datetime, dict[str, float]] = {}
        if not path.is_file():
            self.missing.add(day)
            return data
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    stamp = parse_datetime_value(cell(row, "timestamp", "DateTime", "datetime", "time"))
                    if stamp is None:
                        continue
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=UTC)
                    stamp = stamp.astimezone(UTC).replace(second=0, microsecond=0)
                    try:
                        vals = {k: float(cell(row, k)) for k in ("open", "high", "low", "close")}
                        volume = float(cell(row, "volume")) if cell(row, "volume") else 0.0
                    except (TypeError, ValueError):
                        continue
                    if not all(math.isfinite(v) for v in vals.values()):
                        continue
                    vals["volume"] = volume if math.isfinite(volume) else 0.0
                    data[stamp] = vals
        except (OSError, csv.Error, UnicodeError) as exc:
            print(f"WARNING: cannot read {path}: {exc}", file=sys.stderr)
        self.files_loaded += 1
        self.rows_loaded += len(data)
        self.cache[day] = data
        while len(self.cache) > self.max_days:
            self.cache.popitem(last=False)
        return data

    def get(self, stamp: datetime) -> dict[str, float] | None:
        stamp = stamp.astimezone(UTC).replace(second=0, microsecond=0)
        return self._load(stamp.date()).get(stamp)


def pct_change(end: float | None, base: float | None) -> float | None:
    if end is None or base is None or base == 0:
        return None
    return (end / base - 1.0) * 100.0


def aggregate_side(bars: list[dict[str, float]]) -> dict[str, float] | None:
    if not bars:
        return None
    return {
        "open": bars[0]["open"],
        "high": max(b["high"] for b in bars),
        "low": min(b["low"] for b in bars),
        "close": bars[-1]["close"],
        "volume": sum(b.get("volume", 0.0) for b in bars),
    }


def midpoint_ohlc(bid: dict[str, float], ask: dict[str, float]) -> dict[str, float]:
    return {k: (bid[k] + ask[k]) / 2.0 for k in ("open", "high", "low", "close")}


def metric_values(event: Event, bid_cache: DailyPriceCache, ask_cache: DailyPriceCache) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    t0 = event.scheduled_utc.replace(second=0, microsecond=0)
    baseline_bid = bid_cache.get(t0 - timedelta(minutes=1))
    baseline_ask = ask_cache.get(t0 - timedelta(minutes=1))
    base_mid = None
    if baseline_bid is not None and baseline_ask is not None:
        base_mid = (baseline_bid["close"] + baseline_ask["close"]) / 2.0

    result: dict[str, Any] = {
        "event_id": event.event_id,
        "report_id": event.report_id,
        "report": event.report_name,
        "source_file": event.relative_file,
        "currency": event.currency,
        "impact": event.impact,
        "release_date": t0.date().isoformat(),
        "year": t0.year,
        "scheduled_datetime_utc": t0.isoformat(),
        "actual": event.actual,
        "forecast": event.forecast,
        "previous": event.previous,
        "actual_numeric": event.actual_num,
        "forecast_numeric": event.forecast_num,
        "previous_numeric": event.previous_num,
        "previous_value_revision": event.previous_revision,
        "previous_revision_sign": event.previous_revision_sign,
        "surprise_actual_minus_forecast": event.surprise,
        "surprise_sign": event.surprise_sign,
        "size_level": event.size_level,
        "baseline_bid_close": None if baseline_bid is None else baseline_bid["close"],
        "baseline_ask_close": None if baseline_ask is None else baseline_ask["close"],
        "baseline_mid_close": base_mid,
        "co_release_count": event.co_release_count,
        "is_isolated_release": event.is_isolated,
    }
    candle_rows: list[dict[str, Any]] = []

    for timeframe, horizons in HORIZONS.items():
        tf_minutes = int(timeframe[1:])
        aligned = (t0.minute % tf_minutes == 0 and t0.second == 0)
        result[f"{timeframe}_aligned"] = aligned
        for horizon in horizons:
            prefix = f"{timeframe}_{horizon}"
            for metric in ("cum_return_pct", "body_return_pct", "range_pct", "upper_wick_pct", "lower_wick_pct"):
                result[f"{prefix}_{metric}"] = None
            if not aligned or base_mid is None:
                continue
            end_minute = t0 + timedelta(minutes=horizon - 1)
            start_minute = end_minute - timedelta(minutes=tf_minutes - 1)
            bid_bars: list[dict[str, float]] = []
            ask_bars: list[dict[str, float]] = []
            complete = True
            for offset in range(tf_minutes):
                stamp = start_minute + timedelta(minutes=offset)
                b, a = bid_cache.get(stamp), ask_cache.get(stamp)
                if b is None or a is None:
                    complete = False
                    break
                bid_bars.append(b)
                ask_bars.append(a)
            if not complete or len(bid_bars) != tf_minutes:
                continue
            b_agg, a_agg = aggregate_side(bid_bars), aggregate_side(ask_bars)
            if b_agg is None or a_agg is None:
                continue
            mid = midpoint_ohlc(b_agg, a_agg)
            cum = pct_change(mid["close"], base_mid)
            body = pct_change(mid["close"], mid["open"])
            candle_range = ((mid["high"] - mid["low"]) / mid["open"] * 100.0) if mid["open"] else None
            upper = ((mid["high"] - max(mid["open"], mid["close"])) / mid["open"] * 100.0) if mid["open"] else None
            lower = ((min(mid["open"], mid["close"]) - mid["low"]) / mid["open"] * 100.0) if mid["open"] else None
            result[f"{prefix}_cum_return_pct"] = cum
            result[f"{prefix}_body_return_pct"] = body
            result[f"{prefix}_range_pct"] = candle_range
            result[f"{prefix}_upper_wick_pct"] = upper
            result[f"{prefix}_lower_wick_pct"] = lower
            candle_rows.append({
                "event_id": event.event_id,
                "report_id": event.report_id,
                "report": event.report_name,
                "scheduled_datetime_utc": t0.isoformat(),
                "timeframe": timeframe,
                "horizon_minutes": horizon,
                "bar_start_utc": start_minute.isoformat(),
                "bar_end_utc": (end_minute + timedelta(minutes=1)).isoformat(),
                "bid_open": b_agg["open"], "bid_high": b_agg["high"],
                "bid_low": b_agg["low"], "bid_close": b_agg["close"],
                "ask_open": a_agg["open"], "ask_high": a_agg["high"],
                "ask_low": a_agg["low"], "ask_close": a_agg["close"],
                "mid_open": mid["open"], "mid_high": mid["high"],
                "mid_low": mid["low"], "mid_close": mid["close"],
                "cumulative_return_pct": cum,
                "candle_body_return_pct": body,
                "candle_range_pct": candle_range,
                "upper_wick_pct": upper,
                "lower_wick_pct": lower,
                "bid_tick_volume_sum": b_agg["volume"],
                "ask_tick_volume_sum": a_agg["volume"],
            })
    return result, candle_rows


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return data[lo]
    frac = pos - lo
    return data[lo] * (1.0 - frac) + data[hi] * frac


def calculate_previous_revisions(events: list[Event]) -> None:
    """Compare this release's Previous with the prior release's recorded Actual."""
    grouped: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        grouped[event.report_id].append(event)
    for group in grouped.values():
        group.sort(key=lambda e: e.scheduled_utc)
        prior_actual: float | None = None
        for event in group:
            if prior_actual is not None and event.previous_num is not None:
                event.previous_revision = event.previous_num - prior_actual
                if event.previous_revision > 1e-12:
                    event.previous_revision_sign = "positive_revision"
                elif event.previous_revision < -1e-12:
                    event.previous_revision_sign = "negative_revision"
                else:
                    event.previous_revision_sign = "unchanged"
            if event.actual_num is not None:
                prior_actual = event.actual_num


def assign_scenario_levels(events: list[Event]) -> dict[str, dict[str, Any]]:
    by_report: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        by_report[event.report_id].append(event)
    boundaries: dict[str, dict[str, Any]] = {}
    for rid, group in by_report.items():
        beat = [abs(e.surprise) for e in group if e.surprise_sign == "beat" and e.surprise is not None]
        miss = [abs(e.surprise) for e in group if e.surprise_sign == "miss" and e.surprise is not None]
        q = {
            "beat": {"q33": quantile(beat, 1/3), "q67": quantile(beat, 2/3), "n": len(beat), "unit": "absolute Actual-Forecast in report's native units"},
            "miss": {"q33": quantile(miss, 1/3), "q67": quantile(miss, 2/3), "n": len(miss), "unit": "absolute Actual-Forecast in report's native units"},
        }
        boundaries[rid] = q
        for event in group:
            if event.surprise_sign == "inline":
                event.size_level = "inline"
            elif event.surprise_sign in ("beat", "miss") and event.surprise is not None:
                amount = abs(event.surprise)
                q33 = q[event.surprise_sign]["q33"]
                q67 = q[event.surprise_sign]["q67"]
                if q33 is None or q67 is None or amount <= q33:
                    size = "small"
                elif amount <= q67:
                    size = "medium"
                else:
                    size = "large"
                event.size_level = f"{event.surprise_sign}_{size}"
            else:
                event.size_level = "unknown"
    return boundaries


def period_name(year: int) -> str:
    for name, (start, end) in PERIODS.items():
        if start <= year <= end:
            return name
    return "OTHER"


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denom = 1.0 + z*z/total
    centre = (p + z*z/(2*total)) / denom
    half = z * math.sqrt((p*(1-p)/total) + (z*z/(4*total*total))) / denom
    return max(0.0, centre-half), min(1.0, centre+half)


def reliability(n: int) -> str:
    if n >= 15:
        return "reliable"
    if n >= 10:
        return "preliminary"
    if n > 0:
        return "reference"
    return "empty"


def signal_quality(p_up: float | None, p_down: float | None, p_up_dir: float | None, p_down_dir: float | None) -> tuple[str, float | None, float | None]:
    if p_up is None or p_down is None or p_up_dir is None or p_down_dir is None:
        return "no_data", None, None
    accuracy = max(p_up_dir, p_down_dir)
    edge = abs(p_up - p_down)
    if accuracy >= 0.85 and edge >= 0.50:
        quality = "strong"
    elif accuracy >= 0.70 and edge >= 0.30:
        quality = "moderate"
    elif accuracy >= 0.60 and edge >= 0.15:
        quality = "weak"
    else:
        quality = "noise"
    return quality, accuracy, edge


def summarize(values: list[float], flat_threshold: float | None) -> dict[str, Any]:
    if not values:
        return {"n": 0, "non_flat_n": 0, "p_up": None, "p_down": None, "p_flat": None,
                "p_up_dir": None, "p_down_dir": None, "p_up_ci_lo": None, "p_up_ci_hi": None,
                "p_down_ci_lo": None, "p_down_ci_hi": None, "p_flat_ci_lo": None, "p_flat_ci_hi": None,
                "p_up_dir_ci_lo": None, "p_up_dir_ci_hi": None, "p_down_dir_ci_lo": None, "p_down_dir_ci_hi": None,
                "mean": None, "mean_ci_lo": None, "mean_ci_hi": None, "median": None, "std": None, "iqr": None, "flat_threshold": flat_threshold,
                "reliability": "empty", "signal_quality": "no_data", "direction_lean": "unknown", "directional_accuracy": None, "directional_edge": None}
    threshold = max(0.0, float(flat_threshold or 0.0))
    up = sum(v > threshold for v in values)
    down = sum(v < -threshold for v in values)
    flat = len(values) - up - down
    n = len(values)
    nonflat = up + down
    p_up, p_down, p_flat = up/n, down/n, flat/n
    p_up_dir = up/nonflat if nonflat else None
    p_down_dir = down/nonflat if nonflat else None
    up_lo, up_hi = wilson_interval(up, n)
    down_lo, down_hi = wilson_interval(down, n)
    flat_lo, flat_hi = wilson_interval(flat, n)
    updir_lo, updir_hi = wilson_interval(up, nonflat)
    downdir_lo, downdir_hi = wilson_interval(down, nonflat)
    quality, accuracy, edge = signal_quality(p_up, p_down, p_up_dir, p_down_dir)
    direction_lean = "up" if up > down else "down" if down > up else "flat_or_balanced"
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if n > 1 else 0.0
    mean_half_width = 1.959963984540054 * std / math.sqrt(n) if n > 1 else None
    iqr = (quantile(values, 0.75) - quantile(values, 0.25)) if n > 1 else 0.0
    return {
        "n": n, "non_flat_n": nonflat, "p_up": p_up, "p_down": p_down, "p_flat": p_flat,
        "p_up_dir": p_up_dir, "p_down_dir": p_down_dir,
        "p_up_ci_lo": up_lo, "p_up_ci_hi": up_hi,
        "p_down_ci_lo": down_lo, "p_down_ci_hi": down_hi,
        "p_flat_ci_lo": flat_lo, "p_flat_ci_hi": flat_hi,
        "p_up_dir_ci_lo": updir_lo, "p_up_dir_ci_hi": updir_hi,
        "p_down_dir_ci_lo": downdir_lo, "p_down_dir_ci_hi": downdir_hi,
        "mean": mean, "mean_ci_lo": None if mean_half_width is None else mean - mean_half_width,
        "mean_ci_hi": None if mean_half_width is None else mean + mean_half_width,
        "median": statistics.median(values), "std": std, "iqr": iqr,
        "flat_threshold": threshold, "reliability": reliability(n),
        "signal_quality": quality, "direction_lean": direction_lean,
        "directional_accuracy": accuracy, "directional_edge": edge,
    }


def value_key(timeframe: str, horizon: int, metric: str) -> str:
    return f"{timeframe}_{horizon}_{metric}"


def collect_flat_thresholds(reactions: list[dict[str, Any]]) -> dict[tuple[str, int, str, str], float]:
    """0.5*sigma pooled by timeframe/horizon/metric/period, unique release time."""
    buckets: dict[tuple[str, int, str, str], dict[str, float]] = defaultdict(dict)
    for row in reactions:
        stamp = row["scheduled_datetime_utc"]
        period = period_name(int(row["year"]))
        for tf, horizons in HORIZONS.items():
            for h in horizons:
                for metric in ("cum_return_pct", "body_return_pct"):
                    v = row.get(value_key(tf, h, metric))
                    if v is None:
                        continue
                    # One price observation per release timestamp; co-released
                    # reports do not overweight the pooled volatility estimate.
                    buckets[(tf, h, metric, "ALL")][stamp] = float(v)
                    buckets[(tf, h, metric, period)][stamp] = float(v)
    result: dict[tuple[str, int, str, str], float] = {}
    for key, by_stamp in buckets.items():
        vals = list(by_stamp.values())
        sigma = statistics.stdev(vals) if len(vals) > 1 else 0.0
        result[key] = 0.5 * sigma
    return result


def event_in_period(row: dict[str, Any], period: str) -> bool:
    if period == "ALL":
        return True
    return period_name(int(row["year"])) == period


def make_statistics(events: list[Event], reactions: list[dict[str, Any]], thresholds: dict[tuple[str, int, str, str], float]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_report_events: dict[str, list[Event]] = defaultdict(list)
    by_report_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_report_events[event.report_id].append(event)
    for row in reactions:
        by_report_rows[row["report_id"]].append(row)

    stats_rows: list[dict[str, Any]] = []
    templates: dict[str, Any] = {
        "schema_version": "2.0",
        "generated_utc": datetime.now(UTC).isoformat(),
        "method": {
            "event_history_source": "data/economic_data/**/*.csv only; Forex Factory cache files are not read by the historical analyzer",
            "surprise_levels": list(SCENARIOS),
            "size_boundaries": "report-specific q33/q67 of absolute Actual-Forecast, separately for beat and miss, using full available report history",
            "recent_primary_period": "P4_2023_2026",
            "flat_threshold": "0.5 * pooled sample standard deviation by period, timeframe, horizon and metric; pooled observations are deduplicated by release timestamp",
            "probabilities": "p_up/p_down/p_flat include flat; p_up_dir/p_down_dir are conditional on non-flat",
            "confidence_intervals": "95% Wilson intervals for binomial probabilities",
            "price": "XAUUSD BID/ASK midpoint; no DXY",
            "timeframes": HORIZONS,
            "metrics": ["cum_return_pct", "body_return_pct"],
            "reliability": "n>=15 reliable; n=10..14 preliminary; n<10 reference",
            "co_releases": "All rows retained; co_release_count and is_isolated_release flag possible confounding",
        },
        "pooled_flat_thresholds": {},
        "reports": {},
    }
    for (tf, h, metric, period), threshold in sorted(thresholds.items()):
        templates["pooled_flat_thresholds"].setdefault(tf, {}).setdefault(str(h), {}).setdefault(metric, {})[period] = threshold

    all_period_names = ["ALL", *PERIODS.keys()]
    for rid in sorted(by_report_events):
        group_events = by_report_events[rid]
        group_rows = by_report_rows[rid]
        name = group_events[0].report_name
        currency_set = sorted({e.currency for e in group_events if e.currency})
        numeric = [e for e in group_events if e.surprise_sign in ("beat", "miss", "inline")]
        report_template: dict[str, Any] = {
            "report": name,
            "report_id": rid,
            "source_file": group_events[0].relative_file,
            "currencies": currency_set,
            "data_range": {
                "start": min(e.scheduled_utc.date() for e in group_events).isoformat(),
                "end": max(e.scheduled_utc.date() for e in group_events).isoformat(),
                "n_events": len(group_events),
                "n_numeric_actual_forecast": len(numeric),
            },
            "size_group_boundaries": {},
            "scenarios": {},
        }
        # Reconstruct cutoffs from event labels. The boundaries are separately
        # written below by the caller into the shared templates dict.
        scenarios_to_build = ["UNCONDITIONAL", *SCENARIOS]
        for scenario in scenarios_to_build:
            selected_rows = group_rows if scenario == "UNCONDITIONAL" else [r for r in group_rows if r.get("size_level") == scenario]
            scenario_obj: dict[str, Any] = {"timeframes": {}}
            for tf, horizons in HORIZONS.items():
                scenario_obj["timeframes"][tf] = {}
                for h in horizons:
                    scenario_obj["timeframes"][tf][str(h)] = {"metrics": {}}
                    for metric in ("cum_return_pct", "body_return_pct"):
                        metric_obj = {"periods": {}}
                        for period in all_period_names:
                            subset = [r for r in selected_rows if event_in_period(r, period) and r.get(value_key(tf, h, metric)) is not None]
                            vals = [float(r[value_key(tf, h, metric)]) for r in subset]
                            threshold = thresholds.get((tf, h, metric, period), 0.0)
                            summary = summarize(vals, threshold)
                            metric_obj["periods"][period] = summary
                            stats_rows.append({
                                "report_id": rid, "report": name,
                                "currency": ",".join(currency_set),
                                "scenario_level": scenario,
                                "timeframe": tf, "horizon_minutes": h,
                                "metric": metric, "period": period,
                                **summary,
                            })
                        scenario_obj["timeframes"][tf][str(h)]["metrics"][metric] = metric_obj
            report_template["scenarios"][scenario] = scenario_obj
        templates["reports"][rid] = report_template
    return stats_rows, templates


def apply_boundaries_to_templates(templates: dict[str, Any], boundaries: dict[str, dict[str, Any]]) -> None:
    for rid, data in boundaries.items():
        if rid in templates["reports"]:
            templates["reports"][rid]["size_group_boundaries"] = data


def fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value*100:.1f}%"


def fmt_ci(value: float | None, lo: float | None, hi: float | None) -> str:
    if value is None:
        return "n/a"
    if lo is None or hi is None:
        return fmt_pct(value)
    return f"{value*100:.1f}% [{lo*100:.1f}–{hi*100:.1f}%]"


def report_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"ОТЧЁТ: {report['report']}")
    lines.append(f"ID: {report['report_id']}")
    lines.append(f"Файл истории: {report['source_file']}")
    data = report["data_range"]
    lines.append(f"Период данных: {data['start']} — {data['end']}; событий: {data['n_events']}; числовых Actual/Forecast: {data['n_numeric_actual_forecast']}")
    lines.append("Валюты: " + (", ".join(report["currencies"]) or "не указана"))
    lines.append("")
    lines.append("СЕМЬ УРОВНЕЙ ОТКЛОНЕНИЯ ACTUAL ОТ FORECAST")
    boundaries = report.get("size_group_boundaries", {})
    for direction in ("beat", "miss"):
        b = boundaries.get(direction, {})
        lines.append(f"{direction.upper()}: q33={b.get('q33')}; q67={b.get('q67')}; n={b.get('n', 0)}")
    lines.append("Границы рассчитываются отдельно для этого отчёта. beat/miss означают только Actual выше/ниже Forecast, а не направление золота.")
    lines.append("Группы: beat_small / beat_medium / beat_large / inline / miss_small / miss_medium / miss_large.")
    lines.append("")
    lines.append("КАК ЧИТАТЬ ВЕРОЯТНОСТИ")
    lines.append("P(up), P(down), P(flat) включают все наблюдения и в сумме дают 100%.")
    lines.append("P(up | non-flat) и P(down | non-flat) исключают flat. В квадратных скобках — 95% Wilson CI.")
    lines.append("n<10: reference; n=10–14: preliminary; n>=15: reliable. Интервал неопределённости не гарантирует будущий результат.")
    lines.append("Основной период: 2023–2026. ALL — дополнительный контекст на всей истории.")
    lines.append("")

    scenarios = report["scenarios"]
    for period in ("P4_2023_2026", "ALL"):
        title = "ОСНОВНОЙ ПЕРИОД 2023–2026" if period == "P4_2023_2026" else "ВСЯ ДОСТУПНАЯ ИСТОРИЯ"
        lines.append("=" * 100)
        lines.append(title)
        lines.append("=" * 100)
        for tf, horizon_map in HORIZONS.items():
            lines.append("")
            lines.append(f"{tf} — кумулятивная реакция от close за минуту до публикации")
            for h in horizon_map:
                bucket = scenarios["UNCONDITIONAL"]["timeframes"][tf][str(h)]["metrics"]["cum_return_pct"]["periods"][period]
                lines.append(f"  +{h:>3} мин | BASE n={bucket['n']:>3} | up {fmt_ci(bucket['p_up'], bucket['p_up_ci_lo'], bucket['p_up_ci_hi'])} | down {fmt_ci(bucket['p_down'], bucket['p_down_ci_lo'], bucket['p_down_ci_hi'])} | flat {fmt_ci(bucket['p_flat'], bucket['p_flat_ci_lo'], bucket['p_flat_ci_hi'])} | up no-flat {fmt_ci(bucket['p_up_dir'], bucket['p_up_dir_ci_lo'], bucket['p_up_dir_ci_hi'])} | down no-flat {fmt_ci(bucket['p_down_dir'], bucket['p_down_dir_ci_lo'], bucket['p_down_dir_ci_hi'])}")
                for scenario in SCENARIOS:
                    s = scenarios[scenario]["timeframes"][tf][str(h)]["metrics"]["cum_return_pct"]["periods"][period]
                    lines.append(f"       {scenario:<13} n={s['n']:>3} | up {fmt_ci(s['p_up'], s['p_up_ci_lo'], s['p_up_ci_hi'])} | down {fmt_ci(s['p_down'], s['p_down_ci_lo'], s['p_down_ci_hi'])} | flat {fmt_ci(s['p_flat'], s['p_flat_ci_lo'], s['p_flat_ci_hi'])} | up no-flat {fmt_ci(s['p_up_dir'], s['p_up_dir_ci_lo'], s['p_up_dir_ci_hi'])} | down no-flat {fmt_ci(s['p_down_dir'], s['p_down_dir_ci_lo'], s['p_down_dir_ci_hi'])} | {s['reliability']}/{s['signal_quality']}")
            lines.append(f"{tf} — направление тела последней свечи (open→close), отдельно от кумулятивной реакции")
            for h in horizon_map:
                chunks = []
                for scenario in ("UNCONDITIONAL", *SCENARIOS):
                    s = scenarios[scenario]["timeframes"][tf][str(h)]["metrics"]["body_return_pct"]["periods"][period]
                    chunks.append(f"{scenario}: up {fmt_pct(s['p_up'])}, down {fmt_pct(s['p_down'])}, flat {fmt_pct(s['p_flat'])}, n={s['n']}")
                lines.append(f"  +{h:>3} мин | " + " | ".join(chunks))
        lines.append("")
    lines.append("ПРИМЕЧАНИЯ")
    lines.append("1) XAUUSD midpoint=(BID+ASK)/2. DXY не используется.")
    lines.append("2) M5/M15 строятся из полных последовательных M1 баров. Для статистики используются завершённые свечи, границы которых совпадают с временем релиза.")
    lines.append("3) При одинаковом elapsed horizon close кумулятивная доходность M1/M5/M15 математически совпадает; отличия M5/M15 отражаются в body/range/wicks.")
    lines.append("4) Публикации с одним временем отмечены как co-releases в event_reactions.csv; статистика не доказывает причинный эффект отдельного отчёта.")
    lines.append("5) Пропущенные минутные бары не интерполируются. Неполные агрегированные свечи исключаются.")
    lines.append("6) Flat threshold = 0.5 * sigma по всем уникальным временным отметкам публикаций для соответствующего периода, timeframe, горизонта и метрики.")
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def safe_report_path(out_root: Path, report_id: str) -> Path:
    target = out_root / "reports" / (report_id + ".txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def run(args: argparse.Namespace) -> dict[str, Any]:
    events, skipped, input_counts = load_events(args.economic_data, args.naive_timezone, args.allow_midnight)
    if not events:
        raise RuntimeError("No usable events found in data/economic_data. Inspect skipped_events.csv and verify that report CSVs contain release timestamps.")

    calculate_previous_revisions(events)
    boundaries = assign_scenario_levels(events)
    co_release_map: dict[str, set[str]] = defaultdict(set)
    for event in events:
        stamp = event.scheduled_utc.replace(second=0, microsecond=0).isoformat()
        co_release_map[stamp].add(event.event_id)
    for event in events:
        stamp = event.scheduled_utc.replace(second=0, microsecond=0).isoformat()
        event.co_release_count = len(co_release_map[stamp])
        event.is_isolated = event.co_release_count <= 1

    bid = DailyPriceCache(args.bid_root, "BID", args.price_cache_days)
    ask = DailyPriceCache(args.ask_root, "ASK", args.price_cache_days)
    reactions: list[dict[str, Any]] = []
    candles: list[dict[str, Any]] = []
    for i, event in enumerate(events, 1):
        row, candle_rows = metric_values(event, bid, ask)
        reactions.append(row)
        candles.extend(candle_rows)
        if i % 250 == 0:
            print(f"Processed {i:,}/{len(events):,} report events...")

    thresholds = collect_flat_thresholds(reactions)
    stats_rows, templates = make_statistics(events, reactions, thresholds)
    apply_boundaries_to_templates(templates, boundaries)

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "event_reactions.csv", reactions)
    write_csv(out / "group_stats.csv", stats_rows)
    write_csv(out / "derived_event_candles.csv", candles)
    write_csv(out / "skipped_events.csv", skipped)
    (out / "scenario_templates.json").write_text(json.dumps(templates, ensure_ascii=False, indent=2), encoding="utf-8")

    report_count = 0
    for rid, data in templates["reports"].items():
        path = safe_report_path(out, rid)
        path.write_text(report_text(data), encoding="utf-8")
        report_count += 1

    quality = Counter()
    events_with_baseline = 0
    for row in reactions:
        if row.get("baseline_mid_close") is not None:
            events_with_baseline += 1
        if any(row.get(value_key(tf, h, "cum_return_pct")) is not None for tf, hs in HORIZONS.items() for h in hs):
            quality["with_at_least_one_reaction"] += 1
        else:
            quality["without_complete_reaction_window"] += 1
    summary = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "input_counts": input_counts,
        "reports_analyzed": report_count,
        "unique_report_ids": len({e.report_id for e in events}),
        "numeric_actual_forecast_events": sum(e.surprise_sign in ("beat", "miss", "inline") for e in events),
        "events_with_baseline_quotes": events_with_baseline,
        "events_with_at_least_one_complete_reaction_window": quality["with_at_least_one_reaction"],
        "events_without_complete_reaction_window": quality["without_complete_reaction_window"],
        "co_release_events": sum(not e.is_isolated for e in events),
        "bid_files_loaded": bid.files_loaded,
        "ask_files_loaded": ask.files_loaded,
        "bid_m1_rows_loaded": bid.rows_loaded,
        "ask_m1_rows_loaded": ask.rows_loaded,
        "derived_event_candle_rows": len(candles),
        "outputs": ["event_reactions.csv", "group_stats.csv", "scenario_templates.json", "derived_event_candles.csv", "skipped_events.csv", "run_summary.json", "reports/**/*.txt"],
        "notes": [
            "Historical analyzer reads only data/economic_data/**/*.csv. It does not read either Forex Factory cache.",
            "The current/main Forex Factory cache is used only by the separate live Java calendar collector to locate future releases.",
            "Scenario groups are report-specific q33/q67 bins for beat and miss plus inline.",
            "Primary probability period is 2023-2026; ALL and earlier periods are also written.",
            "M5/M15 event candles are aggregated from complete M1 BID/ASK candles; no interpolation.",
            "Cumulative returns at the same elapsed horizon are identical across frame sizes when the bars are complete; body/range/wick metrics are frame-specific.",
            "Flat thresholds use 0.5*sigma pooled across unique release timestamps for timeframe/horizon/metric/period.",
            "Probabilities are descriptive historical frequencies, not calibrated guarantees or causal attribution.",
        ],
    }
    (out / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze each historical economic report against XAUUSD BID/ASK candles.")
    parser.add_argument("--economic-data", type=Path, required=True, help="Root folder containing one historical CSV per report.")
    parser.add_argument("--bid-root", type=Path, required=True, help="BID daily M1 root: YYYY/MM/XAUUSD_M1_BID_YYYY-MM-DD.csv")
    parser.add_argument("--ask-root", type=Path, required=True, help="ASK daily M1 root: YYYY/MM/XAUUSD_M1_ASK_YYYY-MM-DD.csv")
    parser.add_argument("--output", type=Path, required=True, help="Output folder for per-report TXT files, CSV statistics and JSON templates.")
    parser.add_argument("--naive-timezone", default="UTC", help="Timezone for event timestamps without an offset, or 'skip'.")
    parser.add_argument("--allow-midnight", action="store_true", help="Accept timestamps at midnight when verified as real publication times.")
    parser.add_argument("--price-cache-days", type=int, default=8, help="Number of daily BID/ASK files retained in memory.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.price_cache_days < 2:
        parser.error("--price-cache-days must be at least 2")
    try:
        run(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
