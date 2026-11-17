"""\
Retail Calendar Builder
======================

Собирает только американские релизы:
  * Core Retail Sales m/m — основная серия;
  * Retail Sales m/m — сопутствующий показатель из той же публикации.

Поддерживает:
  1) Forex Factory CSV с заголовком DateTime,Currency,Impact,Event,...;
  2) выгрузку без заголовка с 21 колонкой (scraper export);
  3) уже отфильтрованный исторический CSV с колонками date/datetime_utc/actual/...

Никакие исходные файлы не изменяются. Результат записывается в папку retail рядом со скриптом.
Требуется Python 3.9+. На Windows при отсутствии часовых поясов:
    py -m pip install tzdata
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# =============================================================================
# 1. НАСТРОЙКИ — ПРОПИШИ СВОИ ПУТИ К ФАЙЛАМ ЗДЕСЬ
# =============================================================================

# Основные календарные файлы. Можно добавить несколько источников.
# Поддерживаются CSV с заголовком (8 колонок) и scraper-файл без заголовка (21 колонка).
CALENDAR_FILES = [
    Path(r"E:\work\XAU_gold\mvp_tg_bot\untitled\forexfactory-scraper\forex_factory_cache.csv"), 
    Path(r"E:\work\XAU_gold\mvp_tg_bot\untitled\data\forex_factory_cache.csv"), #This need to change path of forex_factory_cache.csv
]

# Необязательный уже отфильтрованный исторический файл Core Retail Sales,
# например core_retail_events_2007_plus.csv. Оставь None, если не нужен.
CORE_HISTORY_FILE: Optional[Path] = None
# Пример отключения:
# CORE_HISTORY_FILE = None

#Пример включения:
#Path(
#    r"E:\work\XAU_gold\XAUUSD\data\calendar\YOUR_CORE_RETAIL_HISTORY.csv"
#)

# Валюта и точные имена событий.
TARGET_CURRENCY = "USD"
CORE_EVENT_NAME = "Core Retail Sales m/m"
COMPANION_EVENT_NAME = "Retail Sales m/m"

# Официальное время этих двух американских показателей — 08:30 по Нью-Йорку.
# Дата берётся из календаря; часовой пояс и DST обрабатываются автоматически.
RELEASE_TIME_NEW_YORK = time(8, 30)
NEW_YORK_TZ_NAME = "America/New_York"
LOCAL_TZ_NAME = "Europe/Chisinau"

# В 21-колоночной выгрузке scraper время в DateTime/time выглядит как местное
# время Europe/Chisinau, но ISO-суффикс ошибочно указан как +00:00.
# Поэтому для такого формата используем отдельные date + time и трактуем их
# как Europe/Chisinau. НЕ меняй на UTC, пока формат этого источника не изменится.
SCRAPER_21COL_TIMEZONE = LOCAL_TZ_NAME

# Для обычных CSV с DateTime без часового пояса.
# Если в DateTime уже есть +03:30, +04:30, +00:00 и т.п., явный offset сохраняется.
NAIVE_DATETIME_TIMEZONE = LOCAL_TZ_NAME

# Имя папки и результирующих файлов.
OUTPUT_DIR_NAME = "retail"
OUTPUT_CSV_NAME = "us_core_retail_sales.csv"
OUTPUT_QA_NAME = "retail_validation.txt"
OUTPUT_REJECTS_NAME = "retail_rejected_rows.csv"
OUTPUT_README_NAME = "README.txt"

# =============================================================================
# 2. ЧАСОВЫЕ ПОЯСА
# =============================================================================

try:
    NEW_YORK_TZ = ZoneInfo(NEW_YORK_TZ_NAME)
    LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)
    SCRAPER_TZ = ZoneInfo(SCRAPER_21COL_TIMEZONE)
    NAIVE_TZ = ZoneInfo(NAIVE_DATETIME_TIMEZONE)
except ZoneInfoNotFoundError as exc:
    raise SystemExit(
        "Не найдена база часовых поясов. На Windows выполни: py -m pip install tzdata"
    ) from exc

UTC = timezone.utc


# =============================================================================
# 3. СЛУЖЕБНЫЕ ФУНКЦИИ
# =============================================================================


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\ufeff", "").strip()


def norm_name(value: object) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).casefold()


def parse_date(value: object) -> Optional[date]:
    """Читает YYYY-MM-DD либо ISO datetime, возвращает календарную дату."""
    s = clean_text(value)
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.date()
    except ValueError:
        return None


def parse_time(value: object) -> Optional[time]:
    s = clean_text(value)
    if not s:
        return None
    try:
        # Принимает HH:MM и HH:MM:SS.
        return time.fromisoformat(s)
    except ValueError:
        return None


def parse_iso_datetime(value: object) -> Optional[datetime]:
    s = clean_text(value)
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_numeric(value: object) -> Optional[Decimal]:
    """Преобразует 0.4%, -0.2% и числовые строки в Decimal."""
    s = clean_text(value)
    if not s or s.lower() in {"nan", "none", "null", "-", "—", "n/a"}:
        return None
    s = s.replace("%", "").replace(",", "").replace(" ", "")
    match = re.match(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$", s)
    if not match:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def dec_to_str(value: Optional[Decimal]) -> str:
    if value is None:
        return ""
    return format(value, "f")


def is_header_row(row: Sequence[str]) -> bool:
    names = {norm_name(x) for x in row}
    return "currency" in names and "event" in names and any(
        x in names for x in ("datetime", "date", "event date")
    )


def header_index(header: Sequence[str], *candidates: str) -> Optional[int]:
    """Ищет первое вхождение имени колонки, без учёта регистра."""
    normalized = [norm_name(x) for x in header]
    for candidate in candidates:
        key = norm_name(candidate)
        for idx, name in enumerate(normalized):
            if name == key:
                return idx
    return None


def get_cell(row: Sequence[str], idx: Optional[int]) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return clean_text(row[idx])


def scheduled_datetimes(release_date: date) -> Tuple[datetime, datetime]:
    """Возвращает (UTC, Europe/Chisinau) из официального 08:30 America/New_York."""
    ny_dt = datetime.combine(release_date, RELEASE_TIME_NEW_YORK).replace(tzinfo=NEW_YORK_TZ)
    utc_dt = ny_dt.astimezone(UTC)
    local_dt = utc_dt.astimezone(LOCAL_TZ)
    return utc_dt, local_dt


def dt_to_iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def try_parse_raw_time_as_new_york(
    raw_datetime: str,
    raw_time_field: str,
    release_date: date,
    source_kind: str,
) -> Tuple[Optional[datetime], str]:
    """Преобразует исходное время в New York для проверки против 08:30.

    Для формата scraper21 берём отдельное поле time и интерпретируем его в
    Europe/Chisinau, игнорируя ошибочный +00:00 в поле DateTime.
    Для старых CSV уважаем явный ISO offset.
    """
    # Новый scraper: DateTime и time в нём содержат местное время Молдовы,
    # хотя у DateTime в ISO-строке стоит +00:00.
    if source_kind == "scraper21":
        clock = parse_time(raw_time_field)
        if clock is None:
            parsed = parse_iso_datetime(raw_datetime)
            if parsed is not None:
                clock = parsed.time().replace(tzinfo=None)
        if clock is None or clock == time(0, 0):
            return None, "scraper_time_missing_or_00:00"
        local_dt = datetime.combine(release_date, clock).replace(tzinfo=SCRAPER_TZ)
        return local_dt.astimezone(NEW_YORK_TZ), "scraper_time_interpreted_as_Europe/Chisinau"

    parsed = parse_iso_datetime(raw_datetime)
    if parsed is None:
        clock = parse_time(raw_time_field)
        if clock is None or clock == time(0, 0):
            return None, "raw_time_missing_or_00:00"
        local_dt = datetime.combine(release_date, clock).replace(tzinfo=NAIVE_TZ)
        return local_dt.astimezone(NEW_YORK_TZ), "naive_DateTime_interpreted_as_configured_timezone"

    if parsed.time().replace(tzinfo=None) == time(0, 0):
        return None, "raw_time_00:00_all_day_or_tentative"

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NAIVE_TZ)
        interpretation = "naive_DateTime_interpreted_as_configured_timezone"
    else:
        interpretation = "explicit_ISO_offset_used"
    return parsed.astimezone(NEW_YORK_TZ), interpretation


def parse_provided_utc(value: str) -> Optional[datetime]:
    """Читает уже рассчитанное datetime_utc из подготовленного исторического файла."""
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# =============================================================================
# 4. ЧТЕНИЕ РАЗНЫХ ФОРМАТОВ CSV
# =============================================================================

@dataclass
class SourceEvent:
    release_date: date
    currency: str
    event_name: str
    impact: str
    actual_raw: str
    forecast_raw: str
    previous_raw: str
    raw_datetime: str
    explicit_date: str
    raw_time_field: str
    provided_datetime_utc: str
    source_kind: str
    source_file: str
    source_provider: str
    source_url: str
    date_check: str
    raw_time_check: str
    raw_time_interpretation: str
    raw_datetime_new_york: str
    raw_datetime_utc: str
    provided_utc_check: str
    row_number: int


def extract_source_records(path: Path, rejected: List[Dict[str, str]]) -> List[SourceEvent]:
    """Читает один источник, выбирая только USD Core/Headline Retail Sales."""
    records: List[SourceEvent] = []
    if not path.exists():
        print(f"[SKIP] Файл не найден: {path}")
        return records

    rows: List[List[str]] = []
    used_encoding = ""
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                rows = list(csv.reader(f, delimiter=","))
            used_encoding = encoding
            break
        except UnicodeDecodeError:
            continue
        except csv.Error as exc:
            rejected.append({
                "source_file": str(path),
                "row_number": "",
                "reason": f"CSV parse error: {exc}",
                "raw_row": "",
            })
            return records

    if not used_encoding:
        rejected.append({
            "source_file": str(path),
            "row_number": "",
            "reason": "Не удалось определить кодировку",
            "raw_row": "",
        })
        return records

    if not rows:
        print(f"[EMPTY] {path.name}")
        return records

    header: Optional[List[str]] = rows[0] if is_header_row(rows[0]) else None
    start_index = 1 if header is not None else 0

    if header is not None:
        idx_dt = header_index(header, "DateTime", "datetime", "event_datetime")
        idx_currency = header_index(header, "Currency")
        idx_impact = header_index(header, "Impact")
        idx_event = header_index(header, "Event", "event_name")
        idx_actual = header_index(header, "Actual")
        idx_forecast = header_index(header, "Forecast")
        idx_previous = header_index(header, "Previous")
        idx_date = header_index(header, "date", "release_date", "event_date")
        idx_time = header_index(header, "time", "release_time")
        idx_utc = header_index(header, "datetime_utc")
        idx_provider = header_index(header, "source", "provider")
        idx_url = header_index(header, "url", "event_url", "source_url")
        source_kind = "processed_history" if idx_utc is not None else "calendar_header"
    else:
        # В выгрузке scraper без заголовка первые 8 полей — исходные данные,
        # 9-е и 10-е поля — отдельные date/time, 19-е — provider, 20/21-е — URL.
        source_kind = "scraper21" if len(rows[0]) >= 18 else "calendar_no_header"
        idx_dt, idx_currency, idx_impact, idx_event = 0, 1, 2, 3
        idx_actual, idx_forecast, idx_previous = 4, 5, 6
        if source_kind == "scraper21":
            idx_date, idx_time, idx_utc = 8, 9, None
            idx_provider, idx_url = 18, 19
        else:
            idx_date, idx_time, idx_utc = None, None, None
            idx_provider, idx_url = None, None

    for row_number, row in enumerate(rows[start_index:], start=start_index + 1):
        if not row or all(not clean_text(c) for c in row):
            continue

        # Если запись не похожа на событие календаря, пропускаем её.
        if max(x for x in [idx_currency, idx_event] if x is not None) >= len(row):
            continue

        currency = get_cell(row, idx_currency).upper()
        event_name = get_cell(row, idx_event)
        event_key = norm_name(event_name)

        if currency != TARGET_CURRENCY or event_key not in {
            norm_name(CORE_EVENT_NAME), norm_name(COMPANION_EVENT_NAME)
        }:
            continue

        raw_datetime = get_cell(row, idx_dt)
        explicit_date = get_cell(row, idx_date)
        raw_time_field = get_cell(row, idx_time)
        provided_utc = get_cell(row, idx_utc)

        prefix_date = parse_date(raw_datetime)
        dedicated_date = parse_date(explicit_date)
        date_check = "OK"
        if prefix_date and dedicated_date and prefix_date != dedicated_date:
            date_check = "DATE_FIELD_MISMATCH"
        release_date = dedicated_date or prefix_date

        if release_date is None:
            rejected.append({
                "source_file": str(path),
                "row_number": str(row_number),
                "reason": "Не удалось извлечь дату целевого Retail-события",
                "raw_row": " | ".join(row),
            })
            continue

        # Проверяем сырое время как отдельный источник.
        raw_ny_dt, interpretation = try_parse_raw_time_as_new_york(
            raw_datetime=raw_datetime,
            raw_time_field=raw_time_field,
            release_date=release_date,
            source_kind=source_kind,
        )
        expected_utc, _ = scheduled_datetimes(release_date)
        provided_dt = parse_provided_utc(provided_utc)

        if raw_ny_dt is not None:
            if raw_ny_dt.date() == release_date and raw_ny_dt.time().replace(tzinfo=None) == RELEASE_TIME_NEW_YORK:
                raw_time_check = "MATCH_08:30_ET"
            else:
                raw_time_check = (
                    "MISMATCH_RAW="
                    + raw_ny_dt.strftime("%Y-%m-%d %H:%M %Z")
                    + "; REBUILT_08:30_ET"
                )
        elif provided_dt is not None:
            raw_time_check = "RAW_TIME_MISSING; CHECKED_PROVIDED_UTC"
        else:
            raw_time_check = "RAW_TIME_MISSING_OR_00:00; REBUILT_08:30_ET"

        if provided_dt is not None:
            provided_utc_check = "MATCH" if provided_dt == expected_utc else "MISMATCH"
        else:
            provided_utc_check = "NOT_PROVIDED"

        raw_ny_iso = dt_to_iso(raw_ny_dt) if raw_ny_dt is not None else ""
        raw_utc_iso = dt_to_iso(raw_ny_dt.astimezone(UTC)) if raw_ny_dt is not None else ""

        records.append(SourceEvent(
            release_date=release_date,
            currency=currency,
            event_name=event_name,
            impact=get_cell(row, idx_impact),
            actual_raw=get_cell(row, idx_actual),
            forecast_raw=get_cell(row, idx_forecast),
            previous_raw=get_cell(row, idx_previous),
            raw_datetime=raw_datetime,
            explicit_date=explicit_date,
            raw_time_field=raw_time_field,
            provided_datetime_utc=provided_utc,
            source_kind=source_kind,
            source_file=str(path),
            source_provider=get_cell(row, idx_provider),
            source_url=get_cell(row, idx_url),
            date_check=date_check,
            raw_time_check=raw_time_check,
            raw_time_interpretation=interpretation,
            raw_datetime_new_york=raw_ny_iso,
            raw_datetime_utc=raw_utc_iso,
            provided_utc_check=provided_utc_check,
            row_number=row_number,
        ))

    print(f"[READ] {path.name}: encoding={used_encoding}, target events={len(records)}")
    return records


# =============================================================================
# 5. ДЕДУПЛИКАЦИЯ И СБОРКА ОСНОВНОЙ СЕРИИ
# =============================================================================


def completeness_score(record: SourceEvent) -> int:
    return sum(bool(x) for x in (record.actual_raw, record.forecast_raw, record.previous_raw))


def deduplicate_events(records: Sequence[SourceEvent]) -> Tuple[Dict[Tuple[date, str], SourceEvent], Dict[Tuple[date, str], str], int]:
    grouped: Dict[Tuple[date, str], List[SourceEvent]] = defaultdict(list)
    for record in records:
        grouped[(record.release_date, norm_name(record.event_name))].append(record)

    chosen: Dict[Tuple[date, str], SourceEvent] = {}
    conflicts: Dict[Tuple[date, str], str] = {}
    duplicate_count = 0

    for key, items in grouped.items():
        if len(items) > 1:
            duplicate_count += len(items) - 1

        # В первую очередь выбираем строку с заполненными Actual/Forecast/Previous.
        # При одинаковой полноте сохраняется порядок файлов в CALENDAR_FILES.
        best = max(enumerate(items), key=lambda pair: (completeness_score(pair[1]), -pair[0]))[1]
        chosen[key] = best

        conflict_parts = []
        for field_name, attr in (
            ("actual", "actual_raw"),
            ("forecast", "forecast_raw"),
            ("previous", "previous_raw"),
        ):
            values = sorted({clean_text(getattr(x, attr)) for x in items if clean_text(getattr(x, attr))})
            if len(values) > 1:
                conflict_parts.append(f"{field_name}={' / '.join(values)}")
        if conflict_parts:
            conflicts[key] = "; ".join(conflict_parts)

    return chosen, conflicts, duplicate_count


def create_output_rows(records: Sequence[SourceEvent]) -> Tuple[List[Dict[str, str]], Dict[str, int], List[str]]:
    core_records = [r for r in records if norm_name(r.event_name) == norm_name(CORE_EVENT_NAME)]
    headline_records = [r for r in records if norm_name(r.event_name) == norm_name(COMPANION_EVENT_NAME)]

    core, core_conflicts, core_duplicates = deduplicate_events(core_records)
    headline, headline_conflicts, headline_duplicates = deduplicate_events(headline_records)

    output: List[Dict[str, str]] = []
    all_time_checks = Counter()
    all_date_checks = Counter()
    all_utc_checks = Counter()
    missing_headline = 0
    conflict_count = 0

    for key in sorted(core.keys(), key=lambda x: x[0]):
        core_event = core[key]
        headline_event = headline.get((core_event.release_date, norm_name(COMPANION_EVENT_NAME)))
        expected_utc, local_dt = scheduled_datetimes(core_event.release_date)

        all_time_checks[core_event.raw_time_check.split(";")[0]] += 1
        all_date_checks[core_event.date_check] += 1
        all_utc_checks[core_event.provided_utc_check] += 1

        conflict_text = core_conflicts.get(key, "")
        if conflict_text:
            conflict_count += 1
        if headline_event is None:
            missing_headline += 1

        actual_num = parse_numeric(core_event.actual_raw)
        forecast_num = parse_numeric(core_event.forecast_raw)
        previous_num = parse_numeric(core_event.previous_raw)
        surprise = actual_num - forecast_num if actual_num is not None and forecast_num is not None else None
        if surprise is None:
            surprise_sign = ""
        elif surprise > 0:
            surprise_sign = "beat"
        elif surprise < 0:
            surprise_sign = "miss"
        else:
            surprise_sign = "inline"

        h_actual = headline_event.actual_raw if headline_event else ""
        h_forecast = headline_event.forecast_raw if headline_event else ""
        h_previous = headline_event.previous_raw if headline_event else ""
        h_actual_num = parse_numeric(h_actual)
        h_forecast_num = parse_numeric(h_forecast)
        h_surprise = h_actual_num - h_forecast_num if h_actual_num is not None and h_forecast_num is not None else None

        source_files = sorted({x.source_file for x in [core_event, headline_event] if x is not None})
        source_providers = sorted({x.source_provider for x in [core_event, headline_event] if x is not None and x.source_provider})
        source_urls = sorted({x.source_url for x in [core_event, headline_event] if x is not None and x.source_url})

        time_status = core_event.raw_time_check
        # Если в processed history есть независимое поле datetime_utc, оно даёт
        # дополнительную проверку: это значение должно совпасть с 08:30 ET.
        if core_event.provided_utc_check == "MISMATCH":
            time_status += "; PROVIDED_DATETIME_UTC_MISMATCH"
        elif core_event.provided_utc_check == "MATCH":
            time_status += "; PROVIDED_DATETIME_UTC_CONFIRMED"

        output.append({
            "release_date": core_event.release_date.isoformat(),
            "release_time_et": RELEASE_TIME_NEW_YORK.strftime("%H:%M:%S"),
            "scheduled_datetime_utc": dt_to_iso(expected_utc),
            "scheduled_datetime_chisinau": dt_to_iso(local_dt),
            "currency": core_event.currency,
            "event": CORE_EVENT_NAME,
            "impact": core_event.impact,
            "actual": core_event.actual_raw,
            "forecast": core_event.forecast_raw,
            "previous": core_event.previous_raw,
            "actual_numeric": dec_to_str(actual_num),
            "forecast_numeric": dec_to_str(forecast_num),
            "previous_numeric": dec_to_str(previous_num),
            "surprise": dec_to_str(surprise),
            "surprise_sign": surprise_sign,
            "headline_retail_actual": h_actual,
            "headline_retail_forecast": h_forecast,
            "headline_retail_previous": h_previous,
            "headline_retail_surprise": dec_to_str(h_surprise),
            "date_check": core_event.date_check,
            "time_check": time_status,
            "raw_datetime": core_event.raw_datetime,
            "raw_date_field": core_event.explicit_date,
            "raw_time_field": core_event.raw_time_field,
            "raw_time_interpretation": core_event.raw_time_interpretation,
            "raw_datetime_new_york": core_event.raw_datetime_new_york,
            "raw_datetime_utc": core_event.raw_datetime_utc,
            "provided_datetime_utc": core_event.provided_datetime_utc,
            "provided_datetime_utc_check": core_event.provided_utc_check,
            "source_kind": core_event.source_kind,
            "source_provider": " | ".join(source_providers),
            "source_files": " | ".join(source_files),
            "source_urls": " | ".join(source_urls),
            "source_row_number": str(core_event.row_number),
            "source_value_conflicts": conflict_text,
            "headline_source_value_conflicts": headline_conflicts.get(
                (core_event.release_date, norm_name(COMPANION_EVENT_NAME)), ""
            ),
        })

    stats = {
        "core_rows_before_dedup": len(core_records),
        "core_rows_after_dedup": len(core),
        "core_duplicates_removed": core_duplicates,
        "headline_rows_before_dedup": len(headline_records),
        "headline_rows_after_dedup": len(headline),
        "headline_duplicates_removed": headline_duplicates,
        "missing_headline_companion": missing_headline,
        "core_value_conflicts": conflict_count,
    }

    # В QA-отчёте используем Counter, сериализованный в понятные строки.
    details = [
        "TIME_CHECKS=" + ", ".join(f"{k}: {v}" for k, v in sorted(all_time_checks.items())),
        "DATE_CHECKS=" + ", ".join(f"{k}: {v}" for k, v in sorted(all_date_checks.items())),
        "PROVIDED_UTC_CHECKS=" + ", ".join(f"{k}: {v}" for k, v in sorted(all_utc_checks.items())),
    ]
    return output, stats, details


# =============================================================================
# 6. СОХРАНЕНИЕ CSV И ОТЧЁТА ПРОВЕРКИ
# =============================================================================

OUTPUT_FIELDS = [
    "release_date", "release_time_et", "scheduled_datetime_utc", "scheduled_datetime_chisinau",
    "currency", "event", "impact",
    "actual", "forecast", "previous",
    "actual_numeric", "forecast_numeric", "previous_numeric", "surprise", "surprise_sign",
    "headline_retail_actual", "headline_retail_forecast", "headline_retail_previous", "headline_retail_surprise",
    "date_check", "time_check", "raw_datetime", "raw_date_field", "raw_time_field",
    "raw_time_interpretation", "raw_datetime_new_york", "raw_datetime_utc",
    "provided_datetime_utc", "provided_datetime_utc_check",
    "source_kind", "source_provider", "source_files", "source_urls", "source_row_number",
    "source_value_conflicts", "headline_source_value_conflicts",
]


def write_csv(path: Path, rows: Sequence[Dict[str, str]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def find_missing_months(dates: Sequence[date]) -> List[str]:
    if not dates:
        return []
    start = min(dates).year * 12 + min(dates).month - 1
    end = max(dates).year * 12 + max(dates).month - 1
    found = {d.year * 12 + d.month - 1 for d in dates}
    missing = []
    for value in range(start, end + 1):
        if value not in found:
            year = value // 12
            month = value % 12 + 1
            missing.append(f"{year:04d}-{month:02d}")
    return missing


def write_qa_report(
    path: Path,
    output_rows: Sequence[Dict[str, str]],
    stats: Dict[str, int],
    detail_lines: Sequence[str],
    rejected: Sequence[Dict[str, str]],
    files_used: Sequence[Path],
) -> None:
    dates = [date.fromisoformat(x["release_date"]) for x in output_rows]
    month_counts = Counter(f"{d.year:04d}-{d.month:02d}" for d in dates)
    duplicate_months = [f"{month} ({count} releases)" for month, count in sorted(month_counts.items()) if count > 1]

    lines = [
        "RETAIL CALENDAR VALIDATION",
        "=" * 72,
        f"Target: {TARGET_CURRENCY} / {CORE_EVENT_NAME}",
        f"Companion: {TARGET_CURRENCY} / {COMPANION_EVENT_NAME}",
        f"Official schedule used: {RELEASE_TIME_NEW_YORK.strftime('%H:%M')} {NEW_YORK_TZ_NAME}",
        f"Output timezone: {LOCAL_TZ_NAME}",
        "",
        f"Core events: {len(output_rows)}",
        f"Date range: {min(dates).isoformat() if dates else 'нет данных'} -> {max(dates).isoformat() if dates else 'нет данных'}",
        f"Unique release months: {len(month_counts)}",
        f"Months with multiple releases: {', '.join(duplicate_months) or 'нет'}",
        f"Missing months inside observed range: {', '.join(find_missing_months(dates)) or 'нет'}",
        "",
        "MERGE STATISTICS",
        *[f"{k}: {v}" for k, v in stats.items()],
        f"Rejected target rows: {len(rejected)}",
        "",
        "TIME / DATE CHECKS",
        *detail_lines,
        "",
        "INPUT FILES",
        *[str(p) for p in files_used],
        "",
        "NOTES",
        "1. scheduled_datetime_utc is rebuilt from release_date + 08:30 America/New_York.",
        "2. scheduled_datetime_chisinau is converted from the rebuilt UTC timestamp.",
        "3. The 21-column scraper export is interpreted as Europe/Chisinau local time",
        "   because its +00:00 suffix does not match the observed clock time.",
        "4. Raw timestamps are preserved in the CSV for audit; input files are not changed.",
        "5. A calendar date is not independently certified by the 08:30 rule. Conflicting",
        "   duplicated date fields are flagged; official release-date cross-check is separate.",
        "6. Retail Sales m/m is attached as a companion field and is not a second price event.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_readme(path: Path) -> None:
    text = f"""RETAIL DATASET
