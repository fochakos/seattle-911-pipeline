"""Load: idempotent upsert of extracted rows into DuckDB, keyed on incident_number."""
from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pandas as pd

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls_raw (
    incident_number VARCHAR PRIMARY KEY,
    call_ts         TIMESTAMP NOT NULL,     -- Seattle local time, as published
    type            VARCHAR,
    address         VARCHAR,
    latitude        DOUBLE,
    longitude       DOUBLE,
    loaded_at       TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id          INTEGER,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    status          VARCHAR,
    extracted_since TIMESTAMP,
    rows_fetched    INTEGER,
    rows_new        INTEGER,
    rows_changed    INTEGER,
    total_rows      BIGINT,
    latest_call_ts  TIMESTAMP,
    checks_failed   INTEGER,
    checks_warned   INTEGER,
    message         VARCHAR
);
"""


def connect(path=config.DB_PATH) -> duckdb.DuckDBPyConnection:
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def watermark(con: duckdb.DuckDBPyConnection, start_date: str = config.START_DATE,
              lookback_days: int = config.LOOKBACK_DAYS) -> str:
    """Where the next extract starts: the latest loaded call minus a lookback window,
    or START_DATE on an empty warehouse (full backfill)."""
    latest = con.execute("SELECT max(call_ts) FROM calls_raw").fetchall()[0][0]
    if latest is None:
        return f"{start_date}T00:00:00"
    since = max(latest - timedelta(days=lookback_days), datetime.fromisoformat(start_date))
    return since.strftime("%Y-%m-%dT%H:%M:%S")


def upsert(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> tuple[int, int]:
    """Insert new calls and overwrite changed ones. Returns (new rows, changed rows).
    Running the same batch twice changes nothing."""
    if df.empty:
        return 0, 0
    batch = df.drop_duplicates("incident_number", keep="last").copy()
    batch["call_ts"] = pd.to_datetime(batch["call_ts"], format="ISO8601")   # with or without fractions
    for col in ("latitude", "longitude"):
        batch[col] = pd.to_numeric(batch[col], errors="coerce")
    con.register("batch", batch)
    new, changed = con.execute("""
        SELECT
            count(*) FILTER (WHERE r.incident_number IS NULL),
            count(*) FILTER (WHERE r.incident_number IS NOT NULL AND (
                r.call_ts IS DISTINCT FROM b.call_ts OR r.type IS DISTINCT FROM b.type OR
                r.address IS DISTINCT FROM b.address OR
                r.latitude IS DISTINCT FROM b.latitude OR r.longitude IS DISTINCT FROM b.longitude))
        FROM batch b LEFT JOIN calls_raw r USING (incident_number)""").fetchall()[0]
    con.execute("""
        INSERT OR REPLACE INTO calls_raw
        SELECT incident_number, call_ts, type, address, latitude, longitude, now()::TIMESTAMP
        FROM batch""")
    con.unregister("batch")
    return int(new), int(changed)
