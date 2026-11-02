"""Build the static dashboard (site/index.html) and JSON outputs from the mart tables.

Charts are Vega-Lite specs made with Altair and rendered in the browser by vega-embed,
so the page is interactive (tooltips) but needs no server.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd

from . import config
from .quality import Check, as_records

VEGA = "https://cdn.jsdelivr.net/npm/vega@6.4.0"
VEGA_LITE = "https://cdn.jsdelivr.net/npm/vega-lite@6.4.1"
VEGA_EMBED = "https://cdn.jsdelivr.net/npm/vega-embed@7.3.0"
REPO_URL = "https://github.com/fochakos/seattle-911-pipeline"
SOURCE_URL = "https://data.seattle.gov/Public-Safety/Seattle-Real-Time-Fire-911-Calls/kzjm-xkqj"

INK, INK_2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
BLUE, GREY_LINE = "#2a78d6", "#c3c2b7"
CATEGORIES = ["Medical", "Alarm", "Fire", "Other", "Traffic", "Rescue", "Hazard"]
CATEGORY_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

THEME = {
    "background": "transparent",
    "font": "Segoe UI, system-ui, -apple-system, sans-serif",
    "view": {"stroke": None},
    "axis": {"labelColor": INK_2, "titleColor": INK_2, "gridColor": GRID, "domainColor": GREY_LINE,
             "tickColor": GREY_LINE, "labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": "normal"},
    "legend": {"labelColor": INK_2, "titleColor": INK_2, "labelFontSize": 11, "titleFontSize": 11},
}


def q(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


# --------------------------------------------------------------------------- charts
def chart_daily(con) -> dict:
    df = q(con, """SELECT call_date, calls, round(calls_7d_avg, 1) AS avg7 FROM mart_daily
                   WHERE call_date > (SELECT max(call_date) FROM mart_daily) - 365""")
    base = alt.Chart(df).encode(x=alt.X("call_date:T", title=None, axis=alt.Axis(format="%b %Y")))
    daily = base.mark_line(color=GREY_LINE, strokeWidth=1).encode(
        y=alt.Y("calls:Q", title="Calls per day", scale=alt.Scale(zero=False)))
    avg = base.mark_line(color=BLUE, strokeWidth=2).encode(y="avg7:Q")
    hover = alt.selection_point(fields=["call_date"], nearest=True, on="pointerover", empty=False)
    points = base.mark_point(size=60, filled=True, color=BLUE).encode(
        y="avg7:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("call_date:T", title="Date", format="%a %d %b %Y"),
                 alt.Tooltip("calls:Q", title="Calls"), alt.Tooltip("avg7:Q", title="7-day average")],
    ).add_params(hover)
    rule = base.mark_rule(color=MUTED).encode(opacity=alt.condition(hover, alt.value(0.6), alt.value(0)))
    return (daily + avg + rule + points).properties(width="container", height=240).to_dict()


def chart_monthly(con) -> dict:
    df = q(con, "SELECT month, calls FROM mart_monthly WHERE complete_month")
    return alt.Chart(df).mark_bar(color=BLUE, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("month:T", title=None, axis=alt.Axis(format="%Y", tickCount="year")),
        y=alt.Y("calls:Q", title="Calls per month"),
        tooltip=[alt.Tooltip("month:T", title="Month", format="%B %Y"),
                 alt.Tooltip("calls:Q", title="Calls", format=",")],
    ).properties(width="container", height=220).to_dict()


def chart_categories(con) -> dict:
    df = q(con, "SELECT * FROM mart_category_30d")
    df["change"] = (df["calls_30d"] / df["calls_30d_last_year"] - 1).round(3)
    base = alt.Chart(df).encode(y=alt.Y("category:N", sort="-x", title=None))
    bars = base.mark_bar(color=BLUE, cornerRadiusTopRight=3, cornerRadiusBottomRight=3, height=16).encode(
        x=alt.X("calls_30d:Q", title="Calls, last 30 days"),
        tooltip=[alt.Tooltip("category:N", title="Category"),
                 alt.Tooltip("calls_30d:Q", title="Last 30 days", format=","),
                 alt.Tooltip("calls_30d_last_year:Q", title="Same 30 days last year", format=","),
                 alt.Tooltip("change:Q", title="Change", format="+.0%")])
    ticks = base.mark_tick(color=INK, thickness=2, size=22).encode(x="calls_30d_last_year:Q")
    return (bars + ticks).properties(width="container", height=230).to_dict()


def chart_heatmap(con) -> dict:
    df = q(con, "SELECT call_dow, call_hour, round(avg_calls, 1) AS avg_calls FROM mart_hour_dow")
    df["day"] = df["call_dow"].map(lambda d: DAYS[d - 1])
    return alt.Chart(df).mark_rect(cornerRadius=2, stroke="white", strokeWidth=1.5).encode(
        x=alt.X("call_hour:O", title="Hour of day"),
        y=alt.Y("day:N", sort=DAYS, title=None),
        color=alt.Color("avg_calls:Q", title="Calls per hour", scale=alt.Scale(scheme="blues")),
        tooltip=[alt.Tooltip("day:N", title="Day"), alt.Tooltip("call_hour:O", title="Hour"),
                 alt.Tooltip("avg_calls:Q", title="Average calls")],
    ).properties(width="container", height=210).to_dict()


def chart_map(con) -> dict:
    df = q(con, """SELECT call_ts, call_type, category, latitude, longitude FROM mart_recent_points""")
    df["call_ts"] = df["call_ts"].dt.strftime("%a %d %b %H:%M")
    return alt.Chart(df).mark_circle(size=18, opacity=0.75, stroke="white", strokeWidth=0.5).encode(
        longitude="longitude:Q", latitude="latitude:Q",
        color=alt.Color("category:N", title="Category",
                        scale=alt.Scale(domain=CATEGORIES, range=CATEGORY_COLORS),
                        legend=alt.Legend(orient="right")),
        tooltip=[alt.Tooltip("call_type:N", title="Type"), alt.Tooltip("category:N", title="Category"),
                 alt.Tooltip("call_ts:N", title="Time")],
    ).project(type="mercator").properties(width="container", height=460).to_dict()


# --------------------------------------------------------------------------- page
def pct_change(new, old) -> str:
    if not old:
        return ""
    ch = new / old - 1
    return f"{'+' if ch >= 0 else '−'}{abs(ch):.1%}"


def kpi_tiles(k: pd.Series, checks: list[Check]) -> str:
    passed = sum(c.passed for c in checks)
    errors = sum(not c.passed and c.severity == "error" for c in checks)
    warns = sum(not c.passed and c.severity == "warn" for c in checks)
    status = "All checks passed" if passed == len(checks) else f"{errors} failed, {warns} warnings"
    tiles = [
        ("Calls yesterday", f"{int(k.calls_yesterday):,}", k.last_complete_day.strftime("%A %d %B")),
        ("7-day average", f"{k.avg_last_7d:,.0f}",
         f"{pct_change(k.avg_last_7d, k.avg_same_7d_last_year)} vs. same week last year"),
        ("Calls this year", f"{int(k.ytd_calls):,}",
         f"{pct_change(k.ytd_calls, k.ytd_calls_last_year)} vs. same date last year"),
        ("Data checks", f"{passed} / {len(checks)}", status),
    ]
    return "\n".join(
        f'<div class="tile"><div class="label">{html.escape(a)}</div>'
        f'<div class="value">{html.escape(b)}</div><div class="sub">{html.escape(c)}</div></div>'
        for a, b, c in tiles)


def checks_table(checks: list[Check]) -> str:
    rows = []
    for c in checks:
        icon, word = ("✓", "Pass") if c.passed else (("✗", "Fail") if c.severity == "error" else ("!", "Warn"))
        cls = "pass" if c.passed else c.severity
        rows.append(f'<tr><td><span class="badge {cls}">{icon} {word}</span></td><td>{html.escape(c.name)}</td>'
                    f'<td>{html.escape(c.value)}</td><td class="muted">{html.escape(c.rule)}</td>'
                    f'<td class="muted">{c.severity}</td></tr>')
    return "\n".join(rows)


def runs_table(runs: pd.DataFrame) -> str:
    rows = []
    for r in runs.itertuples():
        secs = (r.finished_at - r.started_at).total_seconds()
        ok = r.status == "success"
        rows.append(
            f'<tr><td>{r.run_id}</td><td>{r.started_at:%d %b %Y %H:%M}</td>'
            f'<td><span class="badge {"pass" if ok else "error"}">{"✓" if ok else "✗"} {html.escape(r.status)}</span></td>'
            f'<td class="num">{int(r.rows_fetched):,}</td><td class="num">{int(r.rows_new):,}</td>'
            f'<td class="num">{int(r.rows_changed):,}</td><td class="num">{secs:,.0f} s</td></tr>')
    return "\n".join(rows)


def types_table(con) -> str:
    df = q(con, "SELECT call_type, category, calls_30d FROM mart_type_30d LIMIT 10")
    return "\n".join(f"<tr><td>{html.escape(r.call_type)}</td><td>{r.category}</td>"
                     f'<td class="num">{r.calls_30d:,}</td></tr>' for r in df.itertuples())


def build(con: duckdb.DuckDBPyConnection, checks: list[Check], now_local: datetime) -> Path:
    site = config.SITE_DIR
    (site / "data").mkdir(parents=True, exist_ok=True)
    k = q(con, "SELECT * FROM mart_kpis").iloc[0]
    latest = con.execute("SELECT max(call_ts) FROM calls_raw").fetchall()[0][0]
    runs = q(con, "SELECT * FROM pipeline_runs ORDER BY run_id DESC LIMIT 10")

    specs = {"daily": chart_daily(con), "monthly": chart_monthly(con), "categories": chart_categories(con),
             "heatmap": chart_heatmap(con), "map": chart_map(con)}
    for spec in specs.values():
        spec["config"] = THEME

    page = (Path(__file__).with_name("template.html").read_text(encoding="utf-8")
            .replace("{{VEGA}}", VEGA).replace("{{VEGA_LITE}}", VEGA_LITE).replace("{{VEGA_EMBED}}", VEGA_EMBED)
            .replace("{{UPDATED}}", now_local.strftime("%d %b %Y, %H:%M %Z"))
            .replace("{{LATEST}}", latest.strftime("%d %b %Y, %H:%M"))
            .replace("{{TOTAL}}", f"{int(k.total_calls):,}")
            .replace("{{FIRST_DAY}}", pd.Timestamp(k.first_day).strftime("%d %b %Y"))
            .replace("{{KPIS}}", kpi_tiles(k, checks))
            .replace("{{CHECKS}}", checks_table(checks))
            .replace("{{RUNS}}", runs_table(runs))
            .replace("{{TYPES}}", types_table(con))
            .replace("{{REPO}}", REPO_URL).replace("{{SOURCE}}", SOURCE_URL)
            .replace("{{SPECS}}", json.dumps(specs, default=str).replace("</", "<\\/")))
    (site / "index.html").write_text(page, encoding="utf-8")

    # Machine-readable outputs, served next to the page.
    summary = {"updated_at": now_local.isoformat(timespec="seconds"), "latest_call": latest.isoformat(),
               "kpis": json.loads(k.to_json(date_format="iso")), "checks": as_records(checks)}
    (site / "data" / "latest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    q(con, "SELECT * FROM mart_daily").to_csv(site / "data" / "daily_calls.csv", index=False)
    runs.to_json(site / "data" / "runs.json", orient="records", date_format="iso", indent=2)
    return site / "index.html"
