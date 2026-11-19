#!/usr/bin/env python3
"""
Core Retail Sales — Stage 1 pipeline (v1.1)
============================================
Adds: conditional probabilities (direction | not flat),
directional_edge, signal_quality, DXY tables, per-period breakdown.
"""

from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# ============ CONFIG: paths ============
EVENT_REACTIONS_CSV = Path("retail_reaction_analysis/event_reactions.csv")
OUTPUT_DIR = Path("output/stage1")

XAU_BID_DIR = None
XAU_ASK_DIR = None
FX_PAIRS_DIRS = {p: None for p in ["EURUSD", "GBPUSD", "USDCAD", "USDCHF", "USDJPY", "USDSEK"]}

# ============ CONFIG: analysis ============
HORIZONS = [1, 5, 15, 30, 60, 120]
HORIZON_PRIORITY = {1: "context", 5: "main", 15: "main", 30: "secondary", 60: "context", 120: "context"}
INSTRUMENTS = ["XAU", "DXY"]
RETURN_COL = {"XAU": "xau_mid_return_pct_m{}", "DXY": "dxy_return_pct_m{}"}
QUALITY_COL = {"XAU": "xau_mid_quality", "DXY": "dxy_quality"}

PERIODS = [
    ("P1_2007_2012", 2007, 2012),
    ("P2_2013_2019", 2013, 2019),
    ("P3_2020_2022", 2020, 2022),
    ("P4_2023_2025", 2023, 2026),
]
LATEST_PERIOD = "P4_2023_2026"

SIZE_QUANTILES = [1 / 3, 2 / 3]
FLAT_MULTIPLIER = 0.5
MIN_PUBLISH_N = 15
MIN_PRELIM_N = 10
SCHEMA_VERSION = "1.1"
EPS = 1e-9

try:
    from scipy import stats as _sstats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


# ============ helpers ============
def _sign_of(x):
    if pd.isna(x):
        return "missing"
    if abs(x) < EPS:
        return "inline"
    return "beat" if x > 0 else "miss"


def _period_of(year):
    if pd.isna(year):
        return None
    y = int(year)
    for name, a, b in PERIODS:
        if a <= y <= b:
            return name
    return None


def wilson_ci(p, n, z=1.96):
    if n == 0:
        return (None, None)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    s = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (float(max(0.0, c - s)), float(min(1.0, c + s)))


def mean_ci(mean, std, n, conf=0.95):
    if n < 2:
        return (None, None)
    se = std / np.sqrt(n)
    if HAS_SCIPY:
        t = _sstats.t.ppf((1 + conf) / 2, n - 1) if n < 30 else _sstats.norm.ppf((1 + conf) / 2)
    else:
        t = 2.0 if n < 30 else 1.96
    return (float(mean - t * se), float(mean + t * se))


def reliability_of(n):
    if n >= MIN_PUBLISH_N:
        return "reliable"
    if n >= MIN_PRELIM_N:
        return "preliminary"
    return "reference"


def quality_of(p_dir, edge):
    """Сигнал качества на основе условной точности и величины edge."""
    if p_dir is None or edge is None:
        return None
    if p_dir >= 0.85 and edge >= 0.50:
        return "strong"
    if p_dir >= 0.70 and edge >= 0.30:
        return "moderate"
    if p_dir >= 0.60 and edge >= 0.15:
        return "weak"
    return "noise"


