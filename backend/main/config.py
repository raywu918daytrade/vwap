"""Runtime settings for the slim market-data monitor."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env", override=True)

# Header/watchlist quotes. These are monitored directly from realtime M1 bars and
# are not tied to any strategy candidate filter.
WATCHLIST_QUOTES = [
    s.strip() for s in os.environ.get("WATCHLIST_QUOTES", "0050").split(",") if s.strip()
]

# Taiwan regular trading session close. Realtime collection may continue after
# this, but quote push stops at the official close because prices are final.
MARKET_CLOSE_HOUR = int(os.environ.get("MARKET_CLOSE_HOUR", "13"))
MARKET_CLOSE_MIN = int(os.environ.get("MARKET_CLOSE_MIN", "30"))

# Daily post-market HF sync time. The app stays online 24/7, so this replaces
# the old "restart in the morning to pull fresh history" workflow.
HF_DAILY_SYNC_HOUR = int(os.environ.get("HF_DAILY_SYNC_HOUR", "19"))
HF_DAILY_SYNC_MIN = int(os.environ.get("HF_DAILY_SYNC_MIN", "0"))

# Warm the most common historical reads after startup/HF sync so the first user
# page load does not pay the parquet cold-read cost on small cloud instances.
CACHE_PREWARM_MONTHS = max(0, min(3, int(os.environ.get("CACHE_PREWARM_MONTHS", "3"))))
CACHE_PREWARM_CHART_MONTHS = max(0, min(CACHE_PREWARM_MONTHS, int(os.environ.get("CACHE_PREWARM_CHART_MONTHS", "1"))))
CACHE_PREWARM_CHART_DATES = int(os.environ.get("CACHE_PREWARM_CHART_DATES", "0"))
CACHE_PREWARM_CHART_ROWS = int(os.environ.get("CACHE_PREWARM_CHART_ROWS", "32"))
CACHE_PREWARM_STOCKS = [
    s.strip()
    for s in os.environ.get("CACHE_PREWARM_STOCKS", "0050,2330").split(",")
    if s.strip()
]
CACHE_PREWARM_DAY_ATR = float(os.environ.get("CACHE_PREWARM_DAY_ATR", "0.05"))
CACHE_PREWARM_VOL5_PR = float(os.environ.get("CACHE_PREWARM_VOL5_PR", "0.5"))
CACHE_PREWARM_CHART_PAUSE_SEC = max(0.0, float(os.environ.get("CACHE_PREWARM_CHART_PAUSE_SEC", "0.2")))
