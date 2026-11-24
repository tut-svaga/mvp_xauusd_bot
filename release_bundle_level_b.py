#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
release_bundle_level_b.py -- Stage B: XAUUSD Release Bundle Reaction Analyzer.

Сопоставляет события этапа A (release_events.csv, один уникальный момент
публикации = одно событие) с минутными котировками XAUUSD BID/ASK (M1) и считает
статистику реакции золота на горизонтах +1/+5/+15/+30/+60/+120 минут.

Ключевые правила (см. ТЗ):
  * MID = (BID + ASK) / 2 по каждому из O/H/L/C, ключ сопоставления BID/ASK --
    нормализованный UTC timestamp; без интерполяции и подмены отсутствующих свечей.
  * --bar-time-mode start (по умолчанию): timestamp = начало минутной свечи.
      baseline          = mid_close свечи T-1m
      close горизонта h = mid_close свечи T+(h-1)m
      окно MFE/MAE      = свечи T .. T+(h-1)m  (то есть [T, T+h))
    --bar-time-mode end: timestamp = конец свечи; все метки сдвигаются на -1 минуту
    при чтении, дальнейшая логика идентична. Режим не переключается автоматически.
  * Предположение о семантике timestamp проверяется эмпирически (диапазон свечей
    вокруг моментов публикации) и результат явно пишется в отчёт.
  * Только стандартная библиотека Python (3.8+). Исходные файлы не изменяются.

Запуск (PowerShell):
  py -3 .\\release_bundle_level_b.py `
    --stage-a ".\\output\\release_bundle_level_a" `
    --bid-root "E:\\work\\XAU_gold\\XAUUSD\\raw\\BID" `
    --ask-root "E:\\work\\XAU_gold\\XAUUSD\\raw\\ASK" `
    --output ".\\output\\release_bundle_level_b"
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import logging
import math
import os
import platform
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

VERSION = "1.0.0"

HORIZONS = (1, 5, 15, 30, 60, 120)
MAX_H = max(HORIZONS)
FLAT_THRESHOLDS = (0.25, 0.50, 1.00)
SEM_OFFSETS = (-2, -1, 0, 1, 2, 3)  # смещения (в минутах) меток для проверки семантики
PRE_BARS = 2  # сколько минут до T нужно загружать (baseline T-1 + проверка семантики T-2)
EPOCH_ORD = date(1970, 1, 1).toordinal()
OHLC_EPS = 1e-6
REQUIRED_EVENT_COLUMNS = ("event_datetime_utc", "bundle_composition", "component_count")

log = logging.getLogger("level_b")


class InputError(Exception):
    """Ошибка входных данных / параметров (код возврата 2)."""


# --------------------------------------------------------------------------- #
# Время
# --------------------------------------------------------------------------- #
_day_cache: dict = {}


def _day_epoch(ds: str) -> int:
    v = _day_cache.get(ds)
    if v is None:
        if ds[4] != "-" or ds[7] != "-":
            raise ValueError(ds)
        v = (date(int(ds[0:4]), int(ds[5:7]), int(ds[8:10])).toordinal() - EPOCH_ORD) * 86400
        _day_cache[ds] = v
    return v


def _parse_ts_fallback(s: str):
    s2 = " ".join(s.strip().split())
    if s2.endswith(("Z", "z")):
        s2 = s2[:-1] + "+00:00"
    s2 = s2.replace(" ", "T", 1)
    dt = datetime.fromisoformat(s2)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_ts(s):
    """Строка -> epoch seconds UTC (int или float) либо None. Несколько пробелов допустимы.
    Строка без offset трактуется как UTC."""
    try:
        t = s.split()
        if len(t) == 2 and len(t[0]) == 10 and len(t[1]) >= 8 and t[1][2] == ":" and t[1][5] == ":":
            tm = t[1]
            base = _day_epoch(t[0]) + int(tm[0:2]) * 3600 + int(tm[3:5]) * 60 + int(tm[6:8])
            rest = tm[8:]
            if rest in ("+00:00", "Z", "z", "+0000", "+00", ""):
                return base
            if len(rest) == 6 and rest[0] in "+-" and rest[3] == ":":
                off = int(rest[1:3]) * 3600 + int(rest[4:6]) * 60
                return base - off if rest[0] == "+" else base + off
        return _parse_ts_fallback(s)
    except Exception:
        return None


