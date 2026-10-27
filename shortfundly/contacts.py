"""Contact CRUD, search, verification and export."""
from __future__ import annotations

import io
import sqlite3
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from .database import utcnow
from .validation import (
    CONTACT_FIELDS, FREE_EMAIL_DOMAINS, clean, clean_record, college_key, domains_related,
    email_domain, host_of, validate_contact, validate_url,
)

EXPORT_COLUMNS = list(CONTACT_FIELDS) + [
    "verification_status", "verified_by", "verified_at", "verification_note",
]
SEARCH_COLUMNS = ("college_name", "city", "state", "email", "contact_person", "department", "website")


class ContactError(ValueError):
    """Raised for invalid or conflicting contact data. `.errors` lists every problem."""

    def __init__(self, errors: Any):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


def insert_contact(conn: sqlite3.Connection, rec: Mapping[str, Any]) -> int:
    """Insert an already cleaned+validated record. Does not commit. May raise IntegrityError."""
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO contacts (state, city, college_name, website, contact_person, department, "
        "email, source_url, college_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            rec["state"], rec["city"], rec["college_name"], rec["website"], rec["contact_person"],
            rec["department"], rec["email"], rec["source_url"],
            college_key(rec["college_name"], rec["city"]), now, now,
        ),
    )
    return int(cur.lastrowid)


def email_exists(conn: sqlite3.Connection, email: str, exclude_id: Optional[int] = None) -> bool:
    row = conn.execute(
        "SELECT id FROM contacts WHERE email = ? AND id IS NOT ?", (clean(email).lower(), exclude_id)
    ).fetchone()
    return row is not None


def add_contact(conn: sqlite3.Connection, record: Mapping[str, Any]) -> int:
    rec = clean_record(record)
    errors = validate_contact(rec)
    if errors:
        raise ContactError(errors)
    if email_exists(conn, rec["email"]):
        raise ContactError(f"A contact with email {rec['email']} already exists")
    try:
        cid = insert_contact(conn, rec)
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ContactError(f"Could not save contact: {exc}") from exc
    return cid


def get_contact(conn: sqlite3.Connection, contact_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    return dict(row) if row else None


def get_contacts(conn: sqlite3.Connection, ids: Iterable[int]) -> list[dict]:
    ids = [int(i) for i in ids]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT * FROM contacts WHERE id IN ({marks}) ORDER BY id", ids).fetchall()
    return [dict(r) for r in rows]


def update_contact(conn: sqlite3.Connection, contact_id: int, record: Mapping[str, Any]) -> None:
    existing = get_contact(conn, contact_id)
    if not existing:
        raise ContactError(f"Contact {contact_id} not found")
    rec = clean_record({**{f: existing[f] for f in CONTACT_FIELDS}, **record})
    errors = validate_contact(rec)
    if errors:
        raise ContactError(errors)
    if email_exists(conn, rec["email"], exclude_id=contact_id):
        raise ContactError(f"Another contact already uses {rec['email']}")
    # Changing the evidence invalidates a previous verification.
    evidence_changed = any(rec[f] != existing[f] for f in ("email", "website", "source_url"))
    reset = evidence_changed and existing["verification_status"] == "verified"
    conn.execute(
        "UPDATE contacts SET state=?, city=?, college_name=?, website=?, contact_person=?, "
        "department=?, email=?, source_url=?, college_key=?, updated_at=?"
        + (", verification_status='unverified', verified_by='', verified_at=NULL, "
           "verification_note='Reset: email/website/source changed after verification'" if reset else "")
        + " WHERE id=?",
        (
            rec["state"], rec["city"], rec["college_name"], rec["website"], rec["contact_person"],
            rec["department"], rec["email"], rec["source_url"],
            college_key(rec["college_name"], rec["city"]), utcnow(), contact_id,
        ),
    )
    conn.commit()


def delete_contacts(conn: sqlite3.Connection, ids: Iterable[int]) -> int:
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    cur = conn.execute(f"DELETE FROM contacts WHERE id IN ({marks})", ids)
    conn.commit()
    return cur.rowcount


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_contacts(
    conn: sqlite3.Connection, text: str = "", state: str = "", city: str = "",
    verification: str = "", ids: Optional[Iterable[int]] = None,
) -> pd.DataFrame:
    sql, params = "SELECT * FROM contacts WHERE 1=1", []
    if ids is not None:
        ids = [int(i) for i in ids]
        if not ids:
            return pd.read_sql_query("SELECT * FROM contacts WHERE 1=0", conn)
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params += ids
    text = clean(text)
    if text:
        sql += " AND (" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in SEARCH_COLUMNS) + ")"
        params += [_like(text)] * len(SEARCH_COLUMNS)
    if state:
        sql += " AND state = ?"
        params.append(state)
    if city:
        sql += " AND city = ?"
        params.append(city)
    if verification in ("verified", "unverified"):
        sql += " AND verification_status = ?"
        params.append(verification)
    sql += " ORDER BY state, city, college_name, email"
    return pd.read_sql_query(sql, conn, params=params)


