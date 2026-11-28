#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage D: weighted historical XAUUSD forecast for eight USD macro releases.

Consumes Stage C event-level CSVs and creates a reusable JSON model. It does
not need to load BID/ASK quote history again. The generated module can be
imported by a Telegram bot with load_model() and predict_from_actuals().

Expected Stage C folder:
  output_stage_c_corrected/
    stage_c_run_manifest.json
    reports/<report_slug>_events.csv

Methods:
- Report weights: chronological walk-forward multiclass Brier Skill Score.
- Redundancy correction: correlation between out-of-sample report signals on
  timestamps where reports co-occur.
- Report probabilities: Dirichlet-smoothed group frequencies.
- Combined forecast: skill-weighted probability mixture, not a sum of price
  moves. A joint softmax model is built and used only when its walk-forward
  skill and common-event sample are sufficient.

No model can calculate a final release-specific forecast until Actual and
Forecast values are supplied for the release.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

UTC = timezone.utc
REPORTS = [
    ("core_pce_price_index_m_m", "Core PCE Price Index m/m"),
    ("prelim_gdp_q_q", "Prelim GDP q/q"),
    ("prelim_gdp_price_index_q_q", "Prelim GDP Price Index q/q"),
    ("core_durable_goods_orders_m_m", "Core Durable Goods Orders m/m"),
    ("durable_goods_orders_m_m", "Durable Goods Orders m/m"),
    ("personal_income_m_m", "Personal Income m/m"),
    ("personal_spending_m_m", "Personal Spending m/m"),
    ("unemployment_claims", "Unemployment Claims"),
]
SLUG_TO_NAME = dict(REPORTS)
NAME_TO_SLUG = {name.lower(): slug for slug, name in REPORTS}
GROUPS = ("below_forecast", "inline", "above_forecast")
CLASSES = ("up", "down", "flat")
CLASS_INDEX = {name: i for i, name in enumerate(CLASSES)}
DEFAULT_ALPHA = 8.0
MIN_EVENTS_FOR_WEIGHT = 30
MIN_OOS_FOR_WEIGHT = 8
MIN_JOINT_EVENTS = 40
MIN_JOINT_OOS = 10
MIN_JOINT_BSS = 0.01
MIN_JOINT_COVERAGE = 20
JOINT_L2 = 3.0

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
LOG = logging.getLogger("stage_d")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def parse_numeric(value: Any) -> Optional[float]:
    """Parse values such as 0.3%, 201K, 1.2M, -0.1."""
    if value is None:
        return None
    if isinstance(value, (int, float, np.number)):
        return safe_float(value)
    s = str(value).strip().replace("−", "-").replace("–", "-")
    if not s or s.lower() in {"n/a", "na", "--", "-", "tbd", "null", "none"}:
        return None
    s = s.replace(",", "")
    m = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", s)
    if not m:
        return None
    try:
        number = float(m.group(0))
    except ValueError:
        return None
    suffix = re.search(r"\s*([KMB])\b", s[m.end():], flags=re.IGNORECASE)
    if suffix:
        number *= {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0}[suffix.group(1).upper()]
    return number if math.isfinite(number) else None


def safe_json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
    tmp.replace(path)


def json_num(value: Any) -> Optional[float]:
    v = safe_float(value)
    return round(v, 8) if v is not None else None


def probs_from_counts(counts: Counter, base: np.ndarray, alpha: float = DEFAULT_ALPHA) -> np.ndarray:
    n = sum(counts.get(c, 0) for c in CLASSES)
    if n <= 0:
        return base.copy()
    return np.array([
        (counts.get(c, 0) + alpha * float(base[i])) / (n + alpha)
        for i, c in enumerate(CLASSES)
    ], dtype=float)


def smoothed_base(counts: Counter, prior: float = 1.0) -> np.ndarray:
    vals = np.array([counts.get(c, 0) for c in CLASSES], dtype=float)
    return (vals + prior) / (vals.sum() + prior * len(CLASSES))


def brier_score(y: list[str], p: np.ndarray) -> float:
    if len(y) == 0:
        return float("nan")
    target = np.zeros((len(y), len(CLASSES)), dtype=float)
    for i, label in enumerate(y):
        target[i, CLASS_INDEX[label]] = 1.0
    return float(np.mean(np.sum((p - target) ** 2, axis=1)))


def validate_manifest(stage_c_dir: Path, allow_time_mismatch: bool = False) -> dict:
    manifest_path = stage_c_dir / "stage_c_run_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Stage C manifest not found: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    settings = manifest.get("settings", {})
    event_rule = str(settings.get("event_time_rule", ""))
    release_time = str(settings.get("release_time", ""))
    release_tz = str(settings.get("release_timezone", ""))
    is_corrected = "calendar date + scheduled release time" in event_rule.lower()
    if not is_corrected and not allow_time_mismatch:
        raise ValueError(
            "This does not look like corrected Stage C output. The manifest must contain "
            "settings.event_time_rule = 'calendar date + scheduled release time ...'. "
            "Run stage_c_xauusd_analyzer_corrected.py first. For a diagnostic test only, "
            "use --allow-time-mismatch."
        )
    if release_time and release_time != "08:30":
        LOG.warning("Manifest release_time=%s; this project expects 08:30 New York.", release_time)
    if release_tz and release_tz != "America/New_York":
        LOG.warning("Manifest release_timezone=%s; expected America/New_York.", release_tz)
    return manifest


