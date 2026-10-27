"""Auto-sender: queue and dispatch emails to newly-approved contacts.

Runs as the last step of the worker pipeline, ONLY when both conditions hold:
  - AUTO_SEND_ENABLED=true  in .env
  - SEND_MODE=live           in .env

Contacts that have already been emailed in the configured campaign are skipped
(the existing deduplication index in email_log enforces this at the DB level).

Safety model (same as the dashboard):
  - Each contact is only emailed once per campaign label.
  - The campaign label defaults to "auto" but is configurable.
  - A summary of what was sent is logged and returned.
  - If SMTP is misconfigured, the run aborts immediately with a clear error.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

log = logging.getLogger(__name__)


def _load_default_template(conn: sqlite3.Connection) -> tuple[str, str]:
    """Return (subject, body) from the saved default template, or the file fallback."""
    row = conn.execute(
        "SELECT subject, body FROM email_templates WHERE name='Default' LIMIT 1"
    ).fetchone()
    if row:
        return row["subject"], row["body"]

    # Fallback: read the on-disk default template files.
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent / "templates"
    try:
        subject = (base / "default_subject.txt").read_text(encoding="utf-8").strip()
        body = (base / "default_email_body.txt").read_text(encoding="utf-8").strip()
        return subject, body
    except FileNotFoundError:
        return "Shortfundly Campus Partnership Proposal", ""


def _get_new_approved_contacts(conn: sqlite3.Connection, campaign: str) -> list[dict]:
    """Return approved contacts from the contacts table not yet in this campaign."""
    # Collect emails already queued/sent in this campaign.
    sent_emails = {
        r[0]
        for r in conn.execute(
            "SELECT recipient_email FROM email_log WHERE campaign=? AND is_test=0",
            (campaign,),
        )
    }

    rows = conn.execute(
        "SELECT * FROM contacts WHERE verification_status='verified' OR verification_status='unverified'"
    ).fetchall()

    results: list[dict] = []
    for r in rows:
        email = (r["email"] or "").lower()
        if email and email not in sent_emails:
            results.append(dict(r))
    return results


def auto_send_new_contacts(
    conn: sqlite3.Connection,
    cfg,
    *,
    attachments: Optional[list] = None,
    dry_run: bool = False,
) -> dict:
    """Queue and send emails to contacts approved since the last run.

    Returns:
      {queued: int, sent: int, failed: int, skipped: int,
       aborted: bool, abort_reason: str, dry_run: bool}
    """
    result = {
        "queued": 0, "sent": 0, "failed": 0, "skipped": 0,
        "aborted": False, "abort_reason": "", "dry_run": dry_run,
    }

    # Guard: only run if explicitly enabled and in live mode.
    if not getattr(cfg, "auto_send_enabled", False):
        log.info("auto_send disabled (AUTO_SEND_ENABLED not set). Skipping.")
        result["abort_reason"] = "AUTO_SEND_ENABLED is not set"
        return result

    if cfg.send_mode != "live":
        log.warning("auto_send skipped: SEND_MODE=%s (must be 'live')", cfg.send_mode)
        result["abort_reason"] = f"SEND_MODE is '{cfg.send_mode}', not 'live'"
        return result

    campaign = getattr(cfg, "auto_send_campaign", "auto") or "auto"
    subject_tpl, body_tpl = _load_default_template(conn)

    if not body_tpl:
        log.error("auto_send: no email body template found — aborting")
        result["aborted"] = True
        result["abort_reason"] = "No email body template configured"
        return result

    contacts = _get_new_approved_contacts(conn, campaign)
    if not contacts:
        log.info("auto_send: no new contacts to email")
        return result

    log.info("auto_send: %d contact(s) to email in campaign '%s'", len(contacts), campaign)

    if dry_run:
        result["queued"] = len(contacts)
        log.info("dry_run=True — would email %d contacts", len(contacts))
        return result

    # Queue messages using the existing sending pipeline.
    from .sending import queue_messages, process_queue, SendingBlocked
    from .mailer import SMTPMailer

    sender = {
        "sender_name": cfg.sender_name or "Shortfundly Campus",
        "sender_title": cfg.sender_title or "Campus Outreach",
    }

    try:
        queue_result = queue_messages(
            conn, cfg, contacts,
            subject_template=subject_tpl,
            body_template=body_tpl,
            sender=sender,
            campaign=campaign,
            live=True,
            live_confirmed=True,   # auto mode always confirms
        )
    except SendingBlocked as exc:
        log.error("auto_send: queue blocked — %s", exc)
        result["aborted"] = True
        result["abort_reason"] = str(exc)
        return result

    result["queued"] = len(queue_result.created_ids)
    result["skipped"] += len(queue_result.skipped)

    if not queue_result.created_ids:
        log.info("auto_send: nothing queued (all skipped)")
        return result

    # Send immediately.
    try:
        mailer = SMTPMailer(cfg)
        batch = process_queue(conn, mailer, cfg, queue_result.created_ids, attachments=attachments)
    except Exception as exc:
        log.error("auto_send: process_queue raised: %s", exc)
        result["aborted"] = True
        result["abort_reason"] = str(exc)
        return result

    result["sent"] = batch.accepted
    result["failed"] = batch.failed
    result["aborted"] = batch.aborted
    result["abort_reason"] = batch.abort_reason
    result["skipped"] += len(batch.skipped)

    log.info("auto_send complete: %s", result)
    return result
