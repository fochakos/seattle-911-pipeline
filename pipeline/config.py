"""Settings in one place. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]

# Source: Seattle Real Time Fire 911 Calls (Socrata open-data API, no key required)
API_URL = os.getenv("SOURCE_URL", "https://data.seattle.gov/resource/kzjm-xkqj.json")
APP_TOKEN = os.getenv("SOCRATA_APP_TOKEN")          # optional: raises the API rate limit

START_DATE = os.getenv("START_DATE", "2021-01-01")   # first load backfills from here
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "3"))  # re-read recent days to catch late edits
PAGE_SIZE = 50_000

DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "warehouse.duckdb"))
SQL_DIR = ROOT / "sql"
SITE_DIR = Path(os.getenv("SITE_DIR", ROOT / "site"))

# Timestamps in the feed are Seattle local time without an offset.
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