def load_event_files(stage_c_dir: Path, manifest: dict, allow_time_mismatch: bool = False):
    reports_dir = stage_c_dir / "reports"
    if not reports_dir.exists():
        raise FileNotFoundError(f"Missing reports directory: {reports_dir}")

    settings = manifest.get("settings", {})
    inline_tolerance = safe_float(settings.get("actual_forecast_inline_tolerance"))
    if inline_tolerance is None:
        inline_tolerance = 0.0

    frames: dict[str, pd.DataFrame] = {}
    audit: dict[str, dict] = {}
    for slug, report_name in REPORTS:
        path = reports_dir / f"{slug}_events.csv"
        if not path.exists():
            LOG.warning("Event file missing; this report will have no weight: %s", path.name)
            audit[slug] = {"report": report_name, "file_found": False, "rows": 0}
            continue
        try:
            df = pd.read_csv(path, low_memory=False)
        except Exception as exc:
            raise RuntimeError(f"Cannot read {path}: {exc}") from exc

        required = {"event_time_utc", "result_group"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"{path.name} is missing required columns: {sorted(missing)}")
        df["event_time_utc"] = pd.to_datetime(df["event_time_utc"], utc=True, errors="coerce")
        df["result_group"] = df["result_group"].astype(str).str.strip().str.lower()
        df.loc[~df["result_group"].isin(GROUPS), "result_group"] = None

        # Recompute the group from numeric Actual/Forecast if the fields exist.
        if "actual_numeric" in df.columns and "forecast_numeric" in df.columns:
            a = pd.to_numeric(df["actual_numeric"], errors="coerce")
            f = pd.to_numeric(df["forecast_numeric"], errors="coerce")
            diff = a - f
            valid = a.notna() & f.notna()
            recomputed = pd.Series(index=df.index, dtype="object")
            recomputed.loc[valid & (diff > inline_tolerance)] = "above_forecast"
            recomputed.loc[valid & (diff < -inline_tolerance)] = "below_forecast"
            recomputed.loc[valid & (diff.abs() <= inline_tolerance)] = "inline"
            df.loc[valid, "result_group"] = recomputed.loc[valid]

        for horizon in (5, 15):
            dcol = f"m{horizon}_direction"
            ccol = f"m{horizon}_change_usd"
            if dcol not in df.columns and ccol in df.columns:
                change = pd.to_numeric(df[ccol], errors="coerce")
                df[dcol] = np.where(change > 0, "up", np.where(change < 0, "down", "flat"))
            if dcol in df.columns:
                df[dcol] = df[dcol].astype(str).str.strip().str.lower()
                df.loc[~df[dcol].isin(CLASSES), dcol] = None
            if ccol in df.columns:
                df[ccol] = pd.to_numeric(df[ccol], errors="coerce")

        for col in ("m5_abs_change_usd", "m15_abs_change_usd", "baseline_age_seconds", "m5_age_seconds", "m15_age_seconds"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["event_time_utc", "result_group"])
        # A single report should contribute at most one record per scheduled timestamp.
        before = len(df)
        if "source_row" in df.columns:
            df = df.sort_values(["event_time_utc", "source_row"]).drop_duplicates("event_time_utc", keep="last")
        else:
            df = df.sort_values("event_time_utc").drop_duplicates("event_time_utc", keep="last")
        deduped = before - len(df)

        # The corrected analyzer's output must resolve to 08:30 New York.
        bad_release_time = 0
        if not allow_time_mismatch:
            try:
                local_times = df["event_time_utc"].dt.tz_convert("America/New_York").dt.strftime("%H:%M")
                bad_release_time = int((local_times != "08:30").sum())
                if len(df) and bad_release_time / len(df) > 0.02:
                    raise ValueError(
                        f"{slug}: {bad_release_time}/{len(df)} timestamps are not 08:30 America/New_York. "
                        "Check the Stage C time reconstruction before building weights."
                    )
            except TypeError:
                pass

        valid_by_horizon = {}
        for h in (5, 15):
            dcol = f"m{h}_direction"
            valid_by_horizon[str(h)] = int(df[dcol].isin(CLASSES).sum()) if dcol in df.columns else 0

        frames[slug] = df.reset_index(drop=True)
        audit[slug] = {
            "report": report_name,
            "file_found": True,
            "rows_in_file": int(len(pd.read_csv(path, usecols=[0]))),
            "events_loaded": int(len(df)),
            "duplicate_timestamps_removed": int(deduped),
            "bad_release_time_rows": int(bad_release_time),
            "events_with_direction": valid_by_horizon,
            "first_event_utc": df["event_time_utc"].min().isoformat() if len(df) else None,
            "last_event_utc": df["event_time_utc"].max().isoformat() if len(df) else None,
        }
        LOG.info("Loaded %-38s %4d events", slug, len(df))

    if not frames:
        raise ValueError("No Stage C report event CSVs were found.")
    return frames, audit, inline_tolerance


