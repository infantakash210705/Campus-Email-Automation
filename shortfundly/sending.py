"""Queueing, sending, status tracking, duplicate prevention and retries.

Safety model
------------
* TEST mode (default): the real recipient is stored for reference, but the message is
  ALWAYS delivered to the internal TEST_RECIPIENT. The override is re-applied at send time.
* LIVE mode needs SEND_MODE=live in the environment AND explicit confirmation per batch.
* A unique database index allows only one live row per (campaign, recipient).
* A message is claimed atomically (pending/failed -> sending) before SMTP is contacted, so a
  successful ('sent') message can never be claimed again.
"""
from __future__ import annotations

import hashlib
import smtplib
import socket
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, List, Mapping, Optional, Union

import pandas as pd

from .config import Config
from .database import utcnow
from .mailer import Mailer, RealSMTPBlockedError, build_message
from .template_service import render_email
from .validation import clean, validate_email

PENDING, SENDING, SENT, FAILED = "pending", "sending", "sent", "failed"
INTERRUPTED_PREFIX = "Interrupted while sending"
MAX_CONSECUTIVE_CONNECTION_FAILURES = 3

SMTP_ACCEPTANCE_NOTE = (
    "'Sent' means the SMTP server ACCEPTED the message for delivery. It does not guarantee that "
    "it reached the inbox: it can still bounce later, be filtered as spam, or be delayed."
)


class SendingError(Exception):
    pass


class SendingBlocked(SendingError):
    """A safety rule prevented sending (nothing was sent)."""


@dataclass
class QueueResult:
    batch_id: str
    created_ids: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # (email, reason)


@dataclass
class BatchResult:
    accepted_ids: list = field(default_factory=list)
    failed_ids: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # (log id, reason)
    not_attempted: int = 0
    aborted: bool = False
    abort_reason: str = ""

    @property
    def accepted(self) -> int:
        return len(self.accepted_ids)

    @property
    def failed(self) -> int:
        return len(self.failed_ids)


def dedupe_key(campaign: str, email: str) -> str:
    raw = f"{clean(campaign).casefold()}|{clean(email).lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_sending_ready(cfg: Config, live: bool) -> None:
    if not cfg.smtp_configured:
        raise SendingBlocked(
            "SMTP is not configured. Set SMTP_HOST, SMTP_PORT and SMTP_FROM_EMAIL "
            "(plus SMTP_USERNAME / SMTP_PASSWORD) in your .env file."
        )
    if not validate_email(cfg.from_email):
        raise SendingBlocked("SMTP_FROM_EMAIL is not a valid email address.")
    if live:
        if cfg.send_mode != "live":
            raise SendingBlocked(
                "Live sending is disabled: SEND_MODE is 'test'. Set SEND_MODE=live in .env and "
                "restart the app to allow real recipients."
            )
    elif not validate_email(cfg.test_recipient):
        raise SendingBlocked(
            "Test mode needs TEST_RECIPIENT in .env: an address you control. "
            "All test emails are redirected there."
        )


# ------------------------------------------------------------------- queueing
def queue_messages(
    conn: sqlite3.Connection, cfg: Config, contacts: Iterable[Mapping[str, Any]],
    subject_template: str, body_template: str, sender: Mapping[str, Any], campaign: str,
    *, live: bool = False, live_confirmed: bool = False,
) -> QueueResult:
    """Render and store one PENDING row per contact. Nothing is sent here."""
    contacts = list(contacts)
    campaign = " ".join(clean(campaign).split())
    if not campaign:
        raise SendingBlocked("A campaign label is required (duplicate protection is per campaign).")
    if live and not live_confirmed:
        raise SendingBlocked("Live sending requires explicit confirmation.")
    check_sending_ready(cfg, live)
    if not contacts:
        raise SendingBlocked("No recipients selected.")
    if len(contacts) > cfg.max_batch_size:
        raise SendingBlocked(f"Batch too large: {len(contacts)} recipients (MAX_BATCH_SIZE={cfg.max_batch_size}).")
    if not clean(sender.get("sender_name")) or not clean(sender.get("sender_title")):
        raise SendingBlocked("Configure the sender name and title first; the app will not invent them.")

    result = QueueResult(batch_id=uuid.uuid4().hex[:12])
    now = utcnow()
    for c in contacts:
        email = clean(c.get("email")).lower()
        if not validate_email(email):
            result.skipped.append((email or "(none)", "Invalid email address"))
            continue
        rendered = render_email(subject_template, body_template, c, sender)
        if rendered.problems:
            result.skipped.append((email, "; ".join(rendered.problems)))
            continue
        subject, body = rendered.subject, rendered.body
        if live:
            delivered_to = email
        else:
            delivered_to = cfg.test_recipient
            subject = f"[TEST] {subject}"
            body = f"[TEST MODE - intended recipient: {email} ({c.get('college_name', '')})]\n\n{body}"
        key = dedupe_key(campaign, email)
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO email_log (batch_id, campaign, contact_id, recipient_email, delivered_to, "
                    "recipient_name, college_name, subject, body, status, attempt_count, is_test, "
                    "dedupe_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (result.batch_id, campaign, c.get("id"), email, delivered_to,
                     clean(c.get("contact_person")), clean(c.get("college_name")), subject, body,
                     PENDING, 0, 0 if live else 1, key, now, now),
                )
            result.created_ids.append(int(cur.lastrowid))
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT status FROM email_log WHERE dedupe_key=? AND is_test=0", (key,)
            ).fetchone()
            state = row["status"] if row else "recorded"
            hint = " - use Retry in History" if state == FAILED else ""
            result.skipped.append((email, f"Already {state} in campaign '{campaign}'{hint}"))
    return result


