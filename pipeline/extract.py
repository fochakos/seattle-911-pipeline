"""Extract: page through the Socrata API for calls at or after a given timestamp."""
from __future__ import annotations

import logging
import time

import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)
COLUMNS = ["incident_number", "datetime", "type", "address", "latitude", "longitude"]


def _get(session: requests.Session, params: dict, retries: int = 4) -> list[dict]:
    """GET one page, retrying on network errors, rate limits (429) and server errors (5xx)."""
    headers = {"X-App-Token": config.APP_TOKEN} if config.APP_TOKEN else {}
    for attempt in range(retries):
        try:
            resp = session.get(config.API_URL, params=params, headers=headers, timeout=60)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status < 500 and status != 429:
                raise                                   # 4xx other than 429: a bug, don't retry
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            log.warning("request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_since(since: str, session: requests.Session | None = None,
                page_size: int = config.PAGE_SIZE) -> pd.DataFrame:
    """All calls with datetime >= since, ordered and paged so no row is skipped or repeated."""
    session = session or requests.Session()
    pages, offset = [], 0
    while True:
        params = {
            "$select": ",".join(COLUMNS),
            "$where": f"datetime >= '{since}'",
            "$order": "datetime, incident_number",
            "$limit": page_size,
            "$offset": offset,
        }
        rows = _get(session, params)
        pages.extend(rows)
        log.info("fetched %s rows (offset %s)", len(rows), offset)
        if len(rows) < page_size:
            break
        offset += page_size
    df = pd.DataFrame(pages, columns=COLUMNS)
    return df.rename(columns={"datetime": "call_ts"})
