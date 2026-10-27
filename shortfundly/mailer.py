"""SMTP delivery. Only this module talks to the network."""
from __future__ import annotations

import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import List, Optional, Protocol, Union

from .config import Config


class RealSMTPBlockedError(RuntimeError):
    """Raised if real SMTP is attempted while the automated test-suite is running."""


class Mailer(Protocol):
    def send(self, msg: EmailMessage) -> None: ...


def build_message(
    *, from_email: str, from_name: str, to_email: str, subject: str, body: str,
    reply_to: Optional[str] = None,
    attachments: Optional[List[Union[str, Path]]] = None,
) -> EmailMessage:
    for label, value in (("from", from_email), ("to", to_email), ("subject", subject), ("from name", from_name)):
        if "\r" in value or "\n" in value:
            raise ValueError(f"Line break in the {label} header is not allowed")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_email.rpartition("@")[2] or None)
    # quoted-printable keeps the body 7-bit safe (the rupee sign etc. survive any relay)
    msg.set_content(body, charset="utf-8", cte="quoted-printable")

    # ── Attachments ──────────────────────────────────────────────────────────
    for attachment in (attachments or []):
        path = Path(attachment)
        if not path.is_file():
            raise FileNotFoundError(f"Attachment not found: {path}")
        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type:
            main_type, sub_type = mime_type.split("/", 1)
        else:
            main_type, sub_type = "application", "octet-stream"
        with path.open("rb") as fh:
            data = fh.read()
        msg.add_attachment(data, maintype=main_type, subtype=sub_type, filename=path.name)

    return msg


class SMTPMailer:
    """Sends one message per connection using the SMTP settings from the environment."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @staticmethod
    def _guard() -> None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise RealSMTPBlockedError("Real SMTP is disabled while pytest is running.")

    def _connect(self) -> smtplib.SMTP:
        c = self.cfg
        if c.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                c.smtp_host, c.smtp_port, timeout=c.smtp_timeout, context=ssl.create_default_context()
            )
        else:
            server = smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=c.smtp_timeout)
        try:
            if c.smtp_security != "ssl":
                server.ehlo()
                if c.smtp_security == "starttls":
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
            if c.smtp_username:
                server.login(c.smtp_username, c.smtp_password)
        except Exception:
            server.close()
            raise
        return server

    @staticmethod
    def _close(server: smtplib.SMTP) -> None:
        try:
            server.quit()
        except Exception:
            server.close()

    def check_connection(self) -> None:
        """Connect and authenticate without sending anything."""
        self._guard()
        self._close(self._connect())

    def send(self, msg: EmailMessage) -> None:
        self._guard()
        server = self._connect()
        try:
            refused = server.send_message(msg)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        finally:
            self._close(server)
