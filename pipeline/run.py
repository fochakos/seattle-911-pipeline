"""Run the whole pipeline once: extract -> load -> transform -> quality checks -> dashboard.

Usage (from the project root):
    python -m pipeline.run                 # normal daily run
    python -m pipeline.run --skip-extract  # rebuild marts and dashboard from the warehouse only

Exit code 0 = published, 1 = an error-level check failed or a step crashed (nothing published).
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from . import config, dashboard, extract, load, quality, transform

log = logging.getLogger("pipeline")


def record_run(con, run: dict) -> None:
    cols = ", ".join(run)
    con.execute(f"INSERT INTO pipeline_runs ({cols}) VALUES ({', '.join('?' * len(run))})", list(run.values()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-extract", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    started = datetime.now(config.LOCAL_TZ)
    con = load.connect()
    run = {"run_id": con.execute("SELECT coalesce(max(run_id), 0) + 1 FROM pipeline_runs").fetchall()[0][0],
           "started_at": started.replace(tzinfo=None), "status": "error", "message": "",
           "rows_fetched": 0, "rows_new": 0, "rows_changed": 0}
    checks: list[quality.Check] = []
    try:
        if not args.skip_extract:
            since = load.watermark(con)
            run["extracted_since"] = since
            log.info("extracting calls since %s", since)
            df = extract.fetch_since(since)
            run["rows_new"], run["rows_changed"] = load.upsert(con, df)
            run["rows_fetched"] = len(df)
            log.info("fetched %s rows: %s new, %s changed", len(df), run["rows_new"], run["rows_changed"])

        transform.run(con, started.date())
        checks = quality.run_checks(con, started)
        failed, warned = quality.summarise(checks)
        for c in checks:
            log.log(logging.INFO if c.passed else logging.WARNING, "%-4s %-42s %s",
                    "ok" if c.passed else c.severity.upper(), c.name, c.value)
        run.update(checks_failed=failed, checks_warned=warned,
                   status="success" if failed == 0 else "failed_checks",
                   message="" if failed == 0 else "error-level data-quality check failed")
    except Exception as exc:                       # record the failure, then fail the job
        log.exception("pipeline step failed")
        run["message"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        run["total_rows"] = con.execute("SELECT count(*) FROM calls_raw").fetchall()[0][0]
        run["latest_call_ts"] = con.execute("SELECT max(call_ts) FROM calls_raw").fetchall()[0][0]
        run["finished_at"] = datetime.now(config.LOCAL_TZ).replace(tzinfo=None)
        record_run(con, run)

    if run["status"] != "success":
        log.error("run %s finished with status %s: nothing published", run["run_id"], run["status"])
        con.close()
        return 1

    path = dashboard.build(con, checks, started)
    log.info("dashboard written to %s", path)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
