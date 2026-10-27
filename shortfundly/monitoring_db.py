"""Monitoring / discovery database layer (Phase 3 enhancement).

Additive migration: these tables are created with CREATE TABLE IF NOT EXISTS and
never modify the existing `contacts`, `email_log`, `email_templates`, `settings`
or `import_rejections` tables. Running this on an existing database is safe and
idempotent — no existing user data is touched.

Tables added
------------
* colleges            - discovered/tracked institutions (official website)
* source_pages        - pages fetched per college (contact/admissions/etc.)
* monitoring_jobs     - a scheduled or manual monitoring run
* check_results       - per-page result of a check (status, hash, http code)
* contact_records     - extracted contacts with status + source URL
* contact_changes     - change-history (old value -> new value, approval)
* fetch_errors        - failed requests / extraction errors + retry info
* notifications_log   - sent notifications (for deduplication)

All timestamps are UTC ISO-8601 strings (see database.utcnow).
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from .database import utcnow

MONITORING_SCHEMA = """
CREATE TABLE IF NOT EXISTS colleges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    institution_type TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    website_host TEXT NOT NULL DEFAULT '',          -- normalised host for dedupe
    college_key TEXT NOT NULL,                       -- normalised name|city
    discovery_source TEXT NOT NULL DEFAULT '',       -- url_list | existing_csv | manual
    monitoring_enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'discovered'
        CHECK (status IN ('discovered','pending_review','approved','rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_check_status TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_colleges_host ON colleges(website_host)
    WHERE website_host <> '';
CREATE INDEX IF NOT EXISTS idx_colleges_key ON colleges(college_key);

CREATE TABLE IF NOT EXISTS source_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    college_id INTEGER NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    page_type TEXT NOT NULL DEFAULT 'contact',       -- contact|admissions|department|home|other
    content_hash TEXT NOT NULL DEFAULT '',
    etag TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT '',
    last_fetched_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (college_id, url)
);
CREATE INDEX IF NOT EXISTS idx_source_pages_college ON source_pages(college_id);

CREATE TABLE IF NOT EXISTS monitoring_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'monitor'             -- monitor | discovery
        CHECK (kind IN ('monitor','discovery')),
    trigger TEXT NOT NULL DEFAULT 'manual'           -- manual | scheduled
        CHECK (trigger IN ('manual','scheduled')),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','completed','failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    colleges_checked INTEGER NOT NULL DEFAULT 0,
    new_colleges INTEGER NOT NULL DEFAULT 0,
    new_contacts INTEGER NOT NULL DEFAULT 0,
    changes_detected INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES monitoring_jobs(id) ON DELETE SET NULL,
    college_id INTEGER REFERENCES colleges(id) ON DELETE CASCADE,
    source_page_id INTEGER REFERENCES source_pages(id) ON DELETE SET NULL,
    url TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL                             -- see monitoring.Outcome
        CHECK (outcome IN ('no_change','contact_changed','new_contact',
                           'unavailable','blocked','extraction_failed',
                           'contact_missing','ok')),
    http_status INTEGER,
    content_hash TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_check_results_college ON check_results(college_id);

CREATE TABLE IF NOT EXISTS contact_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    college_id INTEGER NOT NULL REFERENCES colleges(id) ON DELETE CASCADE,
    email TEXT NOT NULL DEFAULT '',                   -- stored lower-case
    phone TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    contact_person TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'discovered'
        CHECK (status IN ('discovered','pending_review','format_valid',
                          'source_verified','approved','rejected','potentially_outdated')),
    collected_at TEXT NOT NULL,
    last_checked_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (college_id, email)
);
CREATE INDEX IF NOT EXISTS idx_contact_records_email ON contact_records(email);
CREATE INDEX IF NOT EXISTS idx_contact_records_status ON contact_records(status);

CREATE TABLE IF NOT EXISTS contact_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    college_id INTEGER REFERENCES colleges(id) ON DELETE CASCADE,
    contact_record_id INTEGER REFERENCES contact_records(id) ON DELETE SET NULL,
    field TEXT NOT NULL,                              -- email|phone|website|department|...
    old_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    detected_at TEXT NOT NULL,
    approval_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (approval_status IN ('pending','approved','rejected','auto_applied')),
    reviewed_by TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_contact_changes_approval ON contact_changes(approval_status);