def compute_stats(returns, flat_threshold):
    r = returns.dropna()
    n = len(r)
    if n == 0:
        return {"n": 0, "reliability": "empty"}
    mean = float(r.mean())
    median = float(r.median())
    std = float(r.std(ddof=1)) if n > 1 else 0.0
    q25 = float(r.quantile(0.25))
    q75 = float(r.quantile(0.75))
    up = int((r >= flat_threshold).sum())
    down = int((r <= -flat_threshold).sum())
    flat = int((r.abs() < flat_threshold).sum())
    rel = reliability_of(n)
    out = {
        "n": n,
        "mean": mean,
        "median": median,
        "std": std,
        "iqr": q75 - q25,
        "flat_threshold": float(flat_threshold),
        "reliability": rel,
    }
    if rel != "reference":
        p_up, p_down, p_flat = up / n, down / n, flat / n
        up_ci, dn_ci, fl_ci = wilson_ci(p_up, n), wilson_ci(p_down, n), wilson_ci(p_flat, n)
        m_ci = mean_ci(mean, std, n)

        # условные вероятности направления при !flat
        non_flat = up + down
        p_up_dir = up / non_flat if non_flat > 0 else None
        p_down_dir = down / non_flat if non_flat > 0 else None
        dir_p = max(p_up_dir, p_down_dir) if non_flat > 0 else None
        edge = (max(p_up, p_down) - min(p_up, p_down)) if non_flat > 0 else None
        direction_lean = "up" if (p_up_dir or 0) > (p_down_dir or 0) else "down"

        out.update({
            "p_up": p_up, "p_down": p_down, "p_flat": p_flat,
            "p_up_ci_lo": up_ci[0], "p_up_ci_hi": up_ci[1],
            "p_down_ci_lo": dn_ci[0], "p_down_ci_hi": dn_ci[1],
            "p_flat_ci_lo": fl_ci[0], "p_flat_ci_hi": fl_ci[1],
            "mean_ci_lo": m_ci[0], "mean_ci_hi": m_ci[1],
            "non_flat_n": non_flat,
            "p_up_dir": p_up_dir,
            "p_down_dir": p_down_dir,
            "directional_accuracy": dir_p,
            "directional_edge": edge,
            "direction_lean": direction_lean if non_flat > 0 else None,
            "signal_quality": quality_of(dir_p, edge),
        })
    return out