# -------------------------------------------------------------------- sending
def _describe_error(exc: BaseException, cfg: Config) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        msg = (f"SMTP authentication failed (server code {exc.smtp_code}). Check SMTP_USERNAME / "
               "SMTP_PASSWORD; many providers require an app password.")
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        msg = f"Recipient refused by the server: {exc}"
    elif isinstance(exc, smtplib.SMTPSenderRefused):
        msg = f"Sender refused by the server (code {exc.smtp_code}). Check SMTP_FROM_EMAIL."
    elif isinstance(exc, smtplib.SMTPServerDisconnected):
        msg = f"Server disconnected unexpectedly: {exc}"
    elif isinstance(exc, smtplib.SMTPConnectError):
        msg = f"Could not connect to the SMTP server: {exc}"
    elif isinstance(exc, (TimeoutError, socket.timeout)):
        msg = f"Connection timed out after {cfg.smtp_timeout}s."
    elif isinstance(exc, socket.gaierror):
        msg = "SMTP host name could not be resolved. Check SMTP_HOST."
    elif isinstance(exc, ConnectionError):
        msg = f"Connection error: {exc}"
    else:
        msg = f"{type(exc).__name__}: {exc}"
    return cfg.redact(msg)[:500]


def _is_connection_problem(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, ConnectionError,
         TimeoutError, socket.timeout, socket.gaierror),
    )