CREATE TABLE IF NOT EXISTS fetch_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES monitoring_jobs(id) ON DELETE SET NULL,
    college_id INTEGER REFERENCES colleges(id) ON DELETE SET NULL,
    url TEXT NOT NULL DEFAULT '',
    error_type TEXT NOT NULL DEFAULT '',
    http_status INTEGER,
    message TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,                         -- new_college|email_changed|...
    dedupe_key TEXT NOT NULL,
    recipient TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL,
    UNIQUE (dedupe_key)
);
"""


def init_monitoring(conn: sqlite3.Connection) -> None:
    """Create monitoring tables if they do not already exist (idempotent)."""
    conn.executescript(MONITORING_SCHEMA)
    conn.commit()


# ------------------------------------------------------------------ colleges
def upsert_college(
    conn: sqlite3.Connection,
    *,
    name: str,
    website: str,
    website_host: str,
    college_key: str,
    state: str = "",
    city: str = "",
    institution_type: str = "",
    discovery_source: str = "manual",
) -> tuple[int, bool]:
    """Insert a college or return the existing one (matched by website_host).

    Returns (college_id, created) where created is True for a new row.
    Never raises on duplicate host — returns the existing row instead.
    """
    now = utcnow()
    if website_host:
        row = conn.execute(
            "SELECT id FROM colleges WHERE website_host = ?", (website_host,)
        ).fetchone()
        if row:
            return int(row["id"]), False
    try:
        cur = conn.execute(
            "INSERT INTO colleges (name, institution_type, state, city, website, "
            "website_host, college_key, discovery_source, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, institution_type, state, city, website, website_host,
             college_key, discovery_source, now, now),
        )
        conn.commit()
        return int(cur.lastrowid), True
    except sqlite3.IntegrityError:
        conn.rollback()
        row = conn.execute(
            "SELECT id FROM colleges WHERE website_host = ?", (website_host,)
        ).fetchone()
        if row:
            return int(row["id"]), False
        raise


def start_job(conn: sqlite3.Connection, kind: str = "monitor", trigger: str = "manual") -> int:
    cur = conn.execute(
        "INSERT INTO monitoring_jobs (kind, trigger, status, started_at) VALUES (?,?,?,?)",
        (kind, trigger, "running", utcnow()),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_job(conn: sqlite3.Connection, job_id: int, *, status: str = "completed",
               colleges_checked: int = 0, new_colleges: int = 0, new_contacts: int = 0,
               changes_detected: int = 0, failures: int = 0, detail: str = "") -> None:
    conn.execute(
        "UPDATE monitoring_jobs SET status=?, finished_at=?, colleges_checked=?, "
        "new_colleges=?, new_contacts=?, changes_detected=?, failures=?, detail=? WHERE id=?",
        (status, utcnow(), colleges_checked, new_colleges, new_contacts,
         changes_detected, failures, detail, job_id),
    )
    conn.commit()


def record_error(conn: sqlite3.Connection, *, url: str, error_type: str, message: str,
                 job_id: Optional[int] = None, college_id: Optional[int] = None,
                 http_status: Optional[int] = None, attempt: int = 1) -> None:
    conn.execute(
        "INSERT INTO fetch_errors (job_id, college_id, url, error_type, http_status, "
        "message, attempt, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (job_id, college_id, url, error_type, http_status, message[:1000], attempt, utcnow()),
    )
    conn.commit()


def monitoring_stats(conn: sqlite3.Connection) -> dict:
    """Dashboard numbers computed from the real monitoring tables."""
    def scalar(sql: str, params: tuple = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    last = conn.execute(
        "SELECT finished_at FROM monitoring_jobs WHERE kind='monitor' AND status='completed' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    return {
        "colleges_tracked": scalar("SELECT COUNT(*) FROM colleges WHERE monitoring_enabled=1"),
        "colleges_total": scalar("SELECT COUNT(*) FROM colleges"),
        "contacts_total": scalar("SELECT COUNT(*) FROM contact_records"),
        "contacts_approved": scalar("SELECT COUNT(*) FROM contact_records WHERE status='approved'"),
        "pending_approvals": scalar(
            "SELECT COUNT(*) FROM contact_changes WHERE approval_status='pending'"),
        "changes_total": scalar("SELECT COUNT(*) FROM contact_changes"),
        "failed_checks": scalar("SELECT COUNT(*) FROM fetch_errors"),
        "last_run_at": last["finished_at"] if last else "",
    }