# ============ main ============
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_lines = []

    def log(msg):
        print(msg)
        log_lines.append(str(msg))

    log(f"Loading {EVENT_REACTIONS_CSV}")
    if not EVENT_REACTIONS_CSV.exists():
        raise FileNotFoundError(EVENT_REACTIONS_CSV)

    df = pd.read_csv(
        EVENT_REACTIONS_CSV, dtype=str,
        na_values=["", "n/a", "N/A", "NA", "null", "None"],
    )

    num_cols = [
        "actual_numeric", "forecast_numeric", "previous_numeric",
        "surprise_actual_minus_forecast", "previous_value_revision",
        "days_between_releases_for_revision_check", "headline_retail_surprise",
    ]
    for h in HORIZONS:
        num_cols.append(RETURN_COL["XAU"].format(h))
        num_cols.append(RETURN_COL["DXY"].format(h))
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["release_date"] = pd.to_datetime(df["release_date"], errors="coerce")
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["analysis_period"] = df["year"].apply(_period_of)
    df["surprise_sign_lc"] = df["surprise_sign"].fillna("missing").str.lower()

    log(f"Loaded {len(df)} rows  {df['release_date'].min().date()} -> {df['release_date'].max().date()}")

    # ---- validation (кратко) ----
    log("\n=== VALIDATION ===")
    if "surprise_actual_minus_forecast" in df.columns:
        comp = df["surprise_actual_minus_forecast"].apply(_sign_of)
        mism = (comp != df["surprise_sign_lc"]) & (comp != "missing") & (df["surprise_sign_lc"] != "missing")
        log(f"  sign mismatches: {int(mism.sum())}")
    if "previous_value_revision" in df.columns:
        log(f"  revision populated: {int(df['previous_value_revision'].notna().sum())}/{len(df)}")
    for inst in INSTRUMENTS:
        q = QUALITY_COL[inst]
        if q in df.columns:
            log(f"  {inst} OK quality: {int((df[q] == 'OK').sum())}/{len(df)}")

    # ---- surprise distribution ----
    log("\n=== SURPRISE DISTRIBUTION ===")
    s_all = df["surprise_actual_minus_forecast"].dropna()
    s_beat = df.loc[df["surprise_sign_lc"] == "beat", "surprise_actual_minus_forecast"].dropna()
    s_miss = df.loc[df["surprise_sign_lc"] == "miss", "surprise_actual_minus_forecast"].dropna()
    s_miss_abs = s_miss.abs()

    def dist_row(label, s):
        row = {"segment": label, "n": int(len(s))}
        if len(s) == 0:
            return row
        for q in (0.10, 0.25, 0.50, 0.75, 0.90):
            row[f"q{int(q * 100)}"] = float(s.quantile(q))
        row["mean"] = float(s.mean())
        row["std"] = float(s.std(ddof=1)) if len(s) > 1 else 0.0
        row["min"] = float(s.min())
        row["max"] = float(s.max())
        return row

    pd.DataFrame([
        dist_row("ALL", s_all),
        dist_row("BEAT", s_beat),
        dist_row("MISS", s_miss),
        dist_row("MISS_ABS", s_miss_abs),
    ]).to_csv(OUTPUT_DIR / "surprise_distribution.csv", index=False)

    # ---- size boundaries ----
    q33_beat = float(s_beat.quantile(SIZE_QUANTILES[0]))
    q67_beat = float(s_beat.quantile(SIZE_QUANTILES[1]))
    q33_miss = float(s_miss_abs.quantile(SIZE_QUANTILES[0]))
    q67_miss = float(s_miss_abs.quantile(SIZE_QUANTILES[1]))
    pd.DataFrame([
        {"sign": "beat", "boundary": "q33", "value": q33_beat},
        {"sign": "beat", "boundary": "q67", "value": q67_beat},
        {"sign": "miss", "boundary": "q33", "value": q33_miss},
        {"sign": "miss", "boundary": "q67", "value": q67_miss},
    ]).to_csv(OUTPUT_DIR / "size_group_boundaries.csv", index=False)

    def size_of(row):
        sn = row.get("surprise_sign_lc")
        sv = row.get("surprise_actual_minus_forecast")
        if pd.isna(sv):
            return None
        mag = abs(sv)
        if sn == "beat":
            return "small" if mag <= q33_beat else ("medium" if mag <= q67_beat else "large")
        if sn == "miss":
            return "small" if mag <= q33_miss else ("medium" if mag <= q67_miss else "large")
        if sn == "inline":
            return "inline"
        return None

    df["size_group"] = df.apply(size_of, axis=1)

    # ---- flat thresholds ----
    flat_pooled = {inst: {} for inst in INSTRUMENTS}
    flat_period = {inst: {h: {} for h in HORIZONS} for inst in INSTRUMENTS}
    sigma_n = {inst: {h: {} for h in HORIZONS} for inst in INSTRUMENTS}

    for inst in INSTRUMENTS:
        qcol = QUALITY_COL[inst]
        ok_mask = (df[qcol] == "OK") if qcol in df.columns else pd.Series(True, index=df.index)
        for h in HORIZONS:
            col = RETURN_COL[inst].format(h)
            if col not in df.columns:
                continue
            r_all = df.loc[ok_mask, col].dropna()
            sigma = float(r_all.std(ddof=1)) if len(r_all) > 1 else 0.0
            flat_pooled[inst][h] = FLAT_MULTIPLIER * sigma
            for pname, _, _ in PERIODS:
                r_p = df.loc[ok_mask & (df["analysis_period"] == pname), col].dropna()
                sig_p = float(r_p.std(ddof=1)) if len(r_p) > 1 else 0.0
                flat_period[inst][h][pname] = FLAT_MULTIPLIER * sig_p
                sigma_n[inst][h][pname] = int(len(r_p))

    # ---- templates ----
    log("\n=== BUILDING TEMPLATES ===")
    templates = []
    stats_rows = []
    for sn in ["beat", "miss", "inline"]:
        sizes = ["small", "medium", "large"] if sn != "inline" else ["inline"]
        for sz in sizes:
            tpl_id = f"{sn}__{sz}"
            tpl = {
                "template_id": tpl_id,
                "conditions": {"surprise_sign": sn, "size_group": sz},
                "instruments": {},
            }
            for inst in INSTRUMENTS:
                qcol = QUALITY_COL[inst]
                ok_mask = (df[qcol] == "OK") if qcol in df.columns else pd.Series(True, index=df.index)
                base = df[(df["surprise_sign_lc"] == sn) & (df["size_group"] == sz) & ok_mask]
                horizons = {}
                for h in HORIZONS:
                    col = RETURN_COL[inst].format(h)
                    if col not in df.columns:
                        continue
                    all_stats = compute_stats(base[col], flat_pooled[inst].get(h, 0.0))
                    period_stats = {}
                    for pname, _, _ in PERIODS:
                        psub = base[base["analysis_period"] == pname]
                        period_stats[pname] = compute_stats(
                            psub[col], flat_period[inst][h].get(pname, 0.0)
                        )
                        stats_rows.append({
                            "template_id": tpl_id, "instrument": inst, "horizon_min": h,
                            "priority": HORIZON_PRIORITY[h], "scope": "period",
                            "period": pname, **period_stats[pname],
                        })
                    horizons[str(h)] = {
                        "priority": HORIZON_PRIORITY[h],
                        "all_periods": all_stats,
                        "periods": period_stats,
                    }
                    stats_rows.append({
                        "template_id": tpl_id, "instrument": inst, "horizon_min": h,
                        "priority": HORIZON_PRIORITY[h], "scope": "all",
                        "period": "all", **all_stats,
                    })
                tpl["instruments"][inst] = {"horizons": horizons}
            templates.append(tpl)

    # ---- write JSON ----
    out_json = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_range": {
            "start": str(df["release_date"].min().date()),
            "end": str(df["release_date"].max().date()),
            "n_events_total": int(len(df)),
        },
        "notes": [
            "Returns in percent: (P_t / P_0 - 1) * 100.",
            "flat_threshold = 0.5 * sigma(period, instrument, horizon).",
            "p_up/p_down/p_flat — по всем событиям (включая flat).",
            "p_up_dir/p_down_dir — условные вероятности направления при !flat (рекомендуется для бота).",
            "directional_accuracy = max(p_up_dir, p_down_dir).",
            "directional_edge = max(p_up,p_down) - min(p_up,p_down).",
            "signal_quality: strong (>=85%, edge>=0.5) | moderate (>=70%, edge>=0.3) | weak (>=60%, edge>=0.15) | noise.",
            "reliability: n>=15 reliable, n=10..14 preliminary, n<10 reference.",
        ],
        "size_group_boundaries": {
            "beat": {"q33": q33_beat, "q67": q67_beat, "n": int(len(s_beat)), "unit": "pp Actual-Forecast"},
            "miss": {"q33": q33_miss, "q67": q67_miss, "n": int(len(s_miss)), "unit": "pp |Actual-Forecast|"},
        },
        "flat_thresholds": {
            "method": "0.5 * sigma",
            "pooled": {inst: {str(h): float(v) for h, v in flat_pooled[inst].items()} for inst in INSTRUMENTS},
            "per_period": {inst: {str(h): {p: float(v) for p, v in d.items()} for h, d in flat_period[inst].items()} for inst in INSTRUMENTS},
            "sigma_n": {inst: {str(h): d for h, d in sigma_n[inst].items()} for inst in INSTRUMENTS},
        },
        "templates": templates,
    }
    with open(OUTPUT_DIR / "scenario_templates.json", "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2, default=str)

    # ---- write CSV ----
    stats_df = pd.DataFrame(stats_rows)
    ordered = [
        "template_id", "instrument", "horizon_min", "priority", "scope", "period",
        "n", "reliability", "mean", "median", "std", "iqr",
        "p_up", "p_down", "p_flat",
        "non_flat_n", "p_up_dir", "p_down_dir",
        "directional_accuracy", "directional_edge",
        "direction_lean", "signal_quality",
        "p_up_ci_lo", "p_up_ci_hi", "p_down_ci_lo", "p_down_ci_hi",
        "p_flat_ci_lo", "p_flat_ci_hi", "mean_ci_lo", "mean_ci_hi",
        "flat_threshold",
    ]
    stats_df = stats_df[[c for c in ordered if c in stats_df.columns] +
                        [c for c in stats_df.columns if c not in ordered]]
    stats_df.to_csv(OUTPUT_DIR / "group_stats.csv", index=False)
    stats_df.to_csv(OUTPUT_DIR / "scenario_templates.csv", index=False)
    log(f"  saved CSV/JSON, {len(stats_df)} rows")

    # ==========================================================
    # MARKDOWN REPORT (расширенный)
    # ==========================================================
    def fmt(v, spec=".4f"):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        try:
            return format(v, spec)
        except (TypeError, ValueError):
            return str(v)

    def table_for(instrument, horizon, scope, period=None):
        """Таблица для instrument × horizon × scope [× period]."""
        rows = []
        for tid in template_ids:
            sel = stats_df[
                (stats_df["template_id"] == tid) &
                (stats_df["instrument"] == instrument) &
                (stats_df["horizon_min"] == horizon) &
                (stats_df["scope"] == scope)
                ]
            if period is not None:
                sel = sel[sel["period"] == period]
            if len(sel) == 0:
                continue
            r = sel.iloc[0].to_dict()
            rows.append(
                f"| {tid.replace('__', ' ')} | {int(r['n'])} | {r.get('reliability', '—')} | "
                f"{fmt(r.get('mean'))} | {fmt(r.get('median'))} | "
                f"{fmt(r.get('p_up'), '.3f')} | {fmt(r.get('p_down'), '.3f')} | "
                f"{fmt(r.get('p_flat'), '.3f')} | "
                f"{fmt(r.get('p_up_dir'), '.3f')} | {fmt(r.get('p_down_dir'), '.3f')} | "
                f"{fmt(r.get('directional_accuracy'), '.3f')} | {fmt(r.get('directional_edge'), '.3f')} | "
                f"{r.get('signal_quality', '—')} |\n"
            )
        if not rows:
            return ["_нет данных_\n"]
        header = (
            "| template | n | reliability | mean | median | "
            "p_up | p_down | p_flat | "
            "p_up\\|dir | p_down\\|dir | dir_acc | dir_edge | quality |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
        )
        return [header] + rows

    template_ids = []
    for sn in ["beat", "miss", "inline"]:
        sizes = ["small", "medium", "large"] if sn != "inline" else ["inline"]
        for sz in sizes:
            template_ids.append(f"{sn}__{sz}")

    md = ["# Core Retail Sales — Stage 1 Report\n\n",
          f"Generated: {out_json['generated_at']}\n\n",
          f"Data: {len(df)} events, {out_json['data_range']['start']} → {out_json['data_range']['end']}\n\n",
          "## Size boundaries\n\n",
          f"- Beat small: |s| ≤ {q33_beat:.3f} (n={len(s_beat)})\n",
          f"- Beat medium: {q33_beat:.3f} < |s| ≤ {q67_beat:.3f}\n",
          f"- Beat large: |s| > {q67_beat:.3f}\n",
          f"- Miss small: |s| ≤ {q33_miss:.3f} (n={len(s_miss)})\n",
          f"- Miss medium: {q33_miss:.3f} < |s| ≤ {q67_miss:.3f}\n",
          f"- Miss large: |s| > {q67_miss:.3f}\n\n",
          "## Как читать таблицы\n\n",
          "- **p_up / p_down / p_flat** — доли по всем событиям, включая боковик.\n",
          "- **p_up\\|dir / p_down\\|dir** — условные вероятности при `!flat`. **Это главная метрика для бота.**\n",
          "- **dir_acc** = max(p_up\\|dir, p_down\\|dir). Показывает, насколько точно угадывается направление, когда движение вообще есть.\n",
          "- **dir_edge** = max(p_up, p_down) − min(p_up, p_down). Насколько сильно выражено направление.\n",
          "- **quality**: strong / moderate / weak / noise.\n\n",
          ]

    for inst in INSTRUMENTS:
        md.append(f"\n# {inst}\n")
        for h in (5, 15, 30):
            prio = HORIZON_PRIORITY[h]
            md.append(f"\n## {inst} — m{h} ({prio}) — все периоды\n\n")
            md += table_for(inst, h, "all")
            md.append("\n")

    # ---- per-period XAU m5 ----
    md.append("\n# Разбивка по периодам — XAU m5\n\n")
    for pname, _, _ in PERIODS:
        md.append(f"\n## {pname}\n\n")
        md += table_for("XAU", 5, "period", period=pname)
        md.append("\n")

    # ---- per-period DXY m5 ----
    md.append("\n# Разбивка по периодам — DXY m5\n\n")
    for pname, _, _ in PERIODS:
        md.append(f"\n## {pname}\n\n")
        md += table_for("DXY", 5, "period", period=pname)
        md.append("\n")

    # ---- сводка strong/moderate ----
    md.append("\n# Сводка: сценарии с рабочим сигналом (m5, all periods)\n\n")
    md.append("| instrument | template | n | direction | dir_acc | dir_edge | quality |\n")
    md.append("|---|---|---|---|---|---|---|\n")
    for inst in INSTRUMENTS:
        for tid in template_ids:
            sel = stats_df[
                (stats_df["template_id"] == tid) &
                (stats_df["instrument"] == inst) &
                (stats_df["horizon_min"] == 5) &
                (stats_df["scope"] == "all")
                ]
            if len(sel) == 0:
                continue
            r = sel.iloc[0].to_dict()
            q = r.get("signal_quality")
            if q in ("strong", "moderate"):
                md.append(
                    f"| {inst} | {tid.replace('__', ' ')} | {int(r['n'])} | "
                    f"{r.get('direction_lean', '—')} | {fmt(r.get('directional_accuracy'), '.3f')} | "
                    f"{fmt(r.get('directional_edge'), '.3f')} | {q} |\n"
                )

    with open(OUTPUT_DIR / "stage1_report.md", "w", encoding="utf-8") as f:
        f.writelines(md)
    log("  saved stage1_report.md")

    with open(OUTPUT_DIR / "validation_log.txt", "w", encoding="utf-8") as f:
        f.writelines(l + "\n" for l in log_lines)
    log("\nDONE")


if __name__ == "__main__":
    main()