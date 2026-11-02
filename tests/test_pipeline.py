"""Offline tests: a fake HTTP session stands in for the API and an in-memory DuckDB for the warehouse.

Run from the project root:  python -m unittest discover tests   (or: pytest)
"""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd
import requests

from pipeline import config, dashboard, extract, load, quality, transform

AS_OF = date(2026, 6, 15)
NOW = datetime(2026, 6, 15, 7, 0, tzinfo=config.LOCAL_TZ)
TYPES = ["Aid Response", "Medic Response", "Auto Fire Alarm", "Rubbish Fire", "MVI - Motor Vehicle Incident",
         "Rescue Elevator", "Natural Gas Odor", "Wires Down"]


def make_calls(days: int = 400, per_day: int = 24, end: date = AS_OF) -> pd.DataFrame:
    """Synthetic calls: one per hour for `days` days up to the morning of `end`."""
    rows, n = [], 0
    start = datetime.combine(end - timedelta(days=days), datetime.min.time())
    t = start
    while t < datetime.combine(end, datetime.min.time()) + timedelta(hours=6):
        rows.append({"incident_number": f"F{n:07d}", "call_ts": t.isoformat(),
                     "type": TYPES[n % len(TYPES)], "address": f"{n} Pine St",
                     "latitude": str(47.60 + (n % 10) / 100), "longitude": str(-122.33 + (n % 7) / 100)})
        n += 1
        t += timedelta(hours=24 / per_day)
    return pd.DataFrame(rows)


def warehouse(df: pd.DataFrame | None = None):
    con = load.connect(":memory:")
    load.upsert(con, make_calls() if df is None else df)
    transform.run(con, AS_OF)
    return con


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload or []

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        return self.responses.pop(0)


def row(i):
    return {"incident_number": f"F{i}", "datetime": "2026-06-01T10:00:00.000", "type": "Aid Response",
            "address": "x", "latitude": "47.6", "longitude": "-122.3"}


# --------------------------------------------------------------------------- extract
class TestExtract(unittest.TestCase):
    def test_pages_until_short_page(self):
        s = FakeSession([FakeResponse(200, [row(1), row(2)]), FakeResponse(200, [row(3), row(4)]),
                         FakeResponse(200, [row(5)])])
        df = extract.fetch_since("2026-06-01T00:00:00", session=s, page_size=2)
        self.assertEqual(len(df), 5)
        self.assertEqual([p["$offset"] for p in s.calls], [0, 2, 4])
        self.assertIn("call_ts", df.columns)
        self.assertEqual(s.calls[0]["$order"], "datetime, incident_number")   # stable paging

    @mock.patch("pipeline.extract.time.sleep")
    def test_retries_server_errors(self, _sleep):
        s = FakeSession([FakeResponse(503), FakeResponse(429), FakeResponse(200, [row(1)])])
        self.assertEqual(len(extract.fetch_since("2026-06-01", session=s)), 1)
        self.assertEqual(len(s.calls), 3)

    @mock.patch("pipeline.extract.time.sleep")
    def test_does_not_retry_client_errors(self, _sleep):
        s = FakeSession([FakeResponse(400), FakeResponse(200, [row(1)])])
        with self.assertRaises(requests.HTTPError):
            extract.fetch_since("2026-06-01", session=s)
        self.assertEqual(len(s.calls), 1)


# --------------------------------------------------------------------------- load
class TestLoad(unittest.TestCase):
    def test_upsert_is_idempotent(self):
        con = load.connect(":memory:")
        df = make_calls(days=5)
        new, changed = load.upsert(con, df)
        self.assertEqual((new, changed), (len(df), 0))
        self.assertEqual(load.upsert(con, df), (0, 0))
        self.assertEqual(con.execute("SELECT count(*) FROM calls_raw").fetchall()[0][0], len(df))

    def test_changed_rows_are_updated(self):
        con = load.connect(":memory:")
        df = make_calls(days=2)
        load.upsert(con, df)
        df.loc[0, "type"] = "Medic Response"
        self.assertEqual(load.upsert(con, df), (0, 1))
        got = con.execute("SELECT type FROM calls_raw WHERE incident_number = 'F0000000'").fetchall()[0][0]
        self.assertEqual(got, "Medic Response")

    def test_duplicates_within_a_batch_keep_last(self):
        con = load.connect(":memory:")
        df = pd.DataFrame([row(1), {**row(1), "type": "Medic Response"}]).rename(columns={"datetime": "call_ts"})
        self.assertEqual(load.upsert(con, df), (1, 0))

    def test_watermark(self):
        con = load.connect(":memory:")
        self.assertEqual(load.watermark(con, start_date="2021-01-01"), "2021-01-01T00:00:00")
        load.upsert(con, pd.DataFrame([row(1)]).rename(columns={"datetime": "call_ts"}))
        self.assertEqual(load.watermark(con, lookback_days=3), "2026-05-29T10:00:00")