def event_rows_for_horizon(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    dcol = f"m{horizon}_direction"
    if dcol not in df.columns:
        return pd.DataFrame(columns=["event_time_utc", "result_group", "direction", "change"])
    cols = ["event_time_utc", "result_group", dcol]
    if f"m{horizon}_change_usd" in df.columns:
        cols.append(f"m{horizon}_change_usd")
    x = df[cols].copy()
    x = x.rename(columns={dcol: "direction", f"m{horizon}_change_usd": "change"})
    x = x[x["direction"].isin(CLASSES) & x["result_group"].isin(GROUPS)]
    return x.sort_values("event_time_utc").reset_index(drop=True)


def report_oos_backtest(df: pd.DataFrame, horizon: int, alpha: float = DEFAULT_ALPHA):
    """Expanding-window walk-forward predictions for one release series."""
    events = event_rows_for_horizon(df, horizon)
    n = len(events)
    if n < MIN_EVENTS_FOR_WEIGHT:
        return [], {"events": n, "oos_events": 0, "brier_model": None, "brier_baseline": None, "bss": None,
                    "status": "insufficient_history"}

    min_train = max(15, int(math.floor(n * 0.50)))
    if n - min_train < MIN_OOS_FOR_WEIGHT:
        min_train = n - MIN_OOS_FOR_WEIGHT
    min_train = max(10, min_train)
    rows = []
    p_model = []
    p_base = []
    y = []
    for i in range(min_train, n):
        train = events.iloc[:i]
        test = events.iloc[i]
        base_counts = Counter(train["direction"].tolist())
        base_prob = smoothed_base(base_counts)
        group_counts = Counter(train.loc[train["result_group"] == test["result_group"], "direction"].tolist())
        group_prob = probs_from_counts(group_counts, base_prob, alpha=alpha)
        label = str(test["direction"])
        p_model.append(group_prob)
        p_base.append(base_prob)
        y.append(label)
        rows.append({
            "event_time_utc": test["event_time_utc"].isoformat(),
            "report_slug": None,
            "horizon_minutes": horizon,
            "result_group": test["result_group"],
            "actual_direction": label,
            "model_p_up": float(group_prob[0]),
            "model_p_down": float(group_prob[1]),
            "model_p_flat": float(group_prob[2]),
            "baseline_p_up": float(base_prob[0]),
            "baseline_p_down": float(base_prob[1]),
            "baseline_p_flat": float(base_prob[2]),
            "signal_up": float(group_prob[0] - base_prob[0]),
        })
    pm = np.asarray(p_model, dtype=float)
    pb = np.asarray(p_base, dtype=float)
    bs_model = brier_score(y, pm)
    bs_base = brier_score(y, pb)
    bss = (1.0 - bs_model / bs_base) if math.isfinite(bs_base) and bs_base > 1e-12 else None
    return rows, {
        "events": n,
        "oos_events": len(y),
        "first_oos_event_utc": rows[0]["event_time_utc"] if rows else None,
        "last_oos_event_utc": rows[-1]["event_time_utc"] if rows else None,
        "brier_model": bs_model,
        "brier_baseline": bs_base,
        "bss": bss,
        "status": "ok" if len(y) >= MIN_OOS_FOR_WEIGHT else "insufficient_oos",
    }


def full_group_stats(df: pd.DataFrame, horizon: int, alpha: float = DEFAULT_ALPHA):
    events = event_rows_for_horizon(df, horizon)
    if len(events) == 0:
        return {"base_probabilities": {c: 1/3 for c in CLASSES}, "groups": {}}
    base_counts = Counter(events["direction"].tolist())
    base_prob = smoothed_base(base_counts)
    changes = pd.to_numeric(events.get("change", pd.Series(dtype=float)), errors="coerce")
    base_mean = float(changes.mean()) if changes.notna().any() else 0.0
    base_abs = float(changes.abs().mean()) if changes.notna().any() else 0.0
    groups = {}
    for group in GROUPS:
        subset = events[events["result_group"] == group]
        cnt = Counter(subset["direction"].tolist())
        probs = probs_from_counts(cnt, base_prob, alpha=alpha)
        ch = pd.to_numeric(subset.get("change", pd.Series(dtype=float)), errors="coerce").dropna()
        n_ch = int(len(ch))
        # Shrink expected price move toward the report's unconditional mean.
        mean_change = ((float(ch.mean()) * n_ch + alpha * base_mean) / (n_ch + alpha)) if n_ch else base_mean
        abs_values = ch.abs()
        mean_abs = ((float(abs_values.mean()) * n_ch + alpha * base_abs) / (n_ch + alpha)) if n_ch else base_abs
        groups[group] = {
            "events": int(len(subset)),
            "events_with_price_change": n_ch,
            "probabilities": {c: float(probs[i]) for i, c in enumerate(CLASSES)},
            "mean_change_usd": mean_change,
            "median_change_usd": float(ch.median()) if n_ch else None,
            "mean_abs_change_usd": mean_abs,
            "q25_change_usd": float(ch.quantile(0.25)) if n_ch else None,
            "q75_change_usd": float(ch.quantile(0.75)) if n_ch else None,
        }
    return {
        "base_probabilities": {c: float(base_prob[i]) for i, c in enumerate(CLASSES)},
        "events": int(len(events)),
        "mean_change_usd": base_mean,
        "mean_abs_change_usd": base_abs,
        "groups": groups,
    }


def build_oos_signal_correlations(oos_rows_by_slug: dict[str, list[dict]]):
    series = {}
    for slug, rows in oos_rows_by_slug.items():
        if not rows:
            continue
        s = pd.DataFrame(rows)
        s["event_time_utc"] = pd.to_datetime(s["event_time_utc"], utc=True)
        series[slug] = s.set_index("event_time_utc")["signal_up"]
    corr_rows = []
    redundancy = {slug: [] for slug in oos_rows_by_slug}
    slugs = list(series)
    for i, a in enumerate(slugs):
        for b in slugs[i+1:]:
            joined = pd.concat([series[a].rename("a"), series[b].rename("b")], axis=1, join="inner").dropna()
            n = len(joined)
            corr = float(joined["a"].corr(joined["b"])) if n >= 10 else None
            corr_rows.append({"report_a": a, "report_b": b, "coincident_oos_events": n, "signal_correlation": corr,
                              "absolute_correlation": abs(corr) if corr is not None and math.isfinite(corr) else None})
            if corr is not None and math.isfinite(corr):
                redundancy[a].append(abs(corr))
                redundancy[b].append(abs(corr))
    red_avg = {slug: (float(np.mean(vals)) if vals else 0.0) for slug, vals in redundancy.items()}
    return corr_rows, red_avg


def normalise_weights(raw: dict[str, float], eligible: list[str]):
    positive = {s: max(0.0, float(raw.get(s, 0.0))) for s in eligible}
    total = sum(positive.values())
    if total > 0:
        return {s: positive[s] / total for s in eligible}, "walk_forward_skill"
    if eligible:
        return {s: 1.0 / len(eligible) for s in eligible}, "equal_fallback_no_positive_skill"
    return {}, "no_eligible_reports"


def make_joint_matrix(joined: pd.DataFrame):
    """Three columns/report: below, above, missing; inline is the reference."""
    matrix = np.zeros((len(joined), len(REPORTS) * 3), dtype=float)
    for r, (slug, _) in enumerate(REPORTS):
        values = joined[slug].tolist()
        for i, group in enumerate(values):
            base = r * 3
            if group == "below_forecast":
                matrix[i, base] = 1.0
            elif group == "above_forecast":
                matrix[i, base + 1] = 1.0
            elif group is None or (isinstance(group, float) and math.isnan(group)):
                matrix[i, base + 2] = 1.0
            # inline is encoded as all zeros for this report.
    return matrix


def fit_softmax(X: np.ndarray, labels: list[str], reg: float = JOINT_L2, max_iter: int = 700):
    """Small ridge-regularised 3-class softmax regression using batch GD."""
    n, p = X.shape
    if n == 0:
        raise ValueError("Cannot fit softmax to an empty sample")
    Xb = np.column_stack([np.ones(n), X])
    k = len(CLASSES)
    Y = np.zeros((n, k), dtype=float)
    for i, label in enumerate(labels):
        Y[i, CLASS_INDEX[label]] = 1.0
    counts = np.array([labels.count(c) for c in CLASSES], dtype=float)
    priors = (counts + 1.0) / (n + k)
    W = np.zeros((p + 1, k), dtype=float)
    W[0, :] = np.log(priors)
    penalty = np.ones((p + 1, 1), dtype=float)
    penalty[0, 0] = 0.0  # do not penalise intercept
    # Lipschitz-safe conservative step for binary design columns.
    lr = 0.06
    previous_loss = float("inf")
    for _ in range(max_iter):
        z = Xb @ W
        z -= z.max(axis=1, keepdims=True)
        expz = np.exp(np.clip(z, -50, 50))
        P = expz / expz.sum(axis=1, keepdims=True)
        loss = -float(np.sum(Y * np.log(np.clip(P, 1e-12, 1.0))) / n) + 0.5 * reg * float(np.sum((W * penalty) ** 2))
        grad = (Xb.T @ (P - Y)) / n + reg * W * penalty
        W -= lr * grad
        if abs(previous_loss - loss) < 1e-10:
            break
        previous_loss = loss
    return W


def softmax_predict(W: np.ndarray, X: np.ndarray) -> np.ndarray:
    Xb = np.column_stack([np.ones(len(X)), X])
    z = Xb @ W
    z -= z.max(axis=1, keepdims=True)
    e = np.exp(np.clip(z, -50, 50))
    return e / e.sum(axis=1, keepdims=True)


def build_joint_events(frames: dict[str, pd.DataFrame], horizon: int):
    rows = []
    for slug, df in frames.items():
        e = event_rows_for_horizon(df, horizon)
        if len(e) == 0:
            continue
        for _, r in e.iterrows():
            rows.append({
                "event_time_utc": r["event_time_utc"],
                "report_slug": slug,
                "result_group": r["result_group"],
                "direction": r["direction"],
                "change": safe_float(r.get("change")),
            })
    if not rows:
        return pd.DataFrame()
    long = pd.DataFrame(rows)
    combined = []
    for ts, group in long.groupby("event_time_utc", sort=True):
        labels = group["direction"].tolist()
        changes = pd.to_numeric(group["change"], errors="coerce").dropna()
        # If repeated quotes for the same release timestamp disagree, use median.
        if len(changes):
            median_change = float(changes.median())
            direction = "up" if median_change > 0 else "down" if median_change < 0 else "flat"
        else:
            direction = Counter(labels).most_common(1)[0][0]
        record = {slug: None for slug, _ in REPORTS}
        for _, row in group.iterrows():
            record[row["report_slug"]] = row["result_group"]
        record.update({"event_time_utc": ts, "direction": direction, "reports_present": int(group["report_slug"].nunique()),
                       "mean_abs_change_usd": float(changes.abs().mean()) if len(changes) else None})
        combined.append(record)
    out = pd.DataFrame(combined).sort_values("event_time_utc").reset_index(drop=True)
    return out


def joint_oos_backtest(joint: pd.DataFrame, horizon: int):
    if len(joint) < MIN_JOINT_EVENTS:
        return [], {"events": int(len(joint)), "oos_events": 0, "brier_model": None, "brier_baseline": None,
                    "bss": None, "status": "insufficient_joint_history"}
    # Require at least two of the eight reports on a historical timestamp.
    data = joint[joint["reports_present"] >= 2].reset_index(drop=True)
    n = len(data)
    if n < MIN_JOINT_EVENTS:
        return [], {"events": int(n), "oos_events": 0, "brier_model": None, "brier_baseline": None,
                    "bss": None, "status": "insufficient_coincident_reports"}
    min_train = max(25, int(math.floor(n * 0.55)))
    if n - min_train < MIN_JOINT_OOS:
        min_train = n - MIN_JOINT_OOS
    if min_train < 20:
        return [], {"events": int(n), "oos_events": 0, "brier_model": None, "brier_baseline": None,
                    "bss": None, "status": "insufficient_train_window"}

    # Blocked expanding-window validation: fit once per chronological block,
    # rather than refitting for every minute/event. Test rows never enter their
    # own training block, and this is considerably faster for the joint model.
    pred_rows, pm_list, pb_list, y = [], [], [], []
    remaining = n - min_train
    block_size = max(5, int(math.ceil(remaining / 5.0)))
    for start in range(min_train, n, block_size):
        stop = min(n, start + block_size)
        train = data.iloc[:start]
        test = data.iloc[start:stop]
        X_train = make_joint_matrix(train)
        X_test = make_joint_matrix(test)
        labels_train = train["direction"].tolist()
        base = smoothed_base(Counter(labels_train))
        W = fit_softmax(X_train, labels_train)
        p_block = softmax_predict(W, X_test)
        for j, (_, test_row) in enumerate(test.iterrows()):
            p_model = p_block[j]
            label = str(test_row["direction"])
            pm_list.append(p_model)
            pb_list.append(base)
            y.append(label)
            pred_rows.append({
                "event_time_utc": test_row["event_time_utc"].isoformat(),
                "horizon_minutes": horizon,
                "reports_present": int(test_row["reports_present"]),
                "actual_direction": label,
                "model_p_up": float(p_model[0]), "model_p_down": float(p_model[1]), "model_p_flat": float(p_model[2]),
                "baseline_p_up": float(base[0]), "baseline_p_down": float(base[1]), "baseline_p_flat": float(base[2]),
            })
    pm = np.asarray(pm_list)
    pb = np.asarray(pb_list)
    bs_model = brier_score(y, pm)
    bs_base = brier_score(y, pb)
    bss = (1.0 - bs_model / bs_base) if bs_base > 1e-12 else None
    return pred_rows, {
        "events": int(n), "oos_events": len(y),
        "min_reports_per_timestamp": 2,
        "max_reports_present_in_history": int(data["reports_present"].max()),
        "brier_model": bs_model, "brier_baseline": bs_base, "bss": bss,
        "validation": "5 chronological expanding-window blocks",
        "status": "ok" if len(y) >= MIN_JOINT_OOS else "insufficient_oos",
    }


def build_model(stage_c_dir: Path, output_dir: Path, allow_time_mismatch: bool = False):
    manifest = validate_manifest(stage_c_dir, allow_time_mismatch=allow_time_mismatch)
    frames, audit, inline_tolerance = load_event_files(stage_c_dir, manifest, allow_time_mismatch=allow_time_mismatch)
    output_dir.mkdir(parents=True, exist_ok=True)

    model = {
        "schema_version": "1.0",
        "model_type": "stage_d_weighted_historical_xauusd",
        "created_utc": utc_now(),
        "source_stage_c_dir": str(stage_c_dir.resolve()),
        "source_stage_c_generated_utc": manifest.get("generated_utc"),
        "classification": {
            "rule": "Compare numeric Actual and Forecast; above/below/inline.",
            "inline_tolerance": inline_tolerance,
            "groups": list(GROUPS),
        },
        "method": {
            "report_weight": "chronological expanding-window multiclass Brier Skill Score, adjusted for co-occurring out-of-sample signal correlation",
            "probability_estimation": f"Dirichlet-smoothed group frequencies; prior strength={DEFAULT_ALPHA} events toward report baseline",
            "combined_forecast": "skill-weighted mixture of report-specific conditional distributions; never sum per-report price moves",
            "joint_model": "ridge softmax trained only on co-timestamped multi-report releases and enabled only after positive walk-forward skill",
            "horizons_minutes": [5, 15],
            "direction_classes": list(CLASSES),
            "important": "Weights are statistical predictive utility, not causal shares of gold movement. Estimates are historical, not guarantees.",
        },
        "data_quality": {"report_audit": audit},
        "horizons": {},
    }

    all_oos_rows = []
    correlation_by_h = {}
    for horizon in (5, 15):
        oos_by_slug = {}
        backtest_stats = {}
        group_stats_by_slug = {}
        for slug, report_name in REPORTS:
            if slug not in frames:
                continue
            oos, stats = report_oos_backtest(frames[slug], horizon)
            for r in oos:
                r["report_slug"] = slug
                r["report"] = report_name
                all_oos_rows.append(r)
            oos_by_slug[slug] = oos
            backtest_stats[slug] = stats
            group_stats_by_slug[slug] = full_group_stats(frames[slug], horizon)

        corr_rows, redundancy = build_oos_signal_correlations(oos_by_slug)
        correlation_by_h[str(horizon)] = corr_rows

        raw_adjusted = {}
        eligible = []
        for slug, report_name in REPORTS:
            if slug not in frames:
                continue
            stats = backtest_stats.get(slug, {})
            oos_n = int(stats.get("oos_events") or 0)
            n_events = int(stats.get("events") or 0)
            bss = safe_float(stats.get("bss"))
            if n_events >= MIN_EVENTS_FOR_WEIGHT and oos_n >= MIN_OOS_FOR_WEIGHT:
                eligible.append(slug)
            if bss is not None and bss > 0 and oos_n >= MIN_OOS_FOR_WEIGHT:
                reliability = math.sqrt(oos_n / (oos_n + 20.0))
                redundancy_factor = 1.0 / (1.0 + redundancy.get(slug, 0.0))
                raw_adjusted[slug] = bss * reliability * redundancy_factor
            else:
                raw_adjusted[slug] = 0.0

        weights, weight_status = normalise_weights(raw_adjusted, eligible)
        report_models = {}
        for slug, report_name in REPORTS:
            if slug not in frames:
                continue
            stats = backtest_stats.get(slug, {})
            gs = group_stats_by_slug.get(slug, {})
            report_models[slug] = {
                "report": report_name,
                "events": int(gs.get("events", 0)),
                "base_probabilities": gs.get("base_probabilities", {c: 1/3 for c in CLASSES}),
                "walk_forward": stats,
                "mean_abs_oos_signal_correlation": redundancy.get(slug, 0.0),
                "adjusted_raw_skill": raw_adjusted.get(slug, 0.0),
                "weight": weights.get(slug, 0.0),
                "weight_pct": 100.0 * weights.get(slug, 0.0),
                "groups": gs.get("groups", {}),
            }

        joint = build_joint_events(frames, horizon)
        joint_oos, joint_stats = joint_oos_backtest(joint, horizon) if len(joint) else ([], {"events": 0, "oos_events": 0, "bss": None, "status": "no_joint_events"})
        all_oos_rows.extend([{**r, "report_slug": "__joint__", "report": "Joint eight-report model"} for r in joint_oos])
        joint_train = joint[joint["reports_present"] >= 2] if len(joint) else pd.DataFrame()
        coverage_counts = {}
        if len(joint_train):
            for k in range(2, len(REPORTS) + 1):
                coverage_counts[str(k)] = int((joint_train["reports_present"] >= k).sum())
        max_supported_count = max(
            [k for k in range(2, len(REPORTS) + 1) if coverage_counts.get(str(k), 0) >= MIN_JOINT_COVERAGE],
            default=0,
        )
        joint_bss_value = safe_float(joint_stats.get("bss"))
        joint_enabled = bool(
            joint_stats.get("status") == "ok"
            and joint_bss_value is not None
            and joint_bss_value >= MIN_JOINT_BSS
            and int(joint_stats.get("oos_events") or 0) >= MIN_JOINT_OOS
            and max_supported_count >= 2
        )
        joint_model_data = None
        if joint_enabled:
            X = make_joint_matrix(joint_train)
            labels = joint_train["direction"].tolist()
            W = fit_softmax(X, labels)
            joint_model_data = {"weights": W.tolist(), "l2_regularization": JOINT_L2,
                                "feature_order": [f"{slug}:{kind}" for slug, _ in REPORTS for kind in ("below", "above", "missing")],
                                "max_reports_present_in_training": int(joint_train["reports_present"].max()),
                                "max_supported_report_count": int(max_supported_count),
                                "coverage_counts_ge_k": coverage_counts,
                                "minimum_historical_coverage_per_k": MIN_JOINT_COVERAGE}

        # A global baseline from unique timestamps prevents eight coincident rows
        # from being counted as eight independent gold outcomes.
        if len(joint):
            global_counts = Counter(joint["direction"].tolist())
        else:
            all_dirs = []
            for df in frames.values():
                for hdf in [event_rows_for_horizon(df, horizon)]:
                    all_dirs.extend(hdf["direction"].tolist())
            global_counts = Counter(all_dirs)
        global_base = smoothed_base(global_counts)

        model["horizons"][str(horizon)] = {
            "global_baseline": {c: float(global_base[i]) for i, c in enumerate(CLASSES)},
            "report_weight_status": weight_status,
            "reports": report_models,
            "joint_model": {
                "enabled": joint_enabled,
                "walk_forward": joint_stats,
                "model": joint_model_data,
                "historical_joint_timestamps": int(len(joint)),
                "coverage_counts_ge_k": coverage_counts,
                "max_supported_report_count": int(max_supported_count),
                "minimum_bss_to_enable": MIN_JOINT_BSS,
            },
        }
        LOG.info("%dm: report weights status=%s; joint model=%s (BSS=%s)", horizon, weight_status,
                 "enabled" if joint_enabled else "disabled", joint_stats.get("bss"))

    safe_json_dump(output_dir / "stage_d_model.json", model)

    if all_oos_rows:
        pd.DataFrame(all_oos_rows).to_csv(output_dir / "stage_d_walk_forward_predictions.csv", index=False)
    for horizon, rows in correlation_by_h.items():
        pd.DataFrame(rows, columns=["report_a", "report_b", "coincident_oos_events", "signal_correlation", "absolute_correlation"]).to_csv(
            output_dir / f"stage_d_signal_correlations_{horizon}m.csv", index=False)

    weight_rows = []
    for horizon in (5, 15):
        hobj = model["horizons"][str(horizon)]
        for slug, report_name in REPORTS:
            r = hobj["reports"].get(slug)
            if not r:
                weight_rows.append({"horizon_minutes": horizon, "report_slug": slug, "report": report_name,
                                    "events": 0, "oos_events": 0, "bss": None, "weight_pct": 0.0,
                                    "weight_status": hobj["report_weight_status"]})
                continue
            weight_rows.append({
                "horizon_minutes": horizon, "report_slug": slug, "report": report_name,
                "events": r["events"], "oos_events": r["walk_forward"].get("oos_events"),
                "brier_model": r["walk_forward"].get("brier_model"),
                "brier_baseline": r["walk_forward"].get("brier_baseline"),
                "bss": r["walk_forward"].get("bss"),
                "mean_abs_oos_signal_correlation": r["mean_abs_oos_signal_correlation"],
                "adjusted_raw_skill": r["adjusted_raw_skill"], "weight_pct": r["weight_pct"],
                "weight_status": hobj["report_weight_status"],
            })
    pd.DataFrame(weight_rows).to_csv(output_dir / "stage_d_report_weights.csv", index=False)

    summary = make_summary_text(model)
    (output_dir / "stage_d_summary.txt").write_text(summary, encoding="utf-8")
    example_input = {
        SLUG_TO_NAME[slug]: {"actual": "", "forecast": ""}
        for slug, _ in REPORTS
    }
    safe_json_dump(output_dir / "stage_d_actuals_example.json", example_input)
    LOG.info("Saved model and reports to %s", output_dir)
    return model


def make_summary_text(model: dict) -> str:
    lines = [
        "STAGE D — XAUUSD WEIGHTED HISTORICAL FORECAST",
        f"Created UTC: {model.get('created_utc')}",
        "Weights are walk-forward predictive utility, not causal influence.",
        "Probabilities are historical conditional estimates, not guarantees.",
        "",
    ]
    for horizon in (5, 15):
        h = model["horizons"][str(horizon)]
        lines.append(f"HORIZON {horizon} MINUTES")
        lines.append(f"Weight method status: {h['report_weight_status']}")
        lines.append("Report weights:")
        rows = sorted(h["reports"].items(), key=lambda kv: kv[1].get("weight", 0), reverse=True)
        for slug, r in rows:
            bss = r["walk_forward"].get("bss")
            bss_s = f"{bss:.4f}" if bss is not None else "n/a"
            lines.append(f"  {r['report']}: {r['weight_pct']:.2f}% | BSS={bss_s} | events={r['events']} | OOS={r['walk_forward'].get('oos_events', 0)}")
        j = h["joint_model"]
        lines.append(f"Joint model: {'ENABLED' if j['enabled'] else 'DISABLED'} | status={j['walk_forward'].get('status')} | BSS={j['walk_forward'].get('bss')}")
        lines.append("")
        lines.append("Historical group probabilities (P up/down/flat):")
        for slug, r in h["reports"].items():
            lines.append(f"  {r['report']}")
            for group in GROUPS:
                g = r["groups"].get(group)
                if not g:
                    continue
                p = g["probabilities"]
                lines.append(f"    {group}: {p['up']*100:.1f}/{p['down']*100:.1f}/{p['flat']*100:.1f}% (n={g['events']}) | E[move]={g['mean_change_usd']:+.3f}$ | mean|move|={g['mean_abs_change_usd']:.3f}$")
        lines.append("")
    return "\n".join(lines)


def resolve_input_report_key(value: str) -> Optional[str]:
    s = str(value).strip().lower()
    if s in SLUG_TO_NAME:
        return s
    if s in NAME_TO_SLUG:
        return NAME_TO_SLUG[s]
    # also accept underscore/hyphen variations of the slug
    norm = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return norm if norm in SLUG_TO_NAME else None


def classify_actuals(actual: Any, forecast: Any, tolerance: float) -> Optional[str]:
    a = parse_numeric(actual)
    f = parse_numeric(forecast)
    if a is None or f is None:
        return None
    diff = a - f
    if diff > tolerance:
        return "above_forecast"
    if diff < -tolerance:
        return "below_forecast"
    return "inline"


def predict_from_actuals(model: dict, actuals: dict[str, Any]) -> dict:
    """Predict XAUUSD reaction from supplied Actual/Forecast values.

    actuals keys may be report slugs or exact report names. Values should be
    {"actual": ..., "forecast": ...}. Unreported/unknown keys are ignored.
    """
    tol = float(model.get("classification", {}).get("inline_tolerance", 0.0))
    classified = {}
    for key, values in (actuals or {}).items():
        slug = resolve_input_report_key(key)
        if slug is None or not isinstance(values, dict):
            continue
        group = classify_actuals(values.get("actual"), values.get("forecast"), tol)
        a = parse_numeric(values.get("actual"))
        f = parse_numeric(values.get("forecast"))
        if group is not None:
            classified[slug] = {
                "report": SLUG_TO_NAME[slug], "actual": values.get("actual"), "forecast": values.get("forecast"),
                "actual_numeric": a, "forecast_numeric": f, "surprise": (a - f), "group": group,
            }

    if not classified:
        raise ValueError("No usable Actual/Forecast pairs were found in the input JSON.")

    result = {
        "schema_version": "1.0",
        "generated_utc": utc_now(),
        "model_created_utc": model.get("created_utc"),
        "classified_reports": classified,
        "reports_received": len(classified),
        "horizons": {},
        "warnings": [],
    }

    for horizon in (5, 15):
        h = model["horizons"][str(horizon)]
        baseline = np.array([h["global_baseline"].get(c, 1/3) for c in CLASSES], dtype=float)
        contributions = []
        for slug, info in classified.items():
            report_model = h["reports"].get(slug)
            if not report_model:
                continue
            group_stats = report_model.get("groups", {}).get(info["group"])
            if not group_stats:
                continue
            probabilities = group_stats.get("probabilities", {})
            p = np.array([probabilities.get(c, float(baseline[i])) for i, c in enumerate(CLASSES)], dtype=float)
            contributions.append({
                "slug": slug, "report": info["report"], "group": info["group"],
                "weight": float(report_model.get("weight", 0.0)),
                "probabilities": p,
                "mean_change_usd": safe_float(group_stats.get("mean_change_usd")),
                "mean_abs_change_usd": safe_float(group_stats.get("mean_abs_change_usd")),
                "median_change_usd": safe_float(group_stats.get("median_change_usd")),
                "events": int(group_stats.get("events", 0)),
                "bss": safe_float(report_model.get("walk_forward", {}).get("bss")),
            })

        if not contributions:
            raise ValueError(f"No matching historical templates available for {horizon}m.")

        joint_meta = h.get("joint_model", {})
        joint_used = False
        joint_reason = "disabled_or_not_enough_historical_joint_skill"
        probs = None
        active_count = len(contributions)
        joint_model = joint_meta.get("model")
        if joint_meta.get("enabled") and joint_model and active_count >= 2:
            max_hist = int(joint_model.get("max_supported_report_count", 0))
            if active_count <= max_hist:
                feat = np.zeros((1, len(REPORTS) * 3), dtype=float)
                class_map = {c["slug"]: c["group"] for c in contributions}
                for i, (slug, _) in enumerate(REPORTS):
                    base = i * 3
                    group = class_map.get(slug)
                    if group == "below_forecast":
                        feat[0, base] = 1.0
                    elif group == "above_forecast":
                        feat[0, base + 1] = 1.0
                    elif group is None:
                        feat[0, base + 2] = 1.0
                W = np.asarray(joint_model["weights"], dtype=float)
                probs = softmax_predict(W, feat)[0]
                joint_used = True
                joint_reason = "joint_model_positive_walk_forward_bss"
            else:
                joint_reason = f"received_{active_count}_reports_but_joint_history_max_was_{max_hist}"

        if not joint_used:
            positive = [c for c in contributions if c["weight"] > 0]
            if positive:
                chosen = positive
                weight_status = h.get("report_weight_status", "walk_forward_skill")
                raw_weights = np.array([c["weight"] for c in chosen], dtype=float)
                w = raw_weights / raw_weights.sum()
            else:
                chosen = contributions
                w = np.ones(len(chosen), dtype=float) / len(chosen)
                weight_status = "equal_fallback_no_positive_skill_among_received_reports"
            probs = np.sum(np.vstack([c["probabilities"] for c in chosen]) * w[:, None], axis=0)
            contributions = chosen
        else:
            weight_status = "joint_softmax_model"

        # Re-normalise for floating point drift.
        probs = np.clip(probs, 0.0, 1.0)
        probs = probs / probs.sum() if probs.sum() > 0 else baseline

        weights_for_strength = np.array([max(0.0, c["weight"]) for c in contributions], dtype=float)
        if weights_for_strength.sum() <= 0:
            weights_for_strength = np.ones(len(contributions), dtype=float)
        weights_for_strength /= weights_for_strength.sum()
        move_values = np.array([c["mean_change_usd"] if c["mean_change_usd"] is not None else 0.0 for c in contributions])
        abs_values = np.array([c["mean_abs_change_usd"] if c["mean_abs_change_usd"] is not None else 0.0 for c in contributions])
        med_values = np.array([c["median_change_usd"] if c["median_change_usd"] is not None else 0.0 for c in contributions])
        expected_move = float(np.sum(move_values * weights_for_strength))
        expected_abs = float(np.sum(abs_values * weights_for_strength))
        weighted_median = float(np.sum(med_values * weights_for_strength))
        weighted_events = float(np.sum(weights_for_strength * np.array([c["events"] for c in contributions], dtype=float)))

        result["horizons"][str(horizon)] = {
            "probability_up_pct": float(probs[0] * 100),
            "probability_down_pct": float(probs[1] * 100),
            "probability_flat_pct": float(probs[2] * 100),
            "expected_change_usd": expected_move,
            "expected_absolute_move_usd": expected_abs,
            "weighted_median_change_usd": weighted_median,
            "weighted_supporting_events": weighted_events,
            "method_used": "joint_softmax" if joint_used else "skill_weighted_mixture",
            "joint_model_reason": joint_reason,
            "weight_status": weight_status,
            "report_contributions": [
                {"report": c["report"], "group": c["group"], "events": c["events"],
                 "standalone_weight_pct": 100.0 * (c["weight"] / sum(x["weight"] for x in contributions)) if sum(x["weight"] for x in contributions) > 0 else 100.0 / len(contributions),
                 "used_in_probability_mixture": not joint_used,
                 "historical_p_up_pct": float(c["probabilities"][0] * 100),
                 "historical_p_down_pct": float(c["probabilities"][1] * 100),
                 "mean_change_usd": c["mean_change_usd"], "mean_abs_change_usd": c["mean_abs_change_usd"],
                 "bss": c["bss"]}
                for c in contributions
            ],
        }

    if len(classified) < len(REPORTS):
        result["warnings"].append(f"Only {len(classified)}/8 report Actual/Forecast pairs supplied; forecast uses available reports only.")
    if any(h.get("weight_status", "").startswith("equal_fallback") for h in result["horizons"].values()):
        result["warnings"].append("At least one horizon has no positive historical skill among the supplied reports; equal weights are a fallback, not evidence of predictive edge.")
    return result


def format_telegram_message(prediction: dict) -> str:
    lines = ["<b>XAUUSD — историческая оценка реакции</b>",
             f"Отчётов учтено: {prediction.get('reports_received', 0)}/8", ""]
    for h in (5, 15):
        x = prediction["horizons"][str(h)]
        lines += [f"<b>Горизонт {h} мин</b>",
                  f"Рост: <b>{x['probability_up_pct']:.1f}%</b>",
                  f"Падение: <b>{x['probability_down_pct']:.1f}%</b>",
                  f"Без движения: {x['probability_flat_pct']:.1f}%",
                  f"Историческое среднее изменение: {x['expected_change_usd']:+.2f}$",
                  f"Средний модуль движения: {x['expected_absolute_move_usd']:.2f}$",
                  f"Модель: {x['method_used']} | взвешенная история≈{x['weighted_supporting_events']:.0f}"]
        if x["method_used"] == "skill_weighted_mixture":
            lines.append("Веса используемых отчётов:")
            for c in sorted(x["report_contributions"], key=lambda item: item.get("standalone_weight_pct", 0), reverse=True):
                lines.append(f"  {c['report']}: {c['standalone_weight_pct']:.1f}% ({c['group']})")
        else:
            lines.append("Взаимодействие публикаций учтено совместной моделью; отдельные веса не суммируются.")
        lines.append("")
    if prediction.get("warnings"):
        lines.append("<b>Примечания</b>")
        lines.extend("• " + w for w in prediction["warnings"])
    lines.append("Оценка основана на исторических реакциях; не является гарантией движения.")
    return "\n".join(lines)


def load_model(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as f:
        model = json.load(f)
    if model.get("model_type") != "stage_d_weighted_historical_xauusd":
        raise ValueError("This JSON is not a Stage D model file.")
    return model


def main():
    parser = argparse.ArgumentParser(description="Build Stage D weighted XAUUSD forecast model and optionally predict from Actual/Forecast.")
    parser.add_argument("--stage-c-dir", type=Path, required=True,
                        help="Path to output_stage_c_corrected generated by Stage C corrected analyzer.")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Directory where Stage D model, weights and reports will be written.")
    parser.add_argument("--input-json", type=Path,
                        help="Optional JSON with new Actual/Forecast values. Writes stage_d_prediction.json.")
    parser.add_argument("--allow-time-mismatch", action="store_true",
                        help="Diagnostic only: bypass checks for corrected 08:30 New York event timestamps.")
    args = parser.parse_args()

    model = build_model(args.stage_c_dir, args.output_dir, allow_time_mismatch=args.allow_time_mismatch)
    if args.input_json:
        with args.input_json.open("r", encoding="utf-8") as f:
            actuals = json.load(f)
        pred = predict_from_actuals(model, actuals)
        safe_json_dump(args.output_dir / "stage_d_prediction.json", pred)
        (args.output_dir / "stage_d_telegram_message.html").write_text(format_telegram_message(pred), encoding="utf-8")
        print("\n" + format_telegram_message(pred).replace("<b>", "").replace("</b>", ""))
    else:
        print(f"\nModel created: {args.output_dir / 'stage_d_model.json'}")
        print(f"Weights:       {args.output_dir / 'stage_d_report_weights.csv'}")
        print(f"Summary:       {args.output_dir / 'stage_d_summary.txt'}")
        print("To produce release probabilities, pass --input-json stage_d_actuals.json")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        LOG.error("%s", exc)
        sys.exit(1)
