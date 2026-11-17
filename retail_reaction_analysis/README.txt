RETAIL REACTION ANALYSIS OUTPUTS
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
