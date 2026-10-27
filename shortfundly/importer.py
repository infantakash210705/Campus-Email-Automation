"""CSV / Excel import with a full preview step before anything is saved."""
from __future__ import annotations

import io
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .contacts import insert_contact
from .database import utcnow
from .validation import (
    CONTACT_FIELDS, REQUIRED_FIELDS, clean, clean_record, college_key, validate_contact,
)

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20000

COLUMN_ALIASES = {
    "state": "state", "state_name": "state",
    "city": "city", "town": "city", "city_town": "city", "district": "city",
    "college_name": "college_name", "college": "college_name", "institution": "college_name",
    "institution_name": "college_name", "name_of_college": "college_name",
    "website": "website", "official_website": "website", "college_website": "website",
    "website_url": "website", "url": "website",
    "contact_person": "contact_person", "contact_name": "contact_person",
    "contact_person_name": "contact_person", "recipient_name": "contact_person",
    "person": "contact_person",
    "department": "department", "dept": "department", "department_name": "department",
    "email": "email", "official_email": "email", "email_address": "email",
    "official_email_address": "email", "email_id": "email", "contact_email": "email",
    "source_url": "source_url", "source_page_url": "source_url", "source_page": "source_url",
    "source": "source_url", "source_link": "source_url",
}


class ImportFormatError(ValueError):
    """The uploaded file cannot be read as a contacts table."""


def normalize_header(header: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(header).strip().lower()).strip("_")


def read_table(data: bytes, filename: str) -> pd.DataFrame:
    if len(data) > MAX_BYTES:
        raise ImportFormatError(f"File is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    name = filename.lower()
    try:
        if name.endswith(".csv"):
            for enc in ("utf-8-sig", "cp1252"):
                try:
                    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:  # pragma: no cover - cp1252 decodes almost anything
                raise ImportFormatError("Could not decode the CSV file (try saving it as UTF-8).")
        elif name.endswith((".xlsx", ".xlsm")):
            df = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False, engine="openpyxl")
        elif name.endswith(".xls"):
            raise ImportFormatError("Legacy .xls files are not supported. Save as .xlsx or .csv.")
        else:
            raise ImportFormatError("Unsupported file type. Upload a .csv or .xlsx file.")
    except ImportFormatError:
        raise
    except Exception as exc:  # malformed files raise many different pandas/openpyxl errors
        raise ImportFormatError(f"Could not read the file: {type(exc).__name__}: {exc}") from exc
    if len(df) > MAX_ROWS:
        raise ImportFormatError(f"File has more than {MAX_ROWS} rows; split it into smaller files.")
    return df


def map_columns(columns) -> tuple[dict, list[str]]:
    """Return ({source column: canonical field}, [ignored columns])."""
    mapping: dict = {}
    ignored: list[str] = []
    used: set = set()
    for col in columns:
        canon = COLUMN_ALIASES.get(normalize_header(col))
        if canon and canon not in used:
            mapping[col] = canon
            used.add(canon)
        else:
            ignored.append(str(col))
    return mapping, ignored


@dataclass
class PreviewRow:
    row_number: int            # spreadsheet row (header = row 1)
    record: dict
    status: str                # "ok" | "invalid" | "duplicate"
    messages: list = field(default_factory=list)
    duplicate_college: bool = False


@dataclass
class ImportPreview:
    filename: str
    missing_columns: list = field(default_factory=list)
    ignored_columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)

    @property
    def ok_rows(self) -> list:
        return [r for r in self.rows if r.status == "ok"]

    @property
    def invalid_count(self) -> int:
        return sum(r.status == "invalid" for r in self.rows)

    @property
    def duplicate_count(self) -> int:
        return sum(r.status == "duplicate" for r in self.rows)

    @property
    def duplicate_college_count(self) -> int:
        return sum(r.duplicate_college for r in self.rows)


@dataclass
class ImportResult:
    batch_id: str
    inserted: int = 0
    invalid: int = 0
    duplicates: int = 0
    skipped_colleges: int = 0


