"""Notification service for monitoring events.

Reuses the project's existing SMTP mailer (shortfundly.mailer). Notifications go
ONLY to the authorised project user (cfg.notify_recipient) — never to scraped
college contacts. Deduplication via the notifications_log table prevents the
same alert being sent repeatedly across checks.

Credentials come from the existing Config (environment / .env). Nothing is
hard-coded. If notifications are disabled or no recipient is set, send_event is
a safe no-op that returns a reason.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Optional

from shortfundly.database import utcnow


def dedupe_key(event_type: str, identity: str) -> str:
    raw = f"{event_type}|{identity}".strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def already_sent(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM notifications_log WHERE dedupe_key=?", (key,)
    ).fetchone()
    return row is not None


def _log_sent(conn: sqlite3.Connection, *, event_type: str, key: str,
              recipient: str, subject: str) -> None:
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO notifications_log "
                "(event_type, dedupe_key, recipient, subject, sent_at) VALUES (?,?,?,?,?)",
                (event_type, key, recipient, subject, utcnow()),
            )
    except sqlite3.Error:
        pass


def build_event_message(event_type: str, details: dict) -> tuple:
    """Return (subject, body) for a monitoring event. Pure function."""
    name = details.get("college_name", details.get("name", "a college"))
    if event_type == "new_college":
        subject = f"[Shortfundly Monitor] New college discovered: {name}"
        body = f"A new college was discovered and awaits review:\n\n{name}\n{details.get('website','')}"
    elif event_type == "email_changed":
        subject = f"[Shortfundly Monitor] Contact change at {name}"
        body = (f"A contact change was detected for {name} and needs approval:\n\n"
                f"Field: {details.get('field','email')}\n"
                f"Old: {details.get('old_value','')}\n"
                f"New: {details.get('new_value','')}\n"
                f"Source: {details.get('source_url','')}")
    elif event_type == "job_failing":
        subject = "[Shortfundly Monitor] Monitoring job repeatedly failing"
        body = f"A monitoring job has failed repeatedly. Details: {details.get('detail','')}"
    elif event_type == "pending_batch":
        subject = "[Shortfundly Monitor] Records awaiting approval"
        body = f"{details.get('count', 0)} record(s)/change(s) await your approval in the dashboard."
    else:
        subject = f"[Shortfundly Monitor] {event_type}"
        body = str(details)
    return subject, body


def send_event(conn: sqlite3.Connection, cfg, event_type: str, *,
               identity: str, details: Optional[dict] = None, mailer=None) -> dict:
    """Send a single notification if enabled, configured, and not a duplicate.

    Returns {"sent": bool, "reason": str, "dedupe_key": str}. Never raises for
    SMTP problems — they are reported in the reason.
    """
    details = details or {}
    key = dedupe_key(event_type, identity)
    if not getattr(cfg, "notifications_enabled", False):
        return {"sent": False, "reason": "notifications_disabled", "dedupe_key": key}
    recipient = getattr(cfg, "notify_recipient", "") or getattr(cfg, "test_recipient", "")
    if not recipient:
        return {"sent": False, "reason": "no_recipient", "dedupe_key": key}
    if already_sent(conn, key):
        return {"sent": False, "reason": "duplicate_suppressed", "dedupe_key": key}

    subject, body = build_event_message(event_type, {**details})
    if not getattr(cfg, "smtp_configured", False):
        return {"sent": False, "reason": "smtp_not_configured", "dedupe_key": key}

    try:
        if mailer is None:
            from shortfundly.mailer import SMTPMailer, build_message
            mailer = SMTPMailer(cfg)
            msg = build_message(
                from_email=cfg.from_email, from_name=cfg.from_name or "Shortfundly Monitor",
                to_email=recipient, subject=subject, body=body,
            )
            mailer.send(msg)
        else:
            # test double: accepts (to, subject, body)
            mailer.send_simple(recipient, subject, body)
    except Exception as exc:  # noqa: BLE001
        return {"sent": False, "reason": f"send_failed: {type(exc).__name__}", "dedupe_key": key}

    _log_sent(conn, event_type=event_type, key=key, recipient=recipient, subject=subject)
    return {"sent": True, "reason": "sent", "dedupe_key": key}