def list_states(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT state FROM contacts ORDER BY state")]


def list_cities(conn: sqlite3.Connection, state: str = "") -> list[str]:
    if state:
        rows = conn.execute("SELECT DISTINCT city FROM contacts WHERE state=? ORDER BY city", (state,))
    else:
        rows = conn.execute("SELECT DISTINCT city FROM contacts ORDER BY city")
    return [r[0] for r in rows]


def find_duplicate_colleges(conn: sqlite3.Connection) -> pd.DataFrame:
    """Contacts that share a college (same normalised name + city)."""
    return pd.read_sql_query(
        "SELECT college_key, id, college_name, city, state, department, contact_person, email "
        "FROM contacts WHERE college_key IN "
        "(SELECT college_key FROM contacts GROUP BY college_key HAVING COUNT(*) > 1) "
        "ORDER BY college_key, id",
        conn,
    )


# ----------------------------------------------------------------- verification
def verification_problems(contact: Mapping[str, Any]) -> list[str]:
    """Automated checks that must pass before a human may mark a contact verified."""
    problems = []
    website, source, email = contact.get("website", ""), contact.get("source_url", ""), contact.get("email", "")
    if not validate_url(website):
        problems.append("An official website URL is required.")
    if not validate_url(source):
        problems.append("A source page URL (the page where the email was found) is required.")
    if validate_url(website) and validate_url(source) and not domains_related(host_of(website), host_of(source)):
        problems.append("The source page is not on the college's official website domain.")
    domain = email_domain(email)
    if domain in FREE_EMAIL_DOMAINS:
        problems.append(f"{domain} is a public mailbox provider, not an official college domain.")
    elif validate_url(website) and domain and domains_related(domain, host_of(website)) is False:
        problems.append("The email domain does not match the official website domain.")
    return problems


def verify_contact(
    conn: sqlite3.Connection, contact_id: int, verified_by: str, note: str, confirmed_on_source: bool
) -> None:
    contact = get_contact(conn, contact_id)
    if not contact:
        raise ContactError(f"Contact {contact_id} not found")
    problems = verification_problems(contact)
    if not clean(verified_by):
        problems.append("Enter your name as the person who verified this record.")
    if not confirmed_on_source:
        problems.append("You must confirm that you saw this email on the source page.")
    if problems:
        raise ContactError(problems)
    conn.execute(
        "UPDATE contacts SET verification_status='verified', verified_by=?, verified_at=?, "
        "verification_note=?, updated_at=? WHERE id=?",
        (clean(verified_by), utcnow(), clean(note), utcnow(), contact_id),
    )
    conn.commit()


def unverify_contact(conn: sqlite3.Connection, contact_id: int) -> None:
    conn.execute(
        "UPDATE contacts SET verification_status='unverified', verified_by='', verified_at=NULL, "
        "verification_note='', updated_at=? WHERE id=?",
        (utcnow(), contact_id),
    )
    conn.commit()


# ----------------------------------------------------------------------- export
def _safe_cell(value: Any) -> Any:
    """Neutralise spreadsheet formula injection in exported text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _export_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.reindex(columns=EXPORT_COLUMNS).fillna("")
    return out.apply(lambda col: col.map(_safe_cell))


def export_csv(df: pd.DataFrame) -> bytes:
    return _export_frame(df).to_csv(index=False).encode("utf-8-sig")


def export_xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _export_frame(df).to_excel(writer, index=False, sheet_name="Contacts")
    return buf.getvalue()
