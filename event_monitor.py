"""
Core Retail Sales Event Monitor (Windows, Python 3.10+)
========================================================
A small always-running supervisor for existing project scripts.

Run:
    py -3 event_monitor.py

Configure the values in the USER CONFIGURATION section below.
No third-party packages are required.

Behavior:
- Watches the configured calendar CSV.
- Runs PRE_RELEASE_TASKS once at RELEASE - PRE_RELEASE_MINUTES.
- Waits until Actual is populated in the calendar CSV.
- Runs POST_RELEASE_TASKS once after Actual appears.
- Reads scenario_templates.json and sends a compact report to Telegram.
- Persists event state in event_monitor_state.json to prevent duplicate runs/sends.

Important:
- This does not rewrite your existing analysis scripts.
- Tasks are just subprocess commands. Add/remove scripts in the two task lists.
- The calendar updater must actually populate Actual/Forecast/Previous in the CSV.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# =============================================================================
# USER CONFIGURATION — EDIT THESE VALUES
# =============================================================================

# Project root: folder containing this file, unless you specify another path.
PROJECT_ROOT = Path(__file__).resolve().parent

# Existing normalized calendar file. Update if your file is elsewhere.
CALENDAR_CSV = "E:\work\XAU_gold\mvp_tg_bot\mvp_retail\retail\us_core_retail_sales.csv"

# Existing Stage 1 scenario file.
SCENARIO_JSON = PROJECT_ROOT / "output" / "stage1" / "scenario_templates.json"

# Event to monitor (match is case-insensitive; partial match is allowed).
TARGET_EVENT = "Core Retail Sales m/m"
TARGET_CURRENCY = "USD"

# Calendar datetime column. Your current CSV uses scheduled_datetime_utc.
EVENT_DATETIME_COLUMN = "scheduled_datetime_utc"

# The monitor checks the CSV for populated actual_numeric.
ACTUAL_COLUMN = "actual_numeric"
FORECAST_COLUMN = "forecast_numeric"
PREVIOUS_COLUMN = "previous_numeric"
SURPRISE_COLUMN = "surprise"
SURPRISE_SIGN_COLUMN = "surprise_sign"

# Run these existing files before the release.
# Each task is a dict: name, script, args (optional), cwd (optional).
# Use absolute paths or paths relative to PROJECT_ROOT.
PRE_RELEASE_MINUTES = 45
PRE_RELEASE_TASKS: list[dict[str, Any]] = [
    # Example:
    # {
    #     "name": "Update calendar/data",
    #     "script": "update_data.py",
    #     "args": [],
    #     "cwd": ".",
    # },
]

# Run these existing files after Actual appears in the calendar.
POST_RELEASE_TASKS: list[dict[str, Any]] = [
    # Example:
    # {
    #     "name": "Run reaction analysis",
    #     "script": "retail_reaction_analyzer.py",
    #     "args": [],
    #     "cwd": ".",
    # },
    # {
    #     "name": "Build Stage 1 templates",
    #     "script": "pipeline.py",
    #     "args": [],
    #     "cwd": ".",
    # },
]

# Telegram credentials: fill these yourself.
TELEGRAM_BOT_TOKEN = ""   # Example format: 123456:ABC...
TELEGRAM_CHAT_ID = ""     # Numeric chat ID or @channelusername
TELEGRAM_ENABLED = True

# If True, prints the report instead of sending it to Telegram.
# Set False after filling bot token and chat ID.
DRY_RUN = True

# Monitor behavior.
POLL_INTERVAL_SECONDS = 15
LOOKAHEAD_DAYS = 45
TASK_TIMEOUT_SECONDS = 60 * 60
STATE_FILE = PROJECT_ROOT / "event_monitor_state.json"
LOG_FILE = PROJECT_ROOT / "event_monitor.log"

# =============================================================================
# IMPLEMENTATION
# =============================================================================

UTC = timezone.utc


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # The configured column should be timezone-aware UTC.
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def is_number(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "na", "none", "null", "nan"}:
        return False
    try:
        return math.isfinite(float(text.replace(",", ".")))
    except ValueError:
        return False


def as_float(value: Any) -> float | None:
    if not is_number(value):
        return None
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def read_calendar_events() -> list[dict[str, str]]:
    if not CALENDAR_CSV.exists():
        raise FileNotFoundError(f"Calendar CSV not found: {CALENDAR_CSV}")
    with CALENDAR_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    now = datetime.now(UTC)
    result = []
    for row in rows:
        event = (row.get("event") or "").strip()
        currency = (row.get("currency") or "").strip()
        if TARGET_EVENT.lower() not in event.lower():
            continue
        if TARGET_CURRENCY and currency.upper() != TARGET_CURRENCY.upper():
            continue

        release_dt = parse_datetime(row.get(EVENT_DATETIME_COLUMN, ""))
        if release_dt is None:
            continue
        if release_dt < now - timedelta(days=1):
            continue
        if release_dt > now + timedelta(days=LOOKAHEAD_DAYS):
            continue

        row["_release_dt_utc"] = release_dt.isoformat()
        row["_event_key"] = f"{event}|{release_dt.isoformat()}"
        result.append(row)

    result.sort(key=lambda x: x["_release_dt_utc"])
    return result


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"events": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logging.exception("Could not read state file; starting with empty state.")
        return {"events": {}}


def save_state(state: dict[str, Any]) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def resolve_path(value: str, cwd: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (cwd / path).resolve()


def run_tasks(tasks: list[dict[str, Any]], stage: str) -> bool:
    """Run configured scripts sequentially. Returns False if any task fails."""
    if not tasks:
        logging.info("%s: no tasks configured; nothing to run.", stage)
        return True

    for task in tasks:
        name = str(task.get("name") or task.get("script") or "unnamed task")
        script_value = str(task.get("script") or "").strip()
        if not script_value:
            logging.error("%s: task '%s' has no script path.", stage, name)
            return False

        cwd_value = Path(str(task.get("cwd") or "."))
        cwd = cwd_value if cwd_value.is_absolute() else (PROJECT_ROOT / cwd_value).resolve()
        script = resolve_path(script_value, cwd)
        if not script.exists():
            logging.error("%s: script not found: %s", stage, script)
            return False

        args = [str(x) for x in task.get("args", [])]
        command = [sys.executable, str(script), *args]
        logging.info("%s: starting '%s': %s", stage, name, command)

        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                check=False,
                timeout=TASK_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logging.exception("%s: task '%s' timed out.", stage, name)
            return False
        except OSError:
            logging.exception("%s: could not start task '%s'.", stage, name)
            return False

        if completed.returncode != 0:
            logging.error(
                "%s: task '%s' exited with code %s.",
                stage, name, completed.returncode
            )
            return False

        logging.info("%s: task '%s' completed.", stage, name)

    return True


def load_scenarios() -> dict[str, Any]:
    if not SCENARIO_JSON.exists():
        logging.warning("Scenario JSON not found: %s", SCENARIO_JSON)
        return {}
    try:
        return json.loads(SCENARIO_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logging.exception("Could not read scenario JSON.")
        return {}


def select_template(
    scenarios: dict[str, Any],
    surprise_sign: str,
    surprise_abs: float | None,
) -> tuple[dict[str, Any] | None, str]:
    sign = (surprise_sign or "").strip().lower()
    if sign not in {"beat", "miss", "inline"}:
        return None, "Не удалось определить Beat/Miss/Inline по полю surprise_sign."

    if sign == "inline":
        size_group = "inline"
    else:
        bounds = scenarios.get("size_group_boundaries", {}).get(sign, {})
        q33 = as_float(bounds.get("q33"))
        q67 = as_float(bounds.get("q67"))
        if surprise_abs is None or q33 is None or q67 is None:
            return None, "Нет Surprise или границ размера Surprise в JSON."
        if surprise_abs <= q33:
            size_group = "small"
        elif surprise_abs <= q67:
            size_group = "medium"
        else:
            size_group = "large"

    template_id = f"{sign}__{size_group}"
    for template in scenarios.get("templates", []):
        if template.get("template_id") == template_id:
            return template, f"{sign.upper()} / {size_group.upper()}"

    return None, f"Шаблон {template_id} не найден в scenario_templates.json."


def fmt_pct(value: Any) -> str:
    number = as_float(value)
    return "—" if number is None else f"{number * 100:.1f}%"


def fmt_num(value: Any) -> str:
    number = as_float(value)
    return "—" if number is None else f"{number:g}"


def build_report(event: dict[str, str]) -> str:
    actual = as_float(event.get(ACTUAL_COLUMN))
    forecast = as_float(event.get(FORECAST_COLUMN))
    previous = as_float(event.get(PREVIOUS_COLUMN))
    surprise = as_float(event.get(SURPRISE_COLUMN))
    sign = (event.get(SURPRISE_SIGN_COLUMN) or "").strip().lower()

    scenarios = load_scenarios()
    template, scenario_label = select_template(scenarios, sign, abs(surprise) if surprise is not None else None)

    release_dt = parse_datetime(event.get(EVENT_DATETIME_COLUMN, "")) or datetime.now(UTC)
    lines = [
        "📊 CORE RETAIL SALES — РЕЗУЛЬТАТ",
        f"Время публикации (UTC): {release_dt:%Y-%m-%d %H:%M}",
        "",
        f"Actual: {fmt_num(actual)}",
        f"Forecast: {fmt_num(forecast)}",
        f"Previous: {fmt_num(previous)}",
        f"Surprise (Actual − Forecast): {fmt_num(surprise)}",
        f"Сценарий: {scenario_label}",
        "",
        "📈 XAUUSD — историческая статистика",
    ]

    if template is None:
        lines.append(scenario_label)
        lines.append("Проверь значения календаря и наличие соответствующего шаблона.")
    else:
        instruments = template.get("instruments", {})
        xau = instruments.get("XAU", {})
        horizons = xau.get("horizons", {})
        for horizon in ("1", "5", "15", "30", "60", "120"):
            stats = horizons.get(horizon, {}).get("all_periods", {})
            if not stats:
                continue
            n = stats.get("n", "—")
            lean = stats.get("direction_lean") or "не выражено"
            p_up = fmt_pct(stats.get("p_up"))
            p_down = fmt_pct(stats.get("p_down"))
            p_flat = fmt_pct(stats.get("p_flat"))
            p_up_dir = fmt_pct(stats.get("p_up_dir"))
            p_down_dir = fmt_pct(stats.get("p_down_dir"))
            reliability = stats.get("reliability", "—")
            quality = stats.get("signal_quality", "—")
            lines.append(
                f"{horizon} мин: ↑ {p_up} | ↓ {p_down} | flat {p_flat}; "
                f"направление без flat: ↑ {p_up_dir} / ↓ {p_down_dir}; "
                f"n={n}, {reliability}, {quality}, lean={lean}"
            )

    lines.extend([
        "",
        "Источник: исторические шаблоны Stage 1.",
        "Это частоты исторических реакций, не гарантия будущего движения.",
    ])
    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    if DRY_RUN:
        logging.info("DRY_RUN report:\n%s", message)
        return True
    if not TELEGRAM_ENABLED:
        logging.info("Telegram disabled; report was not sent.")
        return True
    if not TELEGRAM_BOT_TOKEN.strip() or not TELEGRAM_CHAT_ID.strip():
        logging.error("Fill TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not data.get("ok"):
            logging.error("Telegram API returned error: %s", data)
            return False
        logging.info("Telegram message sent.")
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        logging.exception("Telegram send failed.")
        return False


def event_has_actual(event: dict[str, str]) -> bool:
    return is_number(event.get(ACTUAL_COLUMN))


def process_event(event: dict[str, str], state: dict[str, Any]) -> None:
    key = event["_event_key"]
    event_state = state.setdefault("events", {}).setdefault(key, {})
    release_dt = parse_datetime(event["_release_dt_utc"])
    if release_dt is None:
        return

    now = datetime.now(UTC)
    pre_time = release_dt - timedelta(minutes=PRE_RELEASE_MINUTES)

    # PRE stage: run once when the configured pre-release time is reached.
    if now >= pre_time and not event_state.get("pre_done"):
        logging.info("Pre-release window reached for %s.", key)
        if run_tasks(PRE_RELEASE_TASKS, "PRE_RELEASE"):
            event_state["pre_done"] = True
            event_state["pre_done_at"] = now.isoformat()
            save_state(state)
        else:
            logging.error("Pre-release tasks failed; will retry on next poll.")
            return

    # POST stage: only proceed after Actual is available in the calendar CSV.
    if event_has_actual(event) and not event_state.get("telegram_sent"):
        logging.info("Actual detected for %s.", key)

        # Do not rerun successful scripts just because Telegram temporarily failed.
        if not event_state.get("post_tasks_done"):
            if not run_tasks(POST_RELEASE_TASKS, "POST_RELEASE"):
                logging.error("Post-release tasks failed; will retry on next poll.")
                return
            event_state["post_tasks_done"] = True
            event_state["post_tasks_done_at"] = datetime.now(UTC).isoformat()
            save_state(state)

        # Re-read the calendar after update scripts, in case they refreshed it.
        refreshed = read_calendar_events()
        latest = next((x for x in refreshed if x["_event_key"] == key), event)
        report = build_report(latest)
        if send_telegram(report):
            event_state["telegram_sent"] = True
            event_state["telegram_sent_at"] = datetime.now(UTC).isoformat()
            save_state(state)


def main() -> int:
    setup_logging()
    logging.info("Event Monitor started.")
    logging.info("Project root: %s", PROJECT_ROOT)
    logging.info("Calendar: %s", CALENDAR_CSV)
    logging.info("Scenario JSON: %s", SCENARIO_JSON)
    logging.info("Dry run: %s", DRY_RUN)

    while True:
        try:
            state = load_state()
            events = read_calendar_events()
            if not events:
                logging.info("No upcoming %s events found.", TARGET_EVENT)
            for event in events:
                process_event(event, state)
            save_state(state)
        except KeyboardInterrupt:
            logging.info("Stopped by user.")
            return 0
        except Exception:
            logging.exception("Monitor cycle failed; will retry.")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
