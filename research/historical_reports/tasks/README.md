# Historical XAUUSD tasks

The package provides a stable interface over the three existing calculation
engines. During this migration, the root-level scripts remain the source of
truth for the algorithms. The adapter deliberately delegates to them rather
than duplicating their calculations.

## Unified CLI

Run from the repository root:

```powershell
# 1. All-report descriptive reaction analysis
py -3 -m research.historical_reports.tasks multi-event --economic-data .\data\economic_data --bid-root E:\work\XAU_gold\XAUUSD\raw\BID --ask-root E:\work\XAU_gold\XAUUSD\raw\ASK --output .\artifacts\research\multi_event

# 2. Corrected Stage C: eight scheduled US releases
py -3 -m research.historical_reports.tasks stage-c --calendar-root .\data\economic_data --quotes-root E:\work\XAU_gold\XAUUSD\raw --output-dir .\artifacts\research\stage_c_corrected

# 3. Stage D: train weighted model from Stage C
py -3 -m research.historical_reports.tasks stage-d --stage-c-dir .\artifacts\research\stage_c_corrected --output-dir .\artifacts\research\stage_d

# 4. Stage D: create a prediction from release Actual/Forecast values
py -3 -m research.historical_reports.tasks stage-d --stage-c-dir .\artifacts\research\stage_c_corrected --output-dir .\artifacts\research\stage_d --input-json .\stage_d_actuals_example.json
```

Each task retains its original arguments and validation. The original commands
remain supported for backward compatibility.

## Python API

```python
from research.historical_reports.tasks import load_legacy_task

multi_event = load_legacy_task("multi-event")
stage_c = load_legacy_task("stage-c")
stage_d = load_legacy_task("stage-d")
```

The modules expose the same functions as the original scripts. Prefer the
package import for new integrations; do not copy calculation code into callers.

## Pipeline order

- Discovery: `python -m research.historical_reports.discovery`
- All-report analysis: `tasks multi-event`
- Corrected eight-report analysis: `tasks stage-c`
- Weighted out-of-sample model: `tasks stage-d`

The all-report analyzer and Stage C are separate research products, not
sequential stages. Stage D consumes only the corrected Stage C event files and
manifest. The unified CLI does not automatically chain them; explicit paths
make each run reproducible and prevent accidental use of stale outputs.
