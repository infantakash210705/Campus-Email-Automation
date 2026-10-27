"""Auto-approval engine for scraped contact_records.

Scores each discovered contact 0-100 based on evidence quality, then
auto-approves those meeting the configured threshold.  Contacts that score
below the threshold are left as 'pending_review' for human action.

Scoring rules (additive):
  +50  Email domain matches the college's official website domain
  +20  Email is NOT a free/public mailbox (gmail, yahoo, etc.)
  +20  Source URL is on the same domain as the college website
  +10  Source URL path contains a contact/admissions/placement keyword

All writes are idempotent: already-approved rows are skipped.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional
from urllib.parse import urlparse

from .database import utcnow
from .validation import FREE_EMAIL_DOMAINS, domains_related, email_domain, host_of

log = logging.getLogger(__name__)

# Keywords in source URLs that indicate a contact-rich page.
CONTACT_PAGE_KEYWORDS = (
    "contact", "admissions", "admission", "placement", "faculty",
    "reach", "enquiry", "enquiries", "about", "department",
)

DEFAULT_THRESHOLD = 80  # out of 100


# ------------------------------------------------------------------ scoring

def confidence_score(
    email: str,
    source_url: str,
    website: str,
) -> int:
    """Return an integer 0-100 confidence score for a single contact record.

    Higher = more trustworthy.  The score is additive; each rule is
    independent so partial credit is possible.
    """
    score = 0
    email = (email or "").strip().lower()
    website = (website or "").strip()
    source_url = (source_url or "").strip()

    domain = email_domain(email)
    website_host = host_of(website)
    source_host = host_of(source_url)

    # Rule 1 (+50): email domain matches college website domain
    if domain and website_host and domains_related(domain, website_host):
        score += 50

    # Rule 2 (+20): not a free/public mailbox
    if domain and domain not in FREE_EMAIL_DOMAINS:
        score += 20

    # Rule 3 (+20): source URL is on the same domain as the college website
    if source_host and website_host and domains_related(source_host, website_host):
        score += 20

    # Rule 4 (+10): source URL path looks like a contact/admissions page
    path = urlparse(source_url).path.lower() if source_url else ""
    if any(kw in path for kw in CONTACT_PAGE_KEYWORDS):
        score += 10

    return min(score, 100)


# ------------------------------------------------------------------ batch approval

def auto_approve_pending(
    conn: sqlite3.Connection,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    dry_run: bool = False,
) -> dict:
    """Scan all 'discovered'/'pending_review' contact_records and approve
    those whose confidence score meets or exceeds `threshold`.

    Returns a summary dict:
      {approved: int, skipped_low_score: int, already_approved: int, errors: int}

    When `dry_run=True`, no DB changes are made but the summary is computed.
    """
    approved = skipped = errors = already = 0
    now = utcnow()

    rows = conn.execute(
        "SELECT cr.id, cr.email, cr.source_url, cr.status, "
        "       c.website "
        "FROM contact_records cr "
        "JOIN colleges c ON cr.college_id = c.id "
        "WHERE cr.status IN ('discovered', 'pending_review')"
    ).fetchall()

    for row in rows:
        try:
            score = confidence_score(
                email=row["email"],
                source_url=row["source_url"],
                website=row["website"],
            )
            if score >= threshold:
                if not dry_run:
                    conn.execute(
                        "UPDATE contact_records SET status='approved', updated_at=? WHERE id=?",
                        (now, row["id"]),
                    )
                    conn.execute(
                        "UPDATE contact_changes SET approval_status='auto_applied', "
                        "reviewed_by='auto_approve', reviewed_at=? "
                        "WHERE college_id = (SELECT college_id FROM contact_records WHERE id=?) "
                        "  AND approval_status='pending'",
                        (now, row["id"]),
                    )
                    conn.commit()
                approved += 1
                log.debug(
                    "Auto-approved contact %s (score=%d)", row["email"], score
                )
            else:
                skipped += 1
                log.debug(
                    "Contact %s skipped (score=%d < threshold=%d)",
                    row["email"], score, threshold,
                )
        except Exception as exc:
            errors += 1
            log.warning("Error scoring contact id=%s: %s", row["id"], exc)

    summary = {
        "approved": approved,
        "skipped_low_score": skipped,
        "already_approved": already,
        "errors": errors,
        "dry_run": dry_run,
    }
    log.info("auto_approve_pending: %s", summary)
    return summary


# ------------------------------------------------------------------ contact sync to main table

def sync_approved_to_contacts(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict:
    """Copy newly-approved contact_records into the main `contacts` table.

    Only copies rows where:
      - contact_records.status = 'approved'
      - The email does NOT already exist in contacts
      - The college has state/city available

    Returns {synced: int, skipped_duplicate: int, errors: int}
    """
    synced = skipped = errors = 0
    now = utcnow()

    rows = conn.execute(
        "SELECT cr.id, cr.email, cr.source_url, cr.department, cr.contact_person, "
        "       c.name AS college_name, c.website, c.state, c.city "
        "FROM contact_records cr "
        "JOIN colleges c ON cr.college_id = c.id "
        "WHERE cr.status = 'approved'"
    ).fetchall()

    for row in rows:
        # Check if already in contacts
        existing = conn.execute(
            "SELECT id FROM contacts WHERE email = ?",
            (row["email"].lower(),)
        ).fetchone()
        if existing:
            skipped += 1
            continue
        # Need state + city to insert
        if not row["state"] or not row["city"]:
            skipped += 1
            continue
        try:
            if not dry_run:
                from .validation import college_key as make_key
                conn.execute(
                    "INSERT OR IGNORE INTO contacts "
                    "(state, city, college_name, website, contact_person, department, "
                    " email, source_url, college_key, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["state"], row["city"], row["college_name"],
                        row["website"], row["contact_person"] or "",
                        row["department"] or "",
                        row["email"].lower(), row["source_url"],
                        make_key(row["college_name"], row["city"]),
                        now, now,
                    ),
                )
                conn.commit()
            synced += 1
        except Exception as exc:
            errors += 1
            log.warning("Error syncing contact %s to contacts table: %s", row["email"], exc)

    summary = {"synced": synced, "skipped_duplicate": skipped, "errors": errors, "dry_run": dry_run}
    log.info("sync_approved_to_contacts: %s", summary)
    return summary
