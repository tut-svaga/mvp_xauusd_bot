"""Compatibility entry point for the modular Discovery package.

Run from the repository root:
    py -3 -m research.historical_reports.discovery

Or directly:
    py -3 research/historical_reports/discovery.py

Optional XAUUSD quote inventory:
    py -3 -m research.historical_reports.discovery --xau-root "E:\\work\\XAU_gold\\XAUUSD\\raw"


    Скрипт запускает последовательность скриптов для проверки csv Данных отчета и золота


    
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.historical_reports.discovery.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
