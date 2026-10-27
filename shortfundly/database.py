"""SQLite connection handling and schema."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    city TEXT NOT NULL,
    college_name TEXT NOT NULL,
    website TEXT NOT NULL DEFAULT '',
    contact_person TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL UNIQUE,            -- stored lower-case
    source_url TEXT NOT NULL DEFAULT '',
    college_key TEXT NOT NULL,             -- normalised "college|city" for duplicate detection
    verification_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (verification_status IN ('unverified', 'verified')),
    verified_by TEXT NOT NULL DEFAULT '',
    verified_at TEXT,
    verification_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_college_key ON contacts(college_key);
CREATE INDEX IF NOT EXISTS idx_contacts_state ON contacts(state);

CREATE TABLE IF NOT EXISTS import_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    reason_code TEXT NOT NULL CHECK (reason_code IN ('invalid', 'duplicate')),
    detail TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    campaign TEXT NOT NULL,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    recipient_email TEXT NOT NULL,         -- intended recipient (the college contact)
    delivered_to TEXT NOT NULL,            -- address actually handed to SMTP (differs in test mode)
    recipient_name TEXT NOT NULL DEFAULT '',
    college_name TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    is_test INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_attempt_at TEXT,
    accepted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_email_log_status ON email_log(status);
-- At most ONE live row per (campaign, recipient): the database itself blocks duplicate sends.
CREATE UNIQUE INDEX IF NOT EXISTS ux_email_log_live_dedupe
    ON email_log(dedupe_key) WHERE is_test = 0;
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
