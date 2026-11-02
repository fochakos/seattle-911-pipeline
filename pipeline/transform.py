"""Transform: run the SQL files in order (staging view, then mart tables)."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import duckdb

from . import config


def statements(sql: str) -> list[str]:
    """Split a SQL file into statements. Comments are removed first, so a ';' inside a
    comment can't split a statement. (Our SQL has no '--' or ';' inside string literals.)"""
    no_comments = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def run(con: duckdb.DuckDBPyConnection, as_of: date, sql_dir: Path = config.SQL_DIR) -> None:
    for path in sorted(sql_dir.glob("*.sql")):
        for stmt in statements(path.read_text()):
            if "$as_of" in stmt:
                con.execute(stmt, {"as_of": as_of.isoformat()})
            else:
                con.execute(stmt)