def process_queue(
    conn: sqlite3.Connection, mailer: Mailer, cfg: Config, ids: Iterable[int], *,
    statuses: tuple = (PENDING,), include_interrupted: bool = False,
    progress: Optional[Callable[[int, int, str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
    attachments: Optional[List[Union[str]]] = None,
) -> BatchResult:
    """Send the given log rows one by one. Never raises for a single failed message."""
    result = BatchResult()
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not ids:
        return result
    rows = conn.execute(
        f"SELECT * FROM email_log WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id", ids
    ).fetchall()
    if any(r["is_test"] == 0 for r in rows):
        check_sending_ready(cfg, live=True)
    if any(r["is_test"] == 1 for r in rows):
        check_sending_ready(cfg, live=False)

    marks = ",".join("?" * len(statuses))
    consecutive_conn_failures = 0
    total = len(rows)
    for pos, row in enumerate(rows):
        rid = row["id"]
        if row["status"] not in statuses:
            result.skipped.append((rid, f"status is '{row['status']}'"))
            continue
        if row["status"] == FAILED and not include_interrupted and (row["last_error"] or "").startswith(INTERRUPTED_PREFIX):
            result.skipped.append((rid, "interrupted earlier: delivery state unknown, confirm manually"))
            continue
        # Enforced override: test rows ALWAYS go to the internal recipient, whatever is stored.
        delivered_to = cfg.test_recipient if row["is_test"] else row["recipient_email"]
        now = utcnow()
        claim = conn.execute(
            f"UPDATE email_log SET status='sending', attempt_count=attempt_count+1, last_attempt_at=?, "
            f"updated_at=?, last_error=NULL, delivered_to=? "
            f"WHERE id=? AND status IN ({marks}) AND attempt_count < ?",
            (now, now, delivered_to, rid, *statuses, cfg.max_attempts),
        )
        conn.commit()
        if claim.rowcount != 1:
            result.skipped.append((rid, f"not claimable (already processed or {cfg.max_attempts} attempts used)"))
            continue
        try:
            msg = build_message(
                from_email=cfg.from_email, from_name=cfg.from_name, to_email=delivered_to,
                subject=row["subject"], body=row["body"],
                attachments=attachments or [],
            )
            mailer.send(msg)
        except Exception as exc:  # noqa: BLE001 - every failure must be recorded, not raised
            conn.execute(
                "UPDATE email_log SET status='failed', last_error=?, updated_at=? WHERE id=?",
                (_describe_error(exc, cfg), utcnow(), rid),
            )
            conn.commit()
            result.failed_ids.append(rid)
            consecutive_conn_failures = consecutive_conn_failures + 1 if _is_connection_problem(exc) else 0
            if isinstance(exc, smtplib.SMTPAuthenticationError):
                result.aborted, result.abort_reason = True, "Authentication failed; remaining messages were not attempted."
            elif isinstance(exc, RealSMTPBlockedError):
                result.aborted, result.abort_reason = True, "Real SMTP is blocked in this environment."
            elif consecutive_conn_failures >= MAX_CONSECUTIVE_CONNECTION_FAILURES:
                result.aborted = True
                result.abort_reason = "Repeated connection failures; remaining messages were not attempted."
            if progress:
                progress(pos + 1, total, f"FAILED {row['recipient_email']}")
            if result.aborted:
                result.not_attempted = sum(
                    1 for r in rows[pos + 1:] if r["status"] in statuses
                )
                break
        else:
            consecutive_conn_failures = 0
            conn.execute(
                "UPDATE email_log SET status='sent', accepted_at=?, last_error=NULL, updated_at=? WHERE id=?",
                (utcnow(), utcnow(), rid),
            )
            conn.commit()
            result.accepted_ids.append(rid)
            if progress:
                progress(pos + 1, total, f"accepted {row['recipient_email']}")
        if cfg.send_delay_seconds > 0 and pos < total - 1:
            sleep(cfg.send_delay_seconds)
    return result


def retry_failed(
    conn: sqlite3.Connection, mailer: Mailer, cfg: Config, ids: Optional[Iterable[int]] = None, *,
    include_interrupted: bool = False, progress=None, sleep=time.sleep,
    attachments: Optional[List[Union[str]]] = None,
) -> BatchResult:
    """Retry FAILED rows only. 'sent' rows are never touched."""
    if ids is None:
        ids = [r[0] for r in conn.execute("SELECT id FROM email_log WHERE status='failed'")]
    return process_queue(conn, mailer, cfg, ids, statuses=(FAILED,),
                         include_interrupted=include_interrupted, progress=progress, sleep=sleep,
                         attachments=attachments)


def recover_stuck_sending(conn: sqlite3.Connection, older_than_minutes: int = 15) -> int:
    """Rows left in 'sending' by a crash become 'failed' with an explicit warning.

    They are NOT retried automatically because the server may already have accepted them.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "UPDATE email_log SET status='failed', updated_at=?, last_error=? "
        "WHERE status='sending' AND last_attempt_at <= ?",
        (utcnow(), INTERRUPTED_PREFIX + " (app stopped or crashed). The server may or may not have "
         "accepted it - check your Sent folder before retrying.", cutoff),
    )
    conn.commit()
    return cur.rowcount


# -------------------------------------------------------------------- queries
def emails_in_campaign(conn: sqlite3.Connection, campaign: str) -> set:
    rows = conn.execute(
        "SELECT recipient_email FROM email_log WHERE is_test=0 AND campaign=?",
        (" ".join(clean(campaign).split()),),
    )
    return {r[0] for r in rows}


def history_dataframe(conn: sqlite3.Connection, text: str = "", status: str = "", mode: str = "") -> pd.DataFrame:
    sql, params = "SELECT * FROM email_log WHERE 1=1", []
    text = clean(text)
    if text:
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        cols = ("recipient_email", "delivered_to", "college_name", "subject", "campaign", "last_error")
        sql += " AND (" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in cols) + ")"
        params += [like] * len(cols)
    if status in (PENDING, SENDING, SENT, FAILED):
        sql += " AND status = ?"
        params.append(status)
    if mode in ("live", "test"):
        sql += " AND is_test = ?"
        params.append(1 if mode == "test" else 0)
    sql += " ORDER BY id DESC"
    return pd.read_sql_query(sql, conn, params=params)
