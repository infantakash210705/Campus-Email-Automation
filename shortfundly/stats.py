"""Dashboard statistics, computed from the actual database records."""
from __future__ import annotations

import sqlite3

from .validation import validate_contact


def _status_counts(conn: sqlite3.Connection, is_test: int) -> dict:
    counts = {"pending": 0, "sending": 0, "sent": 0, "failed": 0}
    for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM email_log WHERE is_test = ? GROUP BY status", (is_test,)
    ):
        counts[row["status"]] = row["n"]
    counts["total"] = sum(counts.values())
    return counts


def get_stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    verified = conn.execute("SELECT COUNT(*) FROM contacts WHERE verification_status='verified'").fetchone()[0]
    invalid_in_db = sum(1 for r in conn.execute("SELECT * FROM contacts") if validate_contact(dict(r)))
    dup_groups = conn.execute(
        "SELECT COUNT(*) FROM (SELECT college_key FROM contacts GROUP BY college_key HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    dup_contacts = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE college_key IN "
        "(SELECT college_key FROM contacts GROUP BY college_key HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    rejected = {"invalid": 0, "duplicate": 0}
    for row in conn.execute("SELECT reason_code, COUNT(*) AS n FROM import_rejections GROUP BY reason_code"):
        rejected[row["reason_code"]] = row["n"]
    by_state = {r["state"]: r["n"] for r in conn.execute(
        "SELECT state, COUNT(*) AS n FROM contacts GROUP BY state ORDER BY n DESC")}
    return {
        "contacts_total": total,
        "contacts_verified": verified,
        "contacts_unverified": total - verified,
        "contacts_invalid": invalid_in_db,
        "duplicate_college_groups": dup_groups,
        "duplicate_college_contacts": dup_contacts,
        "rejected_invalid_rows": rejected["invalid"],
        "rejected_duplicate_rows": rejected["duplicate"],
        "contacts_by_state": by_state,
        "emails_live": _status_counts(conn, 0),
        "emails_test": _status_counts(conn, 1),
    }