def build_preview(conn: sqlite3.Connection, df: pd.DataFrame, filename: str = "upload") -> ImportPreview:
    """Analyse a table WITHOUT writing anything to the database."""
    mapping, ignored = map_columns(df.columns)
    missing = [f for f in REQUIRED_FIELDS if f not in mapping.values()]
    preview = ImportPreview(filename=filename, missing_columns=missing, ignored_columns=ignored)
    if missing:
        return preview

    existing_emails = {r[0] for r in conn.execute("SELECT email FROM contacts")}
    existing_colleges = {r[0] for r in conn.execute("SELECT DISTINCT college_key FROM contacts")}
    seen_emails: dict = {}
    seen_colleges: set = set()

    for idx, (_, raw) in enumerate(df.iterrows()):
        raw_rec = {canon: raw[src] for src, canon in mapping.items()}
        if not any(clean(v) for v in raw_rec.values()):
            continue  # fully blank row
        rec = clean_record(raw_rec)
        row_no = idx + 2
        errors = validate_contact(rec)
        row = PreviewRow(row_number=row_no, record=rec, status="ok")
        if errors:
            row.status, row.messages = "invalid", errors
        elif rec["email"] in existing_emails:
            row.status, row.messages = "duplicate", ["Email already exists in the database"]
        elif rec["email"] in seen_emails:
            row.status = "duplicate"
            row.messages = [f"Same email as row {seen_emails[rec['email']]} in this file"]
        else:
            seen_emails[rec["email"]] = row_no
            key = college_key(rec["college_name"], rec["city"])
            if key in existing_colleges or key in seen_colleges:
                row.duplicate_college = True
                row.messages = ["College already present (different contact email)"]
            seen_colleges.add(key)
        preview.rows.append(row)
    return preview


def preview_dataframe(preview: ImportPreview) -> pd.DataFrame:
    rows = []
    for r in preview.rows:
        rows.append({
            "row": r.row_number,
            "status": r.status + (" (college exists)" if r.duplicate_college else ""),
            "problems": "; ".join(r.messages),
            **{f: r.record.get(f, "") for f in CONTACT_FIELDS},
        })
    return pd.DataFrame(rows)


def _reject(conn, batch_id, filename, row: PreviewRow, code: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO import_rejections (batch_id, source_file, row_number, reason_code, detail, "
        "raw_json, created_at) VALUES (?,?,?,?,?,?,?)",
        (batch_id, filename, row.row_number, code, detail, json.dumps(row.record), utcnow()),
    )


def commit_import(
    conn: sqlite3.Connection, preview: ImportPreview, skip_existing_colleges: bool = False
) -> ImportResult:
    """Save the importable rows. Invalid/duplicate rows are logged, never saved."""
    if preview.missing_columns:
        raise ImportFormatError("Cannot import: missing columns " + ", ".join(preview.missing_columns))
    result = ImportResult(batch_id=uuid.uuid4().hex[:12])
    try:
        for row in preview.rows:
            if row.status == "invalid":
                _reject(conn, result.batch_id, preview.filename, row, "invalid", "; ".join(row.messages))
                result.invalid += 1
            elif row.status == "duplicate":
                _reject(conn, result.batch_id, preview.filename, row, "duplicate", "; ".join(row.messages))
                result.duplicates += 1
            elif skip_existing_colleges and row.duplicate_college:
                _reject(conn, result.batch_id, preview.filename, row, "duplicate",
                        "College already present; skipped by option")
                result.duplicates += 1
                result.skipped_colleges += 1
            else:
                try:
                    with conn:  # savepoint-like: a failed insert must not abort the batch
                        insert_contact(conn, row.record)
                    result.inserted += 1
                except sqlite3.IntegrityError:
                    _reject(conn, result.batch_id, preview.filename, row, "duplicate",
                            "Email was added after the preview was built")
                    result.duplicates += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result
