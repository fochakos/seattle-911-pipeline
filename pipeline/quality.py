"""Data-quality checks, run after every load.

severity "error": the run fails and the dashboard is NOT republished (the last good one stays live).
severity "warn":  reported on the dashboard, but doesn't block publishing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import duckdb


@dataclass
class Check:
    name: str
    severity: str          # "error" or "warn"
    passed: bool
    value: str
    rule: str


def _one(con, sql, params=None):
    return con.execute(sql, params).fetchall()[0][0] if params else con.execute(sql).fetchall()[0][0]


def run_checks(con: duckdb.DuckDBPyConnection, now_local: datetime) -> list[Check]:
    checks: list[Check] = []
    add = lambda *a: checks.append(Check(*a))  # noqa: E731
    # "Today" in Seattle, not the server's clock (GitHub runners use UTC).
    con.execute(f"SET VARIABLE today = DATE '{now_local.date().isoformat()}'")

    total = _one(con, "SELECT count(*) FROM calls_raw")
    add("Warehouse has data", "error", total > 0, f"{total:,} rows", "> 0 rows")

    latest = _one(con, "SELECT max(call_ts) FROM calls_raw")
    hours = (now_local.replace(tzinfo=None) - latest).total_seconds() / 3600 if latest else float("inf")
    add("Data is fresh", "error", hours <= 24, f"latest call {hours:.1f} h ago", "≤ 24 h old")

    future = _one(con, "SELECT count(*) FROM calls_raw WHERE call_ts > ?::TIMESTAMP + INTERVAL 1 HOUR",
                  [now_local.replace(tzinfo=None)])
    add("No calls in the future", "error", future == 0, f"{future} rows", "0 rows")

    dupes = _one(con, "SELECT count(*) - count(DISTINCT incident_number) FROM calls_raw")
    add("Incident numbers unique", "error", dupes == 0, f"{dupes} duplicates", "0 duplicates")

    nulls = _one(con, """SELECT count(*) FROM calls_raw
                         WHERE incident_number IS NULL OR call_ts IS NULL OR type IS NULL""")
    add("Required fields present", "error", nulls == 0, f"{nulls} rows missing ID, time or type", "0 rows")

    gaps = _one(con, """SELECT count(*) FROM mart_daily WHERE calls = 0""")
    add("No missing days", "error", gaps == 0, f"{gaps} days with no calls", "0 days")

    coords = _one(con, """SELECT avg(has_coords::INT) FROM stg_calls
                          WHERE call_date >= getvariable('today') - 30 AND NOT is_test""") or 0
    add("Coordinates present (last 30 days)", "warn", coords >= 0.99, f"{coords:.2%}", "≥ 99%")

    inside = _one(con, """SELECT avg(in_seattle_bbox::INT) FROM stg_calls
                          WHERE has_coords AND call_date >= getvariable('today') - 30 AND NOT is_test""") or 0
    add("Coordinates inside Seattle (last 30 days)", "warn", inside >= 0.99, f"{inside:.2%}", "≥ 99%")

    other = _one(con, """SELECT avg((category = 'Other')::INT) FROM stg_calls
                         WHERE call_date >= getvariable('today') - 30 AND NOT is_test""") or 0
    add("Call types categorised (last 30 days)", "warn", other <= 0.10,
        f"{other:.1%} in 'Other'", "≤ 10% uncategorised")

    # Yesterday's volume vs the previous 28 days: flags outages or double loads.
    yesterday, mean, sd, n = con.execute("""
        WITH d AS (SELECT * FROM mart_daily ORDER BY call_date DESC LIMIT 29)
        SELECT (SELECT calls FROM d ORDER BY call_date DESC LIMIT 1),
               avg(calls) FILTER (WHERE call_date < (SELECT max(call_date) FROM d)),
               stddev_samp(calls) FILTER (WHERE call_date < (SELECT max(call_date) FROM d)),
               count(*) - 1
        FROM d""").fetchall()[0]
    rule = "within 3 SD of the previous 28 days"
    if not n or n < 7 or mean is None:
        add("Yesterday's volume is normal", "warn", True, "not enough history yet", rule)
    else:
        # Daily counts behave like Poisson counts, so the spread is at least sqrt(mean).
        # This also stops a perfectly flat history (SD = 0) from dividing by zero.
        spread = max(sd or 0.0, mean ** 0.5)
        z = (yesterday - mean) / spread
        add("Yesterday's volume is normal", "warn", abs(z) <= 3, f"{yesterday} calls (z = {z:+.1f})", rule)
    return checks


def summarise(checks: list[Check]) -> tuple[int, int]:
    failed = sum(1 for c in checks if not c.passed and c.severity == "error")
    warned = sum(1 for c in checks if not c.passed and c.severity == "warn")
    return failed, warned


def as_records(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