==============

Main file: {OUTPUT_CSV_NAME}

One row = one US {CORE_EVENT_NAME} release.
The headline {COMPANION_EVENT_NAME} values are attached to the same row as companion
information because both indicators are published together. They must not be treated
as two independent price reactions.

Date/time policy
----------------
* Release date is taken from the explicit date field when present; otherwise from DateTime.
* Scheduled time is rebuilt as 08:30 America/New_York.
* Python zoneinfo applies historical New York daylight-saving rules automatically.
* UTC and Europe/Chisinau timestamps are rebuilt from that schedule.
* Original datetime/date/time fields are kept in the output for audit.
* For 21-column scraper exports, the separate date/time columns are treated as
  Europe/Chisinau local time. The supplied +00:00 marker is not trusted for that format.

Important columns
-----------------
release_date                calendar release date
release_time_et             official release time in New York
scheduled_datetime_utc      rebuilt scheduled UTC timestamp
scheduled_datetime_chisinau  rebuilt scheduled Moldova local timestamp
raw_datetime_new_york        source clock converted to New York, if available
raw_datetime_utc             source clock converted to UTC, if available
actual/forecast/previous     original text values from the calendar
actual_numeric/...           numeric versions (for example 0.4% -> 0.4)
surprise                    actual_numeric - forecast_numeric
surprise_sign               beat / miss / inline
headline_retail_*           regular Retail Sales m/m published with Core Retail

