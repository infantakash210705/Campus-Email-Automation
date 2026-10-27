"""Editable email templates: placeholder rendering, upload decoding and persistence."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .config import BASE_DIR
from .database import utcnow
from .validation import clean

TEMPLATE_DIR = BASE_DIR / "templates"
DEFAULT_TEMPLATE_NAME = "Default Proposal"
ALLOWED_PLACEHOLDERS = (
    "college_name", "recipient_name", "department", "city", "state", "sender_name", "sender_title",
)
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
FALLBACK_RECIPIENT = "Sir/Madam"        # generic salutation when no contact person is stored
FALLBACK_DEPARTMENT = "Concerned Department"
MAX_UPLOAD_BYTES = 200 * 1024


@dataclass
class RenderResult:
    text: str
    missing: list = field(default_factory=list)   # known placeholders with no value
    unknown: list = field(default_factory=list)   # placeholders the app does not support


@dataclass
class RenderedEmail:
    subject: str
    body: str
    missing: list = field(default_factory=list)
    unknown: list = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        out = []
        if self.missing:
            out.append("No value for: " + ", ".join("{" + m + "}" for m in self.missing))
        if self.unknown:
            out.append("Unknown placeholder(s): " + ", ".join("{" + u + "}" for u in self.unknown))
        return out


def extract_placeholders(text: str) -> list[str]:
    seen: list[str] = []
    for m in PLACEHOLDER_RE.finditer(text or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def render(text: str, context: Mapping[str, Any]) -> RenderResult:
    """Replace {placeholders}. Unresolvable ones are left visible and reported, never guessed."""
    missing: list = []
    unknown: list = []

    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in ALLOWED_PLACEHOLDERS:
            if key not in unknown:
                unknown.append(key)
            return match.group(0)
        value = clean(context.get(key, ""))
        if not value:
            if key not in missing:
                missing.append(key)
            return match.group(0)
        return value

    return RenderResult(PLACEHOLDER_RE.sub(sub, text or ""), missing, unknown)


def build_context(contact: Mapping[str, Any], sender: Mapping[str, Any]) -> dict:
    return {
        "college_name": contact.get("college_name", ""),
        "recipient_name": clean(contact.get("contact_person")) or FALLBACK_RECIPIENT,
        "department": clean(contact.get("department")) or FALLBACK_DEPARTMENT,
        "city": contact.get("city", ""),
        "state": contact.get("state", ""),
        "sender_name": sender.get("sender_name", ""),
        "sender_title": sender.get("sender_title", ""),
    }


def render_email(
    subject_template: str, body_template: str, contact: Mapping[str, Any], sender: Mapping[str, Any]
) -> RenderedEmail:
    ctx = build_context(contact, sender)
    subj = render(subject_template, ctx)
    body = render(body_template, ctx)
    return RenderedEmail(
        subject=" ".join(subj.text.split()),  # a subject must be a single line (no header injection)
        body=body.text,
        missing=list(dict.fromkeys(subj.missing + body.missing)),
        unknown=list(dict.fromkeys(subj.unknown + body.unknown)),
    )


def decode_upload(data: bytes) -> str:
    """Decode an uploaded .txt template body."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Template file is too large (limit 200 KB).")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise ValueError("Could not decode the text file.")
    text = text.replace("\r\n", "\n").strip("\n")
    if not text.strip():
        raise ValueError("The uploaded file is empty.")
    return text


def load_default_template() -> tuple[str, str]:
    subject = (TEMPLATE_DIR / "default_subject.txt").read_text(encoding="utf-8").strip()
    body = (TEMPLATE_DIR / "default_email_body.txt").read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    return subject, body


# ----------------------------------------------------------------- persistence
def save_template(conn: sqlite3.Connection, name: str, subject: str, body: str) -> None:
    name = clean(name)
    if not name:
        raise ValueError("Template name is required.")
    if not clean(subject) or not clean(body):
        raise ValueError("Subject and body cannot be empty.")
    conn.execute(
        "INSERT INTO email_templates(name, subject, body, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET subject=excluded.subject, body=excluded.body, "
        "updated_at=excluded.updated_at",
        (name, " ".join(subject.split()), body.replace("\r\n", "\n"), utcnow()),
    )
    conn.commit()


def load_template(conn: sqlite3.Connection, name: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM email_templates WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_templates(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM email_templates ORDER BY name")]


def delete_template(conn: sqlite3.Connection, name: str) -> None:
    if name == DEFAULT_TEMPLATE_NAME:
        raise ValueError("The default template cannot be deleted (you can overwrite or restore it).")
    conn.execute("DELETE FROM email_templates WHERE name = ?", (name,))
    conn.commit()


def ensure_default_template(conn: sqlite3.Connection) -> None:
    if load_template(conn, DEFAULT_TEMPLATE_NAME) is None:
        subject, body = load_default_template()
        save_template(conn, DEFAULT_TEMPLATE_NAME, subject, body)


def restore_default_template(conn: sqlite3.Connection) -> None:
    subject, body = load_default_template()
    save_template(conn, DEFAULT_TEMPLATE_NAME, subject, body)
