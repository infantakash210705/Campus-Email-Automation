"""Auto-discovery: load college URLs from a seed CSV or CollegeDunia, sync into DB.

Two data sources are supported:
  1. Seed CSV (`data/seed_colleges.csv`) — manually maintained list.
  2. CollegeDunia Karnataka listing — scraped live from the web.

Every time the worker runs, new rows are imported automatically — no manual
dashboard steps required.

Expected CSV columns (all optional except `website`):
    name, website, state, city, institution_type

Any row that already exists in the `colleges` table (matched by website_host)
is silently skipped, so re-running is always safe.
"""
from __future__ import annotations

import csv
import logging
import sqlite3
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _read_seed_csv(path: str) -> list[dict]:
    """Parse the seed CSV and return a list of dicts, skipping blank/header rows."""
    p = Path(path)
    if not p.exists():
        log.warning("Seed CSV not found at %s — skipping discovery", path)
        return []

    rows: list[dict] = []
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, start=2):  # row 1 is the header
            website = (row.get("website") or "").strip()
            if not website:
                log.debug("Seed CSV row %d: no website, skipping", i)
                continue
            rows.append({
                "name": (row.get("name") or row.get("college_name") or "").strip(),
                "website": website,
                "state": (row.get("state") or "").strip(),
                "city": (row.get("city") or "").strip(),
                "institution_type": (row.get("institution_type") or "").strip(),
            })
    log.info("Seed CSV loaded: %d rows from %s", len(rows), path)
    return rows


def sync_seed_colleges(
    conn: sqlite3.Connection,
    seed_csv_path: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Import colleges from the seed CSV into the `colleges` table.

    Uses the discovery pipeline (discover_from_urls + seed_colleges_from_candidates)
    so all deduplication and normalisation logic is reused.

    Returns {created: int, existing: int, errors: int}
    """
    from discovery.providers import discover_from_urls
    from monitoring.pipeline import seed_colleges_from_candidates
    from shortfundly import monitoring_db as mdb

    mdb.init_monitoring(conn)

    rows = _read_seed_csv(seed_csv_path)
    if not rows:
        return {"created": 0, "existing": 0, "errors": 0}

    entries = [
        {
            "url": r["website"],
            "name": r["name"],
            "state": r["state"],
            "city": r["city"],
            "institution_type": r["institution_type"],
        }
        for r in rows
    ]

    try:
        candidates = discover_from_urls(entries)
    except Exception as exc:
        log.error("discover_from_urls failed: %s", exc)
        return {"created": 0, "existing": 0, "errors": 1}

    if dry_run:
        log.info("dry_run=True — would import %d candidates", len(candidates))
        return {"created": len(candidates), "existing": 0, "errors": 0, "dry_run": True}

    try:
        result = seed_colleges_from_candidates(conn, candidates)
    except Exception as exc:
        log.error("seed_colleges_from_candidates failed: %s", exc)
        return {"created": 0, "existing": 0, "errors": 1}

    summary = {
        "created": result.get("created", 0),
        "existing": result.get("existing", 0),
        "errors": 0,
    }
    log.info("sync_seed_colleges: %s", summary)
    return summary


def create_seed_csv_template(path: str) -> None:
    """Write a blank seed CSV template if one does not already exist."""
    p = Path(path)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["name", "website", "state", "city", "institution_type"]
        )
        writer.writeheader()
    log.info("Created seed CSV template at %s", path)


def sync_from_collegedunia(
    conn: "sqlite3.Connection",
    *,
    limit: int = 100,
    delay: float = 1.0,
    dry_run: bool = False,
) -> dict:
    """Scrape Karnataka colleges from CollegeDunia and import them.

    This calls the CollegeDunia scraper (discovery.collegedunia_scraper),
    then feeds results through the same discover_from_urls +
    seed_colleges_from_candidates pipeline used by sync_seed_colleges().

    Args:
        conn: Open SQLite connection.
        limit: Max colleges to scrape per run (0 = all 1728, default 100).
        delay: Seconds between HTTP requests to CollegeDunia.
        dry_run: If True, log what would be imported but make no DB changes.

    Returns:
        {"created": int, "existing": int, "errors": int, "scraped": int}
    """
    try:
        from discovery.collegedunia_scraper import scrape_karnataka_colleges
        from discovery.providers import discover_from_urls
        from monitoring.pipeline import seed_colleges_from_candidates
        from shortfundly import monitoring_db as mdb
    except ImportError as exc:
        log.error("CollegeDunia sync import error: %s", exc)
        return {"created": 0, "existing": 0, "errors": 1, "scraped": 0}

    mdb.init_monitoring(conn)

    real_limit = limit if limit > 0 else None
    log.info("Scraping CollegeDunia (limit=%s, delay=%.1fs)", real_limit or "all", delay)

    try:
        colleges = scrape_karnataka_colleges(limit=real_limit, delay=delay, verbose=False)
    except Exception as exc:
        log.error("CollegeDunia scrape failed: %s", exc)
        return {"created": 0, "existing": 0, "errors": 1, "scraped": 0}

    if not colleges:
        log.warning("CollegeDunia scraper returned no colleges")
        return {"created": 0, "existing": 0, "errors": 0, "scraped": 0}

    entries = [
        {
            "url": c["website"],
            "name": c["name"],
            "state": c.get("state", "Karnataka"),
            "city": c.get("city", ""),
            "institution_type": c.get("institution_type", ""),
        }
        for c in colleges
        if c.get("website")
    ]

    try:
        candidates = discover_from_urls(entries)
    except Exception as exc:
        log.error("discover_from_urls failed: %s", exc)
        return {"created": 0, "existing": 0, "errors": 1, "scraped": len(colleges)}

    if dry_run:
        log.info("dry_run=True — would import %d candidates from CollegeDunia", len(candidates))
        return {"created": len(candidates), "existing": 0, "errors": 0, "scraped": len(colleges), "dry_run": True}

    try:
        result = seed_colleges_from_candidates(conn, candidates)
    except Exception as exc:
        log.error("seed_colleges_from_candidates failed: %s", exc)
        return {"created": 0, "existing": 0, "errors": 1, "scraped": len(colleges)}

    summary = {
        "created": result.get("created", 0),
        "existing": result.get("existing", 0),
        "errors": 0,
        "scraped": len(colleges),
    }
    log.info("sync_from_collegedunia: %s", summary)
    return summary