date_check                  internal consistency of duplicate date fields
time_check                  comparison of raw time against 08:30 New York
provided_datetime_utc_check comparison against precomputed datetime_utc, if supplied
source_value_conflicts      conflicting values found across input files

This script does not alter or delete source files and does not make trading forecasts.
"""
    path.write_text(text, encoding="utf-8")


# =============================================================================
# 7. MAIN
# =============================================================================


def build_dataset(
    calendar_files: Sequence[Path],
    core_history_file: Optional[Path],
    output_dir: Path,
) -> Tuple[Path, Path]:
    rejected: List[Dict[str, str]] = []
    source_records: List[SourceEvent] = []
    files_used: List[Path] = []

    input_files = list(calendar_files)
    if core_history_file is not None:
        input_files.append(core_history_file)

    for path in input_files:
        if path is None:
            continue
        path = Path(path)
        if not path.exists():
            print(f"[SKIP] Не найден путь (проверь настройки): {path}")
            continue
        source_records.extend(extract_source_records(path, rejected))
        files_used.append(path)

    if not source_records:
        raise RuntimeError(
            "Не найдено ни одного целевого события. Проверь CALENDAR_FILES / CORE_HISTORY_FILE."
        )

    output_rows, stats, detail_lines = create_output_rows(source_records)
    if not output_rows:
        raise RuntimeError("В источниках нет USD Core Retail Sales m/m.")

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / OUTPUT_CSV_NAME
    qa_path = output_dir / OUTPUT_QA_NAME
    rejects_path = output_dir / OUTPUT_REJECTS_NAME
    readme_path = output_dir / OUTPUT_README_NAME

    write_csv(csv_path, output_rows, OUTPUT_FIELDS)
    write_qa_report(qa_path, output_rows, stats, detail_lines, rejected, files_used)
    write_readme(readme_path)

    if rejected:
        write_csv(rejects_path, rejected, ["source_file", "row_number", "reason", "raw_row"])
    elif rejects_path.exists():
        rejects_path.unlink()

    print("\n[DONE]")
    print(f"Core Retail events: {len(output_rows)}")
    print(f"CSV: {csv_path}")
    print(f"QA:  {qa_path}")
    if rejected:
        print(f"Rejected rows: {rejects_path} ({len(rejected)})")
    print(f"README: {readme_path}")
    return csv_path, qa_path


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    output_dir = script_dir / OUTPUT_DIR_NAME

    try:
        build_dataset(
            calendar_files=[Path(p) for p in CALENDAR_FILES],
            core_history_file=Path(CORE_HISTORY_FILE) if CORE_HISTORY_FILE else None,
            output_dir=output_dir,
        )
        return 0
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