def fmt_ts(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


# --------------------------------------------------------------------------- #
# Диагностика качества данных
# --------------------------------------------------------------------------- #
DQ_FIELDS = ["category", "severity", "scope", "event_datetime_utc", "horizon_min",
             "timestamp_utc", "side", "file", "count", "detail"]


class DQ:
    def __init__(self, cap: int):
        self.rows = []
        self.counts = Counter()
        self.severity = {}
        self.listed = Counter()
        self.cap = cap

    def add(self, category, severity, scope, detail="", count=1, event=None,
            horizon=None, ts=None, side="", file=""):
        self.counts[category] += count
        self.severity.setdefault(category, severity)
        if scope in ("GLOBAL", "FILE") or self.listed[category] < self.cap:
            self.listed[category] += 1
            self.rows.append({
                "category": category, "severity": severity, "scope": scope,
                "event_datetime_utc": fmt_ts(event["epoch"]) if event else "",
                "horizon_min": horizon if horizon is not None else "",
                "timestamp_utc": fmt_ts(ts) if isinstance(ts, int) else (ts or ""),
                "side": side, "file": file, "count": count, "detail": detail})

    def finalize(self):
        for cat, total in sorted(self.counts.items(), key=lambda kv: -kv[1]):
            self.rows.append({
                "category": cat, "severity": self.severity.get(cat, ""), "scope": "SUMMARY",
                "event_datetime_utc": "", "horizon_min": "", "timestamp_utc": "", "side": "",
                "file": "", "count": total,
                "detail": f"всего={total}; строк в списке={self.listed[cat]}"
                          + ("; СПИСОК УСЕЧЕН (--max-quality-rows)" if self.listed[cat] < total and self.listed[cat] >= self.cap else "")})


# --------------------------------------------------------------------------- #
# Чтение котировок
# --------------------------------------------------------------------------- #
class Merged:
    """Объединённые интервалы [start, end] (включительно, в epoch seconds)."""

    def __init__(self, intervals):
        intervals.sort()
        merged = []
        for s, e in intervals:
            if merged and s <= merged[-1][1] + 60:
                if e > merged[-1][1]:
                    merged[-1][1] = e
            else:
                merged.append([s, e])
        self.starts = [m[0] for m in merged]
        self.ends = [m[1] for m in merged]

    def __len__(self):
        return len(self.starts)

    def contains(self, x) -> bool:
        i = bisect.bisect_left(self.ends, x)
        return i < len(self.starts) and self.starts[i] <= x

    def overlaps(self, lo, hi) -> bool:
        i = bisect.bisect_left(self.ends, lo)
        return i < len(self.starts) and self.starts[i] <= hi


class SideStore:
    def __init__(self, name):
        self.name = name
        self.bars = {}          # bar_start_epoch -> (o, h, l, c)
        self.ambiguous = set()
        self.invalid = {}       # bar_start_epoch -> reason
        self.invalid_reasons = Counter()
        self.files_total = 0
        self.files_read = 0
        self.files_skipped = 0
        self.rows_read = 0
        self.rows_in_window = 0
        self.unparseable = 0
        self.off_minute = 0
        self.dup_identical = 0
        self.dup_conflict = 0
        self.cov_min = None
        self.cov_max = None


def find_csvs(root: str):
    if not os.path.isdir(root):
        raise InputError(f"Каталог не найден: {root}")
    out = []
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            if fn.lower().endswith(".csv"):
                out.append(os.path.join(dp, fn))
    out.sort()
    if not out:
        raise InputError(f"В каталоге нет CSV: {root}")
    return out


def _first_col(raw: bytes):
    return raw.decode("utf-8-sig", errors="replace").split(",", 1)[0]


def probe_file_range(path):
    """Быстро определить [первый, последний] timestamp файла по голове и хвосту.
    Имя файла не используется. Нет результата -> (None, None) -> файл читается целиком."""
    try:
        with open(path, "rb") as f:
            first = None
            for i, raw in enumerate(f):
                if i > 5:
                    break
                ts = parse_ts(_first_col(raw))
                if ts is not None:
                    first = ts
                    break
            size = f.seek(0, 2)
            back = min(size, 65536)
            f.seek(size - back)
            last = None
            for raw in reversed(f.read().splitlines()):
                if not raw.strip():
                    continue
                ts = parse_ts(_first_col(raw))
                if ts is not None:
                    last = ts
                    break
        if first is None or last is None or last < first:
            return None, None
        return first, last
    except OSError:
        return None, None


def read_side(files, st: SideStore, needed: Merged, shift: int, no_prefilter: bool, dq: DQ):
    """Один проход по каждому подходящему файлу; в память попадают только свечи,
    нужные окнам событий. Ключ = начало свечи (epoch), с учётом --bar-time-mode."""
    st.files_total = len(files)
    t0 = time.time()
    for idx, path in enumerate(files, 1):
        fmin, fmax = (None, None) if no_prefilter else probe_file_range(path)
        if fmin is not None:
            st.cov_min = fmin if st.cov_min is None else min(st.cov_min, fmin)
            st.cov_max = fmax if st.cov_max is None else max(st.cov_max, fmax)
            if not needed.overlaps(int(fmin), int(fmax)):
                st.files_skipped += 1
                continue
        st.files_read += 1
        n_file = 0
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                for row in csv.reader(fh):
                    if not row:
                        continue
                    ts = parse_ts(row[0])
                    if ts is None:
                        if row[0].strip().lower() in ("timestamp", "time", "date", "datetime", "gmt time"):
                            continue
                        st.rows_read += 1
                        st.unparseable += 1
                        continue
                    st.rows_read += 1
                    n_file += 1
                    if ts % 60:
                        st.off_minute += 1
                        continue
                    ts = int(ts)
                    if fmin is None:
                        st.cov_min = ts if st.cov_min is None else min(st.cov_min, ts)
                        st.cov_max = ts if st.cov_max is None else max(st.cov_max, ts)
                    key = ts - shift
                    if not needed.contains(key):
                        continue
                    st.rows_in_window += 1
                    if len(row) < 5:
                        st.invalid[key] = "SHORT_ROW"
                        st.invalid_reasons["SHORT_ROW"] += 1
                        continue
                    try:
                        o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
                    except ValueError:
                        st.invalid[key] = "NON_NUMERIC"
                        st.invalid_reasons["NON_NUMERIC"] += 1
                        continue
                    if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(l) and math.isfinite(c)
                            and o > 0 and h > 0 and l > 0 and c > 0):
                        st.invalid[key] = "NON_POSITIVE_OR_NAN"
                        st.invalid_reasons["NON_POSITIVE_OR_NAN"] += 1
                        continue
                    if (h < l - OHLC_EPS or o > h + OHLC_EPS or o < l - OHLC_EPS
                            or c > h + OHLC_EPS or c < l - OHLC_EPS):
                        st.invalid[key] = "OHLC_INCONSISTENT"
                        st.invalid_reasons["OHLC_INCONSISTENT"] += 1
                        continue
                    bar = (o, h, l, c)
                    old = st.bars.get(key)
                    if old is not None:
                        if old == bar:
                            st.dup_identical += 1
                        else:
                            st.dup_conflict += 1
                            st.ambiguous.add(key)
                    elif key in st.ambiguous:
                        st.dup_conflict += 1
                    else:
                        st.bars[key] = bar
        except (OSError, UnicodeDecodeError) as exc:
            dq.add("FILE_READ_ERROR", "HIGH", "FILE", detail=str(exc), side=st.name, file=path)
            log.error("%s: ошибка чтения %s: %s", st.name, path, exc)
        if idx % 200 == 0:
            log.info("%s: просмотрено файлов %d/%d (прочитано %d, пропущено %d), строк %d, %.1f c",
                     st.name, idx, len(files), st.files_read, st.files_skipped, st.rows_read, time.time() - t0)
    for k in st.ambiguous:
        st.bars.pop(k, None)
    log.info("%s: файлов %d (прочитано %d, пропущено по диапазону %d); строк %d; в окнах событий %d; "
             "свечей сохранено %d", st.name, st.files_total, st.files_read, st.files_skipped,
             st.rows_read, st.rows_in_window, len(st.bars))


