"""Monitoring pipeline: orchestrate fetch -> extract -> classify -> persist.

Ties together scraper.fetcher, scraper.extract, monitoring.changes and
shortfundly.monitoring_db. Designed so a single run is idempotent: re-running
does not create duplicate colleges/contacts, and uncertain email changes are
recorded as PENDING for human approval rather than overwriting stored values.

This module performs network I/O (via Fetcher) only when `run_monitoring` is
called with live colleges. All DB writes use the existing connection and small
transactions. It imports heavy/optional deps lazily so importing the module is
always safe.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

from shortfundly.database import utcnow
from shortfundly import monitoring_db as mdb


def _extract():
    from scraper.extract import extract_contacts, find_relevant_pages  # lazy
    return extract_contacts, find_relevant_pages


def seed_colleges_from_candidates(conn: sqlite3.Connection, candidates: list) -> dict:
    """Insert discovery candidates as colleges + their seed source_pages.

    Idempotent: existing colleges (matched by website_host) are reused.
    Returns {"created": int, "existing": int, "college_ids": [...]}.
    """
    created = existing = 0
    ids = []
    for c in candidates:
        cid, is_new = mdb.upsert_college(
            conn,
            name=getattr(c, "name", "") or "",
            website=getattr(c, "website", "") or "",
            website_host=getattr(c, "website_host", "") or "",
            college_key=getattr(c, "key", "") or "",
            state=getattr(c, "state", "") or "",
            city=getattr(c, "city", "") or "",
            institution_type=getattr(c, "institution_type", "") or "",
            discovery_source=getattr(c, "source", "manual") or "manual",
        )
        ids.append(cid)
        created += int(is_new)
        existing += int(not is_new)
        for url in (getattr(c, "seed_urls", None) or [getattr(c, "website", "")]):
            if not url:
                continue
            now = utcnow()
            try:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO source_pages "
                        "(college_id, url, page_type, created_at) VALUES (?,?,?,?)",
                        (cid, url, "home", now),
                    )
            except sqlite3.Error:
                pass
    return {"created": created, "existing": existing, "college_ids": ids}


def _record_change(conn, college_id, field, old_value, new_value, source_url):
    with conn:
        conn.execute(
            "INSERT INTO contact_changes (college_id, field, old_value, new_value, "
            "source_url, detected_at, approval_status) VALUES (?,?,?,?,?,?,'pending')",
            (college_id, field, old_value, new_value, source_url, utcnow()),
        )


def process_page_result(
    conn: sqlite3.Connection,
    *,
    job_id: Optional[int],
    college_id: int,
    url: str,
    fetch_outcome: str,
    http_status: Optional[int],
    html: str,
    new_hash: str,
    stored_hash: str,
) -> dict:
    """Extract + classify a single fetched page and persist results.

    Returns a summary dict with the outcome and counts. Never deletes stored
    contacts; new contacts are inserted as 'discovered', disappearances and
    changes are logged to contact_changes as 'pending' for review.
    """
    from monitoring.changes import classify_snapshot

    extract_contacts, _ = _extract()
    extracted_emails: list = []
    if fetch_outcome == "ok" and html:
        try:
            snap = extract_contacts(html, page_url=url, include_free=True)
            extracted_emails = snap.get("emails", [])
        except Exception as exc:  # malformed HTML etc.
            mdb.record_error(conn, url=url, error_type="extraction_failed",
                             message=str(exc), job_id=job_id, college_id=college_id)
            fetch_outcome = "extraction_failed"

    stored_rows = conn.execute(
        "SELECT email FROM contact_records WHERE college_id=?", (college_id,)
    ).fetchall()
    stored_emails = [r["email"] for r in stored_rows]

    verdict = classify_snapshot(
        fetch_outcome=fetch_outcome,
        stored_emails=stored_emails,
        extracted_emails=extracted_emails,
        stored_hash=stored_hash,
        new_hash=new_hash,
    )
    outcome = verdict["outcome"]
    now = utcnow()

    new_contacts = 0
    changes = 0
    if outcome in ("new_contact", "no_change", "contact_changed") and verdict.get("diff"):
        diff = verdict["diff"]
        for email in diff.get("new", []):
            try:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO contact_records (college_id, email, "
                        "source_url, status, collected_at, last_checked_at, updated_at) "
                        "VALUES (?,?,?,'discovered',?,?,?)",
                        (college_id, email, url, now, now, now),
                    )
                new_contacts += 1
            except sqlite3.Error:
                pass
            _record_change(conn, college_id, "email", "", email, url)
            changes += 1
        for email in diff.get("missing", []):
            # flag only — never auto-delete a stored contact on a single miss
            _record_change(conn, college_id, "email_missing", email, "", url)
            changes += 1

    # Record the per-page check result
    with conn:
        conn.execute(
            "INSERT INTO check_results (job_id, college_id, url, outcome, http_status, "
            "content_hash, checked_at, detail) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, college_id, url, outcome, http_status, new_hash, now,
             f"new={new_contacts} changes={changes}"),
        )
        conn.execute(
            "UPDATE source_pages SET content_hash=?, last_fetched_at=? "
            "WHERE college_id=? AND url=?",
            (new_hash, now, college_id, url),
        )
        conn.execute(
            "UPDATE colleges SET last_checked_at=?, last_check_status=? WHERE id=?",
            (now, outcome, college_id),
        )
    return {"outcome": outcome, "new_contacts": new_contacts, "changes": changes}


def run_monitoring(
    conn: sqlite3.Connection,
    cfg,
    *,
    trigger: str = "manual",
    college_ids: Optional[list] = None,
    fetcher=None,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Check tracked colleges and persist results. Returns a job summary dict.

    If `fetcher` is None, a real Fetcher is built from cfg (requires `requests`).
    Pass a fake fetcher in tests to avoid network access.
    """
    job_id = mdb.start_job(conn, kind="monitor", trigger=trigger)
    if fetcher is None:
        from scraper.fetcher import Fetcher
        fetcher = Fetcher.from_config(cfg)

    rows = conn.execute(
        "SELECT * FROM colleges WHERE monitoring_enabled=1"
        + ("" if college_ids is None else
           f" AND id IN ({','.join('?' * len(college_ids))})"),
        tuple(college_ids or ()),
    ).fetchall()

    checked = new_colleges = new_contacts = changes = failures = 0
    total = len(rows)
    for i, col in enumerate(rows, 1):
        cid = col["id"]
        pages = conn.execute(
            "SELECT * FROM source_pages WHERE college_id=? ORDER BY id "
            "LIMIT ?", (cid, max(1, getattr(cfg, "max_pages_per_college", 5))),
        ).fetchall()
        if not pages:
            pages = [{"url": col["website"], "content_hash": "", "etag": "", "last_modified": ""}]
        for pg in pages:
            res = fetcher.fetch(pg["url"], etag=pg["etag"] if "etag" in pg.keys() else "",
                                last_modified=pg["last_modified"] if "last_modified" in pg.keys() else "")
            if res.outcome in ("blocked", "unavailable") and not res.ok:
                failures += 1
                mdb.record_error(conn, url=pg["url"], error_type=res.error_type or res.outcome,
                                 message=res.message, job_id=job_id, college_id=cid,
                                 http_status=res.status)
            summary = process_page_result(
                conn, job_id=job_id, college_id=cid, url=res.url or pg["url"],
                fetch_outcome=res.outcome, http_status=res.status, html=res.text,
                new_hash=getattr(res, "content_hash", "") or "",
                stored_hash=pg["content_hash"] if "content_hash" in pg.keys() else "",
            )
            new_contacts += summary["new_contacts"]
            changes += summary["changes"]
        checked += 1
        if progress:
            progress(i, total, col["name"])

    mdb.finish_job(conn, job_id, status="completed", colleges_checked=checked,
                   new_colleges=new_colleges, new_contacts=new_contacts,
                   changes_detected=changes, failures=failures)
    return {"job_id": job_id, "checked": checked, "new_contacts": new_contacts,
            "changes": changes, "failures": failures}