# --------------------------------------------------------------------------- transform
class TestTransform(unittest.TestCase):
    def test_semicolon_in_comment_does_not_split(self):
        sql = "-- first; second\nSELECT 1;\nSELECT 2; -- trailing; comment"
        self.assertEqual(transform.statements(sql), ["SELECT 1", "SELECT 2"])

    def test_categories(self):
        expected = {"Aid Response": "Medical", "Medic Response- Overdose": "Medical",
                    "Auto Fire Alarm": "Alarm", "Automatic Medical Alarm": "Alarm",
                    "Rubbish Fire": "Fire", "Illegal Burn": "Fire", "MVI Medic": "Traffic",
                    "Rescue Elevator": "Rescue", "Natural Gas Odor": "Hazard", "Tranformer Fire": "Fire",
                    "Wires Down": "Hazard", "1RED 1 Unit": "Other", "TEST - MIS TEST": "Other"}
        con = load.connect(":memory:")
        df = pd.DataFrame([{**row(i), "type": t} for i, t in enumerate(expected)]).rename(
            columns={"datetime": "call_ts"})
        load.upsert(con, df)
        transform.run(con, AS_OF)
        got = dict(con.execute("SELECT call_type, category FROM stg_calls").fetchall())
        self.assertEqual(got, expected)

    def test_test_calls_are_flagged_and_excluded(self):
        df = make_calls(days=30)
        df.loc[df.index[-30], "type"] = "TEST - MIS TEST"
        con = warehouse(df)
        self.assertEqual(con.execute("SELECT count(*) FROM stg_calls WHERE is_test").fetchall()[0][0], 1)
        in_marts = con.execute("SELECT sum(calls) FROM mart_daily").fetchall()[0][0]
        before_today = con.execute("SELECT count(*) FROM stg_calls WHERE call_date < ?", [AS_OF]).fetchall()[0][0]
        self.assertEqual(in_marts, before_today - 1)

    def test_daily_mart_has_every_day(self):
        con = warehouse()
        n, first, last = con.execute("SELECT count(*), min(call_date), max(call_date) FROM mart_daily").fetchall()[0]
        self.assertEqual(n, (last - first).days + 1)
        self.assertEqual(last, AS_OF - timedelta(days=1))       # today is incomplete, so excluded


# --------------------------------------------------------------------------- quality
class TestQuality(unittest.TestCase):
    def failed(self, con, now=NOW):
        return {c.name for c in quality.run_checks(con, now) if not c.passed}

    def test_clean_data_passes(self):
        self.assertEqual(self.failed(warehouse()), set())

    def test_stale_data_fails_freshness(self):
        self.assertIn("Data is fresh", self.failed(warehouse(), NOW + timedelta(days=3)))

    def test_future_call_fails(self):
        df = make_calls()
        df.loc[0, "call_ts"] = "2030-01-01T00:00:00"
        self.assertIn("No calls in the future", self.failed(warehouse(df)))

    def test_missing_day_fails(self):
        df = make_calls()
        gap = pd.to_datetime(df["call_ts"]).dt.date == AS_OF - timedelta(days=100)
        self.assertIn("No missing days", self.failed(warehouse(df[~gap])))

    def test_volume_spike_warns(self):
        df = make_calls()
        extra = make_calls(days=1, per_day=500)
        extra["incident_number"] = "X" + extra["incident_number"]
        failed = self.failed(warehouse(pd.concat([df, extra])))
        self.assertIn("Yesterday's volume is normal", failed)

    def test_error_vs_warning_counts(self):
        checks = [quality.Check("a", "error", False, "", ""), quality.Check("b", "warn", False, "", ""),
                  quality.Check("c", "error", True, "", "")]
        self.assertEqual(quality.summarise(checks), (1, 1))


# --------------------------------------------------------------------------- dashboard
class TestDashboard(unittest.TestCase):
    def test_builds_page_and_json(self):
        con = warehouse()
        con.execute("""INSERT INTO pipeline_runs VALUES (1, '2026-06-15 07:00', '2026-06-15 07:01', 'success',
                       '2026-06-12', 100, 10, 1, 9600, '2026-06-15 05:00', 0, 0, '')""")
        checks = quality.run_checks(con, NOW)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "SITE_DIR", Path(tmp)):
            page = dashboard.build(con, checks, NOW).read_text(encoding="utf-8")
            latest = json.loads((Path(tmp) / "data" / "latest.json").read_text())
        self.assertNotIn("{{", page)
        for chart in ["daily", "monthly", "categories", "heatmap", "map"]:
            self.assertIn(f'id="{chart}"', page)
        self.assertEqual(len(latest["checks"]), len(checks))


if __name__ == "__main__":
    unittest.main()