# --------------------------------------------------------------------------- #
# События этапа A
# --------------------------------------------------------------------------- #
def load_events(path: str, dq: DQ):
    if not os.path.isfile(path):
        raise InputError(f"Файл событий не найден: {path}")
    events, seen = [], set()
    n_rows = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        missing = [c for c in REQUIRED_EVENT_COLUMNS if c not in (rd.fieldnames or [])]
        if missing:
            raise InputError(f"В {path} нет обязательных колонок: {missing}")
        for line_no, row in enumerate(rd, start=2):
            n_rows += 1
            raw = (row.get("event_datetime_utc") or "").strip()
            ts = parse_ts(raw)
            if ts is None:
                dq.add("EVENT_TIME_UNPARSEABLE", "HIGH", "EVENT", detail=f"строка {line_no}: '{raw}'")
                continue
            if ts % 60:
                dq.add("EVENT_TIME_NOT_ON_MINUTE", "LOW", "EVENT",
                       detail=f"строка {line_no}: '{raw}' -> округлено вниз до минуты")
            epoch = int(ts // 60 * 60)
            if epoch in seen:
                dq.add("DUPLICATE_EVENT_TIMESTAMP", "MEDIUM", "EVENT",
                       detail=f"строка {line_no}: {fmt_ts(epoch)} повторно -> пропущено (одно событие = один момент)")
                continue
            seen.add(epoch)
            try:
                cc = int(float(row.get("component_count") or 0))
            except ValueError:
                cc = 0
                dq.add("EVENT_BAD_COMPONENT_COUNT", "LOW", "EVENT", detail=f"строка {line_no}")
            comp = (row.get("bundle_composition") or "").strip() or "UNKNOWN"
            events.append({"epoch": epoch, "composition": comp, "component_count": cc})
    events.sort(key=lambda e: e["epoch"])
    return events, n_rows


# --------------------------------------------------------------------------- #
# Расчёты
# --------------------------------------------------------------------------- #
def direction(change: float) -> str:
    return "UP" if change > 0 else ("DOWN" if change < 0 else "ZERO")


def build_mid(bid: SideStore, ask: SideStore):
    mid = {}
    for k, b in bid.bars.items():
        a = ask.bars.get(k)
        if a is None:
            continue
        mid[k] = ((b[0] + a[0]) / 2.0, (b[1] + a[1]) / 2.0, (b[2] + a[2]) / 2.0,
                  (b[3] + a[3]) / 2.0, a[3] - b[3])  # o, h, l, c, spread
    return mid


def make_missing_reason(bid: SideStore, ask: SideStore):
    def reason(ts):
        parts = []
        for side, st in (("BID", bid), ("ASK", ask)):
            if ts in st.bars:
                continue
            if ts in st.ambiguous:
                parts.append(f"{side}_DUPLICATE_CONFLICT")
            elif ts in st.invalid:
                parts.append(f"{side}_INVALID_PRICE({st.invalid[ts]})")
            else:
                parts.append(f"{side}_MISSING")
        if parts == ["BID_MISSING", "ASK_MISSING"]:
            return "BOTH_MISSING"
        return "+".join(parts) if parts else "OK"
    return reason


def process_events(events, mid, reason_fn, shift, dq: DQ, cov):
    reactions, per_event, sem_samples = [], [], []
    for ev in events:
        T = ev["epoch"]
        dt = datetime.fromtimestamp(T, tz=timezone.utc)
        if dt.weekday() >= 5:
            dq.add("EVENT_ON_WEEKEND_UTC", "LOW", "EVENT", event=ev, detail=f"{dt:%A}")
        if cov[0] is not None and (T < cov[0] or T > cov[1]):
            dq.add("EVENT_OUTSIDE_QUOTE_COVERAGE", "HIGH", "EVENT", event=ev,
                   detail=f"покрытие котировок {fmt_ts(int(cov[0]))} .. {fmt_ts(int(cov[1]))}")

        base_key = T - 60
        base = mid.get(base_key)
        if base is None:
            dq.add("MISSING_BASELINE", "HIGH", "EVENT", event=ev, ts=base_key, side="MID",
                   detail=reason_fn(base_key))

        present = [(T + i * 60) in mid for i in range(MAX_H)]
        if not any(present):
            dq.add("NO_QUOTES_IN_EVENT_WINDOW", "HIGH", "EVENT", event=ev,
                   detail=("выходной (UTC)" if dt.weekday() >= 5 else "будний день: возможен праздник/разрыв данных"))

        # накопление экстремумов только по непрерывному префиксу окна
        exc_at, run_max, run_min = {}, -math.inf, math.inf
        for i in range(MAX_H):
            bar = mid.get(T + i * 60)
            if bar is None:
                break
            run_max, run_min = max(run_max, bar[1]), min(run_min, bar[2])
            if (i + 1) in HORIZONS:
                exc_at[i + 1] = (run_max, run_min)

        # проверка семантики timestamp: диапазон свечей вокруг T по СЫРЫМ меткам
        ranges = []
        for k in SEM_OFFSETS:
            b = mid.get(T + 60 * k - shift)
            ranges.append(None if b is None else b[1] - b[2])
        if all(r is not None for r in ranges):
            sem_samples.append(ranges)

        chg = {}
        for h in HORIZONS:
            endbar = mid.get(T + (h - 1) * 60)
            chg[h] = round(endbar[3] - base[3], 6) if (base is not None and endbar is not None) else None
        init_dir = direction(chg[1]) if chg[1] is not None else None

        n_valid = n_exc = 0
        for h in HORIZONS:
            end_key = T + (h - 1) * 60
            endbar = mid.get(end_key)
            valid = chg[h] is not None
            complete = base is not None and h in exc_at
            if base is not None and endbar is None:
                dq.add("MISSING_HORIZON_CLOSE", "MEDIUM", "EVENT", event=ev, horizon=h, ts=end_key,
                       side="MID", detail=reason_fn(end_key))
            if base is not None and not complete:
                miss = sum(1 for i in range(h) if not present[i])
                dq.add("INCOMPLETE_EXCURSION_WINDOW", "MEDIUM", "EVENT", event=ev, horizon=h,
                       detail=f"отсутствует свечей в окне: {miss} из {h} (MFE/MAE не считаются)")
            row = {
                "event_epoch": T, "event_datetime_utc": fmt_ts(T),
                "bundle_composition": ev["composition"], "component_count": ev["component_count"],
                "horizon_min": h, "baseline_bar_utc": fmt_ts(base_key),
                "baseline_mid_close": base[3] if base else None,
                "horizon_bar_utc": fmt_ts(end_key),
                "horizon_mid_close": endbar[3] if endbar else None,
                "change_usd": chg[h],
                "change_pct": (chg[h] / base[3] * 100.0) if valid else None,
                "mfe_usd": (exc_at[h][0] - base[3]) if complete else None,
                "mae_usd": (exc_at[h][1] - base[3]) if complete else None,
                "excursion_window_complete": complete,
                "initial_direction": init_dir if valid else None,
                "final_direction": direction(chg[h]) if valid else None,
                "baseline_spread_usd": base[4] if base else None,
                "valid_price": valid,
                "status": "OK" if valid else ("NO_BASELINE" if base is None else "NO_HORIZON_CLOSE"),
            }
            outcome, rev = "NA", ""
            if valid and init_dir in ("UP", "DOWN"):
                fd = row["final_direction"]
                if fd == "ZERO":
                    outcome, rev = "FINAL_ZERO", "FALSE"
                elif fd != init_dir:
                    outcome, rev = "REVERSAL", "TRUE"
                else:
                    outcome, rev = "PERSISTED", "FALSE"
            row["outcome"], row["reversal"] = outcome, rev
            reactions.append(row)
            n_valid += valid
            n_exc += complete
        if n_valid == 0:
            dq.add("EVENT_NO_REACTION_COMPUTABLE", "HIGH", "EVENT", event=ev,
                   detail="ни один горизонт не может быть рассчитан")
        per_event.append({"epoch": T, "n_valid": n_valid, "n_exc": n_exc})
    return reactions, per_event, sem_samples


def semantic_check(samples, mode):
    res = {"n_events_used": len(samples), "offsets_min": list(SEM_OFFSETS)}
    if not samples:
        res.update(verdict="INCONCLUSIVE", reason="нет событий с полными свечами T-2..T+3")
        return res
    means = [statistics.mean(s[i] for s in samples) for i in range(len(SEM_OFFSETS))]
    argmax_counts = Counter(SEM_OFFSETS[max(range(len(s)), key=lambda i: s[i])] for s in samples)
    res["mean_range_usd_by_offset"] = {str(o): round(m, 4) for o, m in zip(SEM_OFFSETS, means)}
    res["share_events_max_range_at_offset"] = {str(o): round(c / len(samples), 4) for o, c in sorted(argmax_counts.items())}
    m = dict(zip(SEM_OFFSETS, means))
    best = max(m, key=m.get)
    verdict, why = "INCONCLUSIVE", "эмпирический признак выражен недостаточно"
    if len(samples) >= 20:
        if best == 0 and m[0] >= 1.5 * m[-1]:
            verdict, why = "START_CONFIRMED", "максимальный средний диапазон у метки T и >=1.5x метки T-1m"
        elif best == 1 and m[1] >= 1.5 * m[0]:
            verdict, why = "END_SUSPECTED", "максимальный средний диапазон у метки T+1m (метка=конец свечи?)"
    else:
        why = "слишком мало событий (<20) для проверки"
    res["verdict"], res["reason"] = verdict, why
    res["configured_mode"] = mode
    res["conflict_with_configured_mode"] = (
        (mode == "start" and verdict == "END_SUSPECTED") or (mode == "end" and verdict == "START_CONFIRMED"))
    return res


# --------------------------------------------------------------------------- #
# Статистика
# --------------------------------------------------------------------------- #
def pct(n, d):
    return round(100.0 * n / d, 4) if d else None


def rnd(x, nd=6):
    return None if x is None else round(x, nd)


def _mean(v):
    return statistics.mean(v) if v else None


def _median(v):
    return statistics.median(v) if v else None


def binom_two_sided_p(k, n):
    """Точный двусторонний биномиальный тест p=0.5 (k успехов из n)."""
    if n == 0:
        return None
    m = max(k, n - k)
    c = math.comb(n, m)
    tail = 0
    for i in range(m, n + 1):
        tail += c
        c = c * (n - i) // (i + 1)
    return min(1.0, 2 * tail / (1 << n))


def sample_flag(n, min_sample):
    if n == 0:
        return "NO_DATA"
    if n < 10:
        return "VERY_LOW_SAMPLE(<10)"
    if n < min_sample:
        return f"LOW_SAMPLE(<{min_sample})"
    return "OK"


def compute_stats(rows, n_events, min_sample):
    valid = [r for r in rows if r["valid_price"]]
    n_valid = len(valid)
    ch = [r["change_usd"] for r in valid]
    n_up = sum(1 for c in ch if c > 0)
    n_down = sum(1 for c in ch if c < 0)
    n_zero = n_valid - n_up - n_down
    exc = [r for r in valid if r["excursion_window_complete"]]
    mfe = [r["mfe_usd"] for r in exc]
    mae = [r["mae_usd"] for r in exc]
    init_nz = [r for r in valid if r["initial_direction"] in ("UP", "DOWN")]
    n_rev = sum(1 for r in init_nz if r["outcome"] == "REVERSAL")
    n_per = sum(1 for r in init_nz if r["outcome"] == "PERSISTED")
    out = {
        "n_events_in_cohort": n_events,
        "n_valid_price_windows": n_valid,
        "n_missing_windows": n_events - n_valid,
        "n_up": n_up, "n_down": n_down, "n_zero": n_zero,
        "up_raw_pct": pct(n_up, n_valid), "down_raw_pct": pct(n_down, n_valid), "zero_pct": pct(n_zero, n_valid),
        "mean_change_usd": rnd(_mean(ch)), "median_change_usd": rnd(_median(ch)),
        "std_change_usd": rnd(statistics.stdev(ch)) if n_valid > 1 else None,
        "mean_absolute_change_usd": rnd(_mean([abs(c) for c in ch])),
        "n_valid_excursion_windows": len(exc),
        "mean_mfe_usd": rnd(_mean(mfe)), "mean_mae_usd": rnd(_mean(mae)),
        "median_mfe_usd": rnd(_median(mfe)), "median_mae_usd": rnd(_median(mae)),
        "mean_adverse_excursion_usd": rnd(_mean([abs(min(0.0, m)) for m in mae])),
        "n_initial_nonzero": len(init_nz), "n_reversal": n_rev, "n_persisted": n_per,
        "reversal_pct": pct(n_rev, len(init_nz)), "direction_persisted_pct": pct(n_per, len(init_nz)),
        "p_value_up_vs_down_binomial": rnd(binom_two_sided_p(n_up, n_up + n_down), 6),
        "sample_warning": sample_flag(n_valid, min_sample),
    }
    for thr in FLAT_THRESHOLDS:
        u = sum(1 for c in ch if c > thr)
        d = sum(1 for c in ch if c < -thr)
        out[f"up_pct_thr_{thr:.2f}"] = pct(u, n_valid)
        out[f"down_pct_thr_{thr:.2f}"] = pct(d, n_valid)
        out[f"flat_pct_thr_{thr:.2f}"] = pct(n_valid - u - d, n_valid)
    return out


def compute_reversal(rows, n_events):
    both = [r for r in rows if r["valid_price"] and r["initial_direction"] is not None]
    out = {"n_events_in_cohort": n_events, "n_valid_initial_and_final": len(both)}
    out["n_initial_zero"] = sum(1 for r in both if r["initial_direction"] == "ZERO")
    nz = [r for r in both if r["initial_direction"] in ("UP", "DOWN")]
    out["n_initial_nonzero"] = len(nz)
    n_rev = sum(1 for r in nz if r["outcome"] == "REVERSAL")
    n_per = sum(1 for r in nz if r["outcome"] == "PERSISTED")
    n_fz = sum(1 for r in nz if r["outcome"] == "FINAL_ZERO")
    out.update(n_reversal=n_rev, n_persisted=n_per, n_final_zero_after_nonzero_initial=n_fz,
               reversal_pct_of_initial_nonzero=pct(n_rev, len(nz)),
               persisted_pct_of_initial_nonzero=pct(n_per, len(nz)),
               final_zero_pct_of_initial_nonzero=pct(n_fz, len(nz)),
               reversal_pct_of_all_valid=pct(n_rev, len(both)),
               persisted_pct_of_all_valid=pct(n_per, len(both)))
    for side in ("UP", "DOWN"):
        sub = [r for r in nz if r["initial_direction"] == side]
        out[f"n_initial_{side.lower()}"] = len(sub)
        out[f"reversal_pct_given_initial_{side.lower()}"] = pct(sum(1 for r in sub if r["outcome"] == "REVERSAL"), len(sub))
    return out


# --------------------------------------------------------------------------- #
# Вывод файлов
# --------------------------------------------------------------------------- #
def write_csv(path, fields, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


def reaction_out(r):
    o = dict(r)
    for k in ("baseline_mid_close", "horizon_mid_close", "change_usd", "mfe_usd", "mae_usd", "baseline_spread_usd"):
        o[k] = rnd(r[k], 6)
    o["change_pct"] = rnd(r["change_pct"], 8)
    o["excursion_window_complete"] = "TRUE" if r["excursion_window_complete"] else "FALSE"
    return o


EVENT_FIELDS = ["event_datetime_utc", "bundle_composition", "component_count", "horizon_min",
                "baseline_bar_utc", "baseline_mid_close", "horizon_bar_utc", "horizon_mid_close",
                "change_usd", "change_pct", "mfe_usd", "mae_usd", "excursion_window_complete",
                "initial_direction", "final_direction", "reversal", "status", "baseline_spread_usd"]

STAT_FIELDS = (["group_type", "group_name", "horizon_min", "n_events_in_cohort", "n_valid_price_windows",
                "n_missing_windows", "n_up", "n_down", "n_zero", "up_raw_pct", "down_raw_pct", "zero_pct",
                "mean_change_usd", "median_change_usd", "std_change_usd", "mean_absolute_change_usd",
                "n_valid_excursion_windows", "mean_mfe_usd", "mean_mae_usd", "median_mfe_usd", "median_mae_usd",
                "mean_adverse_excursion_usd", "n_initial_nonzero", "n_reversal", "n_persisted",
                "reversal_pct", "direction_persisted_pct", "p_value_up_vs_down_binomial", "sample_warning"]
               + [f"{s}_pct_thr_{t:.2f}" for t in FLAT_THRESHOLDS for s in ("up", "down", "flat")])

REV_FIELDS = ["group_type", "group_name", "horizon_min", "n_events_in_cohort", "n_valid_initial_and_final",
              "n_initial_zero", "n_initial_nonzero", "n_reversal", "n_persisted",
              "n_final_zero_after_nonzero_initial", "reversal_pct_of_initial_nonzero",
              "persisted_pct_of_initial_nonzero", "final_zero_pct_of_initial_nonzero",
              "reversal_pct_of_all_valid", "persisted_pct_of_all_valid",
              "n_initial_up", "reversal_pct_given_initial_up", "n_initial_down", "reversal_pct_given_initial_down"]


def build_groups(events, reactions, full_size):
    """(type, name) -> {'events': [...], 'rows': {h: [...]}}. Одно событие -- одна строка на горизонт."""
    by_event = defaultdict(list)
    for r in reactions:
        by_event[r["event_epoch"]].append(r)
    groups = {}

    def keys(ev):
        ks = [("ALL", "ALL_BUNDLES"), ("COMPOSITION", ev["composition"]),
              ("COMPONENT_COUNT", str(ev["component_count"]))]
        if ev["component_count"] == full_size:
            ks.append(("FULL_BUNDLE", f"FULL_BUNDLE_{full_size}"))
        return ks

    for ev in events:
        for k in keys(ev):
            g = groups.setdefault(k, {"events": [], "rows": {h: [] for h in HORIZONS}})
            g["events"].append(ev)
            for r in by_event[ev["epoch"]]:
                g["rows"][r["horizon_min"]].append(r)
    return groups


def order_groups(groups):
    order = {"ALL": 0, "FULL_BUNDLE": 1, "COMPONENT_COUNT": 2, "COMPOSITION": 3}

    def key(k):
        t, n = k
        if t == "COMPONENT_COUNT":
            return (order[t], int(n) if n.lstrip("-").isdigit() else 0, n)
        if t == "COMPOSITION":
            return (order[t], -len(groups[k]["events"]), n)
        return (order[t], 0, n)
    return sorted(groups, key=key)


# --------------------------------------------------------------------------- #
# Отчёт
# --------------------------------------------------------------------------- #
def f(x, nd=3, plus=False):
    if x is None:
        return "-"
    return f"{x:+.{nd}f}" if plus else f"{x:.{nd}f}"


def build_report(ctx):
    S = ctx["stats_index"]
    L = []
    add = L.append
    bar = "=" * 100
    add(bar)
    add("XAUUSD: РЕАКЦИЯ НА ПАКЕТЫ ЭКОНОМИЧЕСКИХ ПУБЛИКАЦИЙ -- ЭТАП B")
    add(f"версия скрипта {VERSION}; запуск {ctx['started_utc']}; режим времени свечи: {ctx['mode']}")
    add(bar)

    sem = ctx["semantic"]
    add("\n0. ПРОВЕРКА ТЕМПОРАЛЬНОЙ СЕМАНТИКИ M1 (timestamp = начало свечи?)")
    add(f"   Принятая модель: --bar-time-mode {ctx['mode']}. Baseline = mid_close свечи T-1m; close горизонта h = свеча T+(h-1)m.")
    add(f"   Эмпирическая проверка по {sem['n_events_used']} событиям (средний диапазон high-low свечи по смещению метки от T):")
    if "mean_range_usd_by_offset" in sem:
        add("   смещение метки, мин : " + "  ".join(f"{o:>+7d}" for o in SEM_OFFSETS))
        add("   средний range, USD  : " + "  ".join(f"{sem['mean_range_usd_by_offset'][str(o)]:>7.3f}" for o in SEM_OFFSETS))
        add("   доля событий, где максимум range на смещении: " +
            ", ".join(f"{o}m: {v*100:.0f}%" for o, v in sem["share_events_max_range_at_offset"].items()))
    add(f"   ВЕРДИКТ: {sem['verdict']} -- {sem['reason']}")
    if sem["verdict"] == "START_CONFIRMED":
        add("   Данные согласуются с семантикой «timestamp = начало свечи» (косвенное подтверждение, не доказательство).")
    else:
        add("   ВНИМАНИЕ: семантика timestamp ДАННЫМИ НЕ ПОДТВЕРЖДЕНА; результаты опираются на допущение.")
    if sem.get("conflict_with_configured_mode"):
        add("   !!! ЭМПИРИКА ПРОТИВОРЕЧИТ ВЫБРАННОМУ --bar-time-mode. Проверьте режим и перезапустите.")

    add("\n1-3. ОБЪЁМ ОБРАБОТКИ")
    add(f"   Строк в release_events.csv: {ctx['n_event_rows']}; уникальных событий обработано: {ctx['n_events']}")
    add(f"   События хотя бы с одним рассчитанным горизонтом: {ctx['n_events_any_valid']}")
    add(f"   События с ПОЛНЫМИ ценовыми окнами на всех 6 горизонтах: {ctx['n_events_all_valid']}")
    add(f"   События с полными окнами MFE/MAE на всех горизонтах: {ctx['n_events_all_exc']}")
    add(f"   События полного пакета ({ctx['full_size']} показателей): {ctx['n_full']}")
    if ctx["full_first"]:
        add("   Первые события полного пакета: " + ", ".join(ctx["full_first"]))
    add(f"   Различных составов пакета: {ctx['n_compositions']}")

    def table(title, cols):
        add(f"\n{title}")
        add("   " + "".join(f"{c[0]:>{c[1]}}" for c in cols))
        for h in HORIZONS:
            r = S.get(("ALL", "ALL_BUNDLES", h))
            if r:
                add("   " + "".join(f"{c[2](r, h):>{c[1]}}" for c in cols))

    table("4. ВЕРОЯТНОСТИ (ALL_BUNDLES; доли от n_valid_price_windows; ZERO = точный 0)", [
        ("гориз.", 8, lambda r, h: f"+{h}m"), ("n_events", 10, lambda r, h: r["n_events_in_cohort"]),
        ("n_valid", 9, lambda r, h: r["n_valid_price_windows"]),
        ("P(UP)%", 9, lambda r, h: f(r["up_raw_pct"], 2)), ("P(DOWN)%", 10, lambda r, h: f(r["down_raw_pct"], 2)),
        ("P(ZERO)%", 10, lambda r, h: f(r["zero_pct"], 2)),
        ("p-value UP/DOWN", 17, lambda r, h: f(r["p_value_up_vs_down_binomial"], 4))])
    add("   p-value -- точный двусторонний биномиальный тест (H0: P(UP)=P(DOWN)) на нулевых изменениях исключённых.")
    add("   Групп и горизонтов много: без поправки на множественные сравнения отдельные малые p-value ничего не доказывают.")

    add("\n   Чувствительность к определению FLAT (ALL_BUNDLES, фиксированные пороги USD):")
    add("   " + f"{'гориз.':>8}" + "".join(f"{'UP/DOWN/FLAT% @'+format(t, '.2f'):>26}" for t in FLAT_THRESHOLDS))
    for h in HORIZONS:
        r = S.get(("ALL", "ALL_BUNDLES", h))
        if r:
            add("   " + f"{'+'+str(h)+'m':>8}" + "".join(
                f"{f(r[f'up_pct_thr_{t:.2f}'],1)+'/'+f(r[f'down_pct_thr_{t:.2f}'],1)+'/'+f(r[f'flat_pct_thr_{t:.2f}'],1):>26}"
                for t in FLAT_THRESHOLDS))

    table("5. ИЗМЕНЕНИЕ XAUUSD (USD, относительно baseline T-1m)", [
        ("гориз.", 8, lambda r, h: f"+{h}m"), ("mean", 10, lambda r, h: f(r["mean_change_usd"], 3, True)),
        ("median", 10, lambda r, h: f(r["median_change_usd"], 3, True)),
        ("mean |chg|", 12, lambda r, h: f(r["mean_absolute_change_usd"], 3)),
        ("std", 9, lambda r, h: f(r["std_change_usd"], 3))])

    table("6. MFE / MAE (USD, только полные окна; MAE <= 0)", [
        ("гориз.", 8, lambda r, h: f"+{h}m"), ("n_exc_win", 10, lambda r, h: r["n_valid_excursion_windows"]),
        ("mean MFE", 10, lambda r, h: f(r["mean_mfe_usd"], 3, True)),
        ("median MFE", 12, lambda r, h: f(r["median_mfe_usd"], 3, True)),
        ("mean MAE", 10, lambda r, h: f(r["mean_mae_usd"], 3, True)),
        ("median MAE", 12, lambda r, h: f(r["median_mae_usd"], 3, True))])

    table("7. РАЗВОРОТЫ (знаменатель -- события с НЕНУЛЕВЫМ направлением на +1m; +1m тривиально 0%)", [
        ("гориз.", 8, lambda r, h: f"+{h}m"), ("n_init_nz", 11, lambda r, h: r["n_initial_nonzero"]),
        ("reversal%", 11, lambda r, h: f(r["reversal_pct"], 2)),
        ("persisted%", 12, lambda r, h: f(r["direction_persisted_pct"], 2))])
    add("   Если на горизонте итог ровно 0 при ненулевом старте, событие не считается ни разворотом, ни сохранением "
        "(см. reversal_statistics.csv).")

    add("\n8. СРАВНЕНИЕ СОСТАВОВ ПАКЕТА (топ по числу событий; полная таблица -- composition_statistics.csv)")
    for t, name in ctx["full_groups"]:
        pass
    for k in ctx["ordered_groups"]:
        if k[0] == "COMPONENT_COUNT" or k[0] == "FULL_BUNDLE":
            ev = ctx["groups"][k]["events"]
            s15, s60 = S.get((k[0], k[1], 15)), S.get((k[0], k[1], 60))
            lab = f"{k[0]}={k[1]}"
            add(f"   {lab:<28} n={len(ev):>4} | +15m: n={s15['n_valid_price_windows']:>4} UP={f(s15['up_raw_pct'],1):>6}% "
                f"mean={f(s15['mean_change_usd'],3,True):>8} med={f(s15['median_change_usd'],3,True):>8} "
                f"| +60m: n={s60['n_valid_price_windows']:>4} UP={f(s60['up_raw_pct'],1):>6}% "
                f"mean={f(s60['mean_change_usd'],3,True):>8} med={f(s60['median_change_usd'],3,True):>8}")
    shown = 0
    for k in ctx["ordered_groups"]:
        if k[0] != "COMPOSITION":
            continue
        if shown >= 30:
            add(f"   ... ещё составов: {ctx['n_compositions'] - shown} (см. composition_statistics.csv)")
            break
        shown += 1
        ev = ctx["groups"][k]["events"]
        s15, s60 = S[("COMPOSITION", k[1], 15)], S[("COMPOSITION", k[1], 60)]
        add(f"   [{shown}] {k[1]}")
        add(f"        n={len(ev)}, компонентов={ev[0]['component_count']}, первое {fmt_ts(ev[0]['epoch'])[:10]}, "
            f"последнее {fmt_ts(ev[-1]['epoch'])[:10]}  [{s15['sample_warning']}]")
        add(f"        +15m: n={s15['n_valid_price_windows']} UP={f(s15['up_raw_pct'],1)}% DOWN={f(s15['down_raw_pct'],1)}% "
            f"mean={f(s15['mean_change_usd'],3,True)} median={f(s15['median_change_usd'],3,True)} | "
            f"+60m: n={s60['n_valid_price_windows']} UP={f(s60['up_raw_pct'],1)}% DOWN={f(s60['down_raw_pct'],1)}% "
            f"mean={f(s60['mean_change_usd'],3,True)} median={f(s60['median_change_usd'],3,True)}")

    add("\n9. КРУПНЕЙШИЕ ПРОБЛЕМЫ КАЧЕСТВА ДАННЫХ (по числу; детали -- data_quality.csv)")
    if ctx["dq_counts"]:
        for cat, cnt in ctx["dq_counts"].most_common(12):
            add(f"   {cnt:>10}  {ctx['dq_sev'].get(cat, ''):<7} {cat}")
    else:
        add("   проблем не обнаружено")
    add(f"   Свечей BID/ASK, совпавших по timestamp (в окнах событий): {ctx['n_mid']}; "
        f"только BID: {ctx['only_bid']}; только ASK: {ctx['only_ask']}")
    add(f"   Spread (ask_close - bid_close): медиана {f(ctx['spread_median'],3)}, p99 {f(ctx['spread_p99'],3)}, "
        f"макс {f(ctx['spread_max'],3)}; отрицательных {ctx['neg_spread']}, > {ctx['spread_thr']} USD: {ctx['big_spread']}")

    add("\n10. ОГРАНИЧЕНИЯ")
    for line in [
        f"Размер выборки: полный пакет -- {ctx['n_full']} событий; малые группы (см. sample_warning) не позволяют уверенных выводов.",
        "Вероятности -- эмпирические доли в прошлой выборке, не прогноз; доверительные интервалы здесь не строились.",
        "Реакция измеряется от закрытия T-1m: ожидание рынка и движение до публикации не учитываются, "
        "а движение за первую минуту включает также спред/проскальзывание, отражённое в mid лишь частично.",
        f"События с пересекающимися окнами (<{MAX_H} мин между публикациями): {ctx['n_overlap']} -- их реакции не независимы.",
        "Не разделялись сюрприз (факт vs прогноз), направление отчёта, режим волатильности и другие факторы; "
        "этап B описывает безусловную реакцию по составу пакета.",
        "UP/DOWN/ZERO -- по точному знаку изменения; ZERO редок, поэтому смотрите пороговую (flat) чувствительность.",
        "Диагностика дублей/некорректных цен/spread выполнена только по свечам в окнах событий (не по всему архиву).",
        "Определение файлов по диапазону дат делается по первой/последней строке файла (имя файла не используется); "
        "для неотсортированных CSV используйте --no-prefilter.",
        "Праздники не определяются по календарю: они видны как отсутствие свечей (NO_QUOTES_IN_EVENT_WINDOW / "
        "MISSING_BASELINE); выходные помечены по дню недели UTC.",
        "Множественные сравнения: групп×горизонтов много, отдельные «значимые» p-value без поправки ничего не доказывают.",
    ]:
        add("   - " + line)
    if sem["verdict"] != "START_CONFIRMED":
        add("   - Семантика timestamp M1 данными не подтверждена (см. раздел 0).")
    add("\n" + bar)
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(description="Stage B: XAUUSD Release Bundle Reaction Analyzer")
    p.add_argument("--stage-a", required=True, help="каталог этапа A (или прямой путь к release_events.csv)")
    p.add_argument("--bid-root", required=True)
    p.add_argument("--ask-root", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--bar-time-mode", choices=("start", "end"), default="start",
                   help="start: timestamp = начало M1-свечи (по умолчанию); end: конец свечи")
    p.add_argument("--events-file", default="release_events.csv")
    p.add_argument("--full-bundle-size", type=int, default=8)
    p.add_argument("--spread-anomaly-usd", type=float, default=3.0, help="порог аномального spread, USD")
    p.add_argument("--min-sample", type=int, default=30, help="порог метки LOW_SAMPLE")
    p.add_argument("--max-quality-rows", type=int, default=5000, help="лимит строк на категорию в data_quality.csv")
    p.add_argument("--no-prefilter", action="store_true", help="не отбирать файлы по диапазону, читать все")
    p.add_argument("--log-level", default="INFO")
    return p


def run(args):
    t_start = time.time()
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout, force=True)
    fh = logging.FileHandler(out_dir / "run.log", mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fh)

    shift = 60 if args.bar_time_mode == "end" else 0
    dq = DQ(args.max_quality_rows)

    ev_path = args.stage_a if os.path.isfile(args.stage_a) else os.path.join(args.stage_a, args.events_file)
    log.info("Загрузка событий: %s", ev_path)
    events, n_event_rows = load_events(ev_path, dq)
    if not events:
        raise InputError("Нет пригодных событий")
    log.info("Событий: строк %d, уникальных %d (%s .. %s)", n_event_rows, len(events),
             fmt_ts(events[0]["epoch"]), fmt_ts(events[-1]["epoch"]))

    needed = Merged([[e["epoch"] - PRE_BARS * 60 + shift, e["epoch"] + (MAX_H - 1) * 60 + shift] for e in events])
    log.info("Нужных интервалов котировок после слияния: %d", len(needed))

    bid, ask = SideStore("BID"), SideStore("ASK")
    for st, root in ((bid, args.bid_root), (ask, args.ask_root)):
        files = find_csvs(root)
        log.info("%s: найдено CSV: %d в %s", st.name, len(files), root)
        read_side(files, st, needed, shift, args.no_prefilter, dq)
        if not st.bars:
            raise InputError(f"{st.name}: в окнах событий не найдено ни одной валидной свечи")

    mid = build_mid(bid, ask)
    only_bid = set(bid.bars) - set(ask.bars)
    only_ask = set(ask.bars) - set(bid.bars)
    log.info("MID-свечей: %d; только BID: %d; только ASK: %d", len(mid), len(only_bid), len(only_ask))

    # --- диагностика котировок ---
    for st in (bid, ask):
        dq.add("QUOTE_FILES_SUMMARY", "INFO", "GLOBAL", side=st.name,
               detail=f"файлов {st.files_total}, прочитано {st.files_read}, пропущено {st.files_skipped}, "
                      f"строк {st.rows_read}, в окнах событий {st.rows_in_window}, свечей {len(st.bars)}", count=0)
        if st.dup_identical:
            dq.add("DUPLICATE_TIMESTAMP_IDENTICAL", "LOW", "GLOBAL", side=st.name, count=st.dup_identical,
                   detail="повтор свечи с идентичными значениями (оставлена одна)")
        if st.dup_conflict:
            dq.add("DUPLICATE_TIMESTAMP_CONFLICT", "HIGH", "GLOBAL", side=st.name, count=st.dup_conflict,
                   detail="повтор timestamp с разными значениями -- свеча исключена")
        for rsn, c in st.invalid_reasons.items():
            dq.add("INVALID_PRICE_ROW", "HIGH", "GLOBAL", side=st.name, count=c, detail=rsn)
        if st.unparseable:
            dq.add("UNPARSEABLE_TIMESTAMP_ROW", "MEDIUM", "GLOBAL", side=st.name, count=st.unparseable)
        if st.off_minute:
            dq.add("TIMESTAMP_NOT_ON_MINUTE", "MEDIUM", "GLOBAL", side=st.name, count=st.off_minute,
                   detail="строки с секундами != 0 пропущены")
    for side, keys, other in (("BID", only_bid, "ASK"), ("ASK", only_ask, "BID")):
        for k in sorted(keys):
            dq.add("UNMATCHED_TIMESTAMP", "MEDIUM", "TIMESTAMP", ts=k, side=side,
                   detail=f"есть {side}, нет {other} (MID не создаётся)")
    spreads = [v[4] for v in mid.values()]
    neg = [k for k, v in mid.items() if v[4] < 0]
    big = [k for k, v in mid.items() if v[4] > args.spread_anomaly_usd]
    for k in neg:
        dq.add("NEGATIVE_SPREAD", "MEDIUM", "TIMESTAMP", ts=k, side="MID", detail=f"spread={mid[k][4]:.4f}")
    for k in big:
        dq.add("ANOMALOUS_SPREAD", "MEDIUM", "TIMESTAMP", ts=k, side="MID",
               detail=f"spread={mid[k][4]:.4f} > {args.spread_anomaly_usd}")
    sp_sorted = sorted(spreads)
    cov_vals = [(s.cov_min, s.cov_max) for s in (bid, ask) if s.cov_min is not None]
    cov = (max(c[0] for c in cov_vals), min(c[1] for c in cov_vals)) if cov_vals else (None, None)

    # --- расчёт реакции ---
    log.info("Расчёт реакций по %d событиям × %d горизонтам", len(events), len(HORIZONS))
    reactions, per_event, sem_samples = process_events(events, mid, make_missing_reason(bid, ask), shift, dq, cov)
    semantic = semantic_check(sem_samples, args.bar_time_mode)
    log.info("Семантика timestamp: %s (%s)", semantic["verdict"], semantic["reason"])
    if semantic.get("conflict_with_configured_mode"):
        log.warning("Эмпирика противоречит --bar-time-mode %s!", args.bar_time_mode)

    # --- статистика ---
    groups = build_groups(events, reactions, args.full_bundle_size)
    ordered = order_groups(groups)
    stat_rows, rev_rows, comp_rows = [], [], []
    stats_index = {}
    prob_fail = 0
    for k in ordered:
        g = groups[k]
        for h in HORIZONS:
            s = compute_stats(g["rows"][h], len(g["events"]), args.min_sample)
            if s["n_valid_price_windows"]:
                tot = s["up_raw_pct"] + s["down_raw_pct"] + s["zero_pct"]
                if abs(tot - 100.0) > 0.001:
                    prob_fail += 1
                    log.warning("Сумма вероятностей %.4f != 100 для %s/%s/%s", tot, k[0], k[1], h)
            row = {"group_type": k[0], "group_name": k[1], "horizon_min": h, **s}
            stat_rows.append(row)
            stats_index[(k[0], k[1], h)] = row
            rev_rows.append({"group_type": k[0], "group_name": k[1], "horizon_min": h,
                             **compute_reversal(g["rows"][h], len(g["events"]))})
            if k[0] == "COMPOSITION":
                ev = g["events"]
                comp_rows.append({**row, "component_count": ev[0]["component_count"],
                                  "is_full_bundle": "TRUE" if ev[0]["component_count"] == args.full_bundle_size else "FALSE",
                                  "n_events_total": len(ev), "first_event_utc": fmt_ts(ev[0]["epoch"]),
                                  "last_event_utc": fmt_ts(ev[-1]["epoch"])})

    # --- перекрытие окон ---
    times = [e["epoch"] for e in events]
    overlap = set()
    for a, b in zip(times, times[1:]):
        if b - a < MAX_H * 60:
            overlap.update((a, b))
    full_events = [e for e in events if e["component_count"] == args.full_bundle_size]

    # --- запись ---
    dq.finalize()
    write_csv(out_dir / "event_reactions.csv", EVENT_FIELDS, [reaction_out(r) for r in reactions])
    write_csv(out_dir / "bundle_statistics.csv", STAT_FIELDS, stat_rows)
    write_csv(out_dir / "composition_statistics.csv",
              STAT_FIELDS[:2] + ["component_count", "is_full_bundle", "n_events_total", "first_event_utc",
                                 "last_event_utc"] + STAT_FIELDS[2:], comp_rows)
    write_csv(out_dir / "reversal_statistics.csv", REV_FIELDS, rev_rows)
    write_csv(out_dir / "data_quality.csv", DQ_FIELDS, dq.rows)

    n_any = sum(1 for p in per_event if p["n_valid"] > 0)
    n_all = sum(1 for p in per_event if p["n_valid"] == len(HORIZONS))
    n_all_exc = sum(1 for p in per_event if p["n_exc"] == len(HORIZONS))
    ctx = {
        "started_utc": started, "mode": args.bar_time_mode, "semantic": semantic,
        "n_event_rows": n_event_rows, "n_events": len(events), "n_events_any_valid": n_any,
        "n_events_all_valid": n_all, "n_events_all_exc": n_all_exc, "n_full": len(full_events),
        "full_size": args.full_bundle_size,
        "full_first": [fmt_ts(e["epoch"])[:16] for e in full_events[:10]],
        "n_compositions": sum(1 for k in groups if k[0] == "COMPOSITION"),
        "stats_index": stats_index, "groups": groups, "ordered_groups": ordered, "full_groups": [],
        "dq_counts": Counter({c: n for c, n in dq.counts.items() if n > 0 and c != "QUOTE_FILES_SUMMARY"}),
        "dq_sev": dq.severity, "n_mid": len(mid), "only_bid": len(only_bid), "only_ask": len(only_ask),
        "spread_median": statistics.median(spreads) if spreads else None,
        "spread_p99": sp_sorted[int(0.99 * (len(sp_sorted) - 1))] if sp_sorted else None,
        "spread_max": sp_sorted[-1] if sp_sorted else None,
        "neg_spread": len(neg), "big_spread": len(big), "spread_thr": args.spread_anomaly_usd,
        "n_overlap": len(overlap),
    }
    (out_dir / "report.txt").write_text(build_report(ctx), encoding="utf-8")

    summary = {
        "script_version": VERSION, "started_utc": started, "elapsed_sec": round(time.time() - t_start, 2),
        "python": platform.python_version(), "platform": platform.platform(),
        "parameters": {k: v for k, v in vars(args).items()}, "horizons_min": list(HORIZONS),
        "flat_thresholds_usd": list(FLAT_THRESHOLDS),
        "events": {"rows_in_stage_a": n_event_rows, "unique_processed": len(events),
                   "any_horizon_valid": n_any, "all_horizons_valid": n_all, "all_excursion_windows_complete": n_all_exc,
                   "full_bundle_events": len(full_events), "events_with_overlapping_windows": len(overlap),
                   "compositions": ctx["n_compositions"]},
        "quotes": {s.name: {"files_total": s.files_total, "files_read": s.files_read, "files_skipped": s.files_skipped,
                            "rows_read": s.rows_read, "rows_in_event_windows": s.rows_in_window,
                            "bars_kept": len(s.bars), "duplicates_identical": s.dup_identical,
                            "duplicates_conflict": s.dup_conflict, "invalid_rows": dict(s.invalid_reasons),
                            "unparseable_rows": s.unparseable, "off_minute_rows": s.off_minute,
                            "coverage": [fmt_ts(int(s.cov_min)) if s.cov_min is not None else None,
                                         fmt_ts(int(s.cov_max)) if s.cov_max is not None else None]}
                   for s in (bid, ask)},
        "mid_bars_in_event_windows": len(mid), "unmatched_bid_only": len(only_bid), "unmatched_ask_only": len(only_ask),
        "reaction_rows": len(reactions), "valid_price_windows": sum(1 for r in reactions if r["valid_price"]),
        "missing_price_windows": sum(1 for r in reactions if not r["valid_price"]),
        "probability_sum_check_failures": prob_fail,
        "timestamp_semantics_check": semantic,
        "data_quality_counts": dict(dq.counts),
    }
    (out_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("=" * 70)
    log.info("ГОТОВО за %.1f c. Событий: %d (полных пакетов: %d); горизонтов-строк: %d (валидных %d)",
             time.time() - t_start, len(events), len(full_events), len(reactions), summary["valid_price_windows"])
    log.info("Семантика timestamp: %s; нарушений суммы вероятностей: %d", semantic["verdict"], prob_fail)
    log.info("Результаты: %s", out_dir.resolve())
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except InputError as exc:
        logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
        log.error("Ошибка входных данных: %s", exc)
        return 2
    except KeyboardInterrupt:
        log.error("Прервано пользователем")
        return 130
    except Exception:  # noqa: BLE001
        log.exception("Непредвиденная ошибка")
        return 1


if __name__ == "__main__":
    sys.exit(main())
