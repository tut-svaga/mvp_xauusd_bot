"""Compatibility entry point for the modular Discovery package.

Run from the repository root:
    py -3 -m research.historical_reports.discovery

Optional XAUUSD quote inventory:
    py -3 -m research.historical_reports.discovery --xau-root "E:\\work\\XAU_gold\\XAUUSD\\raw"

The implementation lives in research/historical_reports/discovery/.
"""
from research.historical_reports.discovery.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
