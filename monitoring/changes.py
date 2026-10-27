"""Change detection: compare a freshly-extracted snapshot to stored values.

Pure functions only (dict/list/str) — no dataclasses, no network, no DB — so
this core logic is fully unit-testable in any environment.

Key safety rule (from the spec): a changed page *hash* must NOT be treated as a
changed email. We always re-extract and compare the actual fields. An uncertain
scraped value never silently overwrites an approved contact — callers record a
pending change for human approval instead.
"""
from __future__ import annotations

from typing import Iterable, Optional

# Possible per-page outcomes (mirrors the check_results CHECK constraint).
OUTCOMES = (
    "no_change", "contact_changed", "new_contact",
    "unavailable", "blocked", "extraction_failed", "contact_missing", "ok",
)


def normalize_email(email: str) -> str:
    """Lower-case + trim for reliable duplicate/equality checks."""
    return (email or "").strip().lower()


def normalize_phone(phone: str) -> str:
    """Keep digits and a leading + for comparison."""
    s = (phone or "").strip()
    out = []
    for i, ch in enumerate(s):
        if ch.isdigit() or (ch == "+" and i == 0):
            out.append(ch)
    return "".join(out)


def diff_contacts(
    stored_emails: Iterable[str],
    extracted_emails: Iterable[str],
) -> dict:
    """Compare two sets of emails and classify what changed.

    Returns {"new": [...], "missing": [...], "unchanged": [...]} using
    normalized (lower-cased) comparison. 'missing' means present before but not
    found now — callers must NOT delete these on a single miss (could be a
    temporary page issue); they are flagged, not removed.
    """
    s = {normalize_email(e) for e in stored_emails if normalize_email(e)}
    x = {normalize_email(e) for e in extracted_emails if normalize_email(e)}
    return {
        "new": sorted(x - s),
        "missing": sorted(s - x),
        "unchanged": sorted(s & x),
    }


def classify_snapshot(
    *,
    fetch_outcome: str,
    stored_emails: Optional[Iterable[str]] = None,
    extracted_emails: Optional[Iterable[str]] = None,
    stored_hash: str = "",
    new_hash: str = "",
) -> dict:
    """Decide the per-page outcome from a fetch result + extraction.

    Returns {"outcome": <one of OUTCOMES>, "diff": {...}|None, "hash_changed": bool}.

    Logic:
      * fetch said blocked/unavailable            -> pass that outcome through
      * extraction yielded nothing but page OK     -> contact_missing
      * emails changed vs stored                   -> new_contact / contact_changed
      * nothing changed                            -> no_change
    The page hash is informational only: it can flag "something on the page
    changed" but never by itself asserts an email changed.
    """
    if fetch_outcome in ("blocked",):
        return {"outcome": "blocked", "diff": None, "hash_changed": False}
    if fetch_outcome in ("unavailable", "not_modified"):
        # not_modified => definitely no change; unavailable => don't touch data
        outcome = "no_change" if fetch_outcome == "not_modified" else "unavailable"
        return {"outcome": outcome, "diff": None, "hash_changed": False}
    if fetch_outcome == "extraction_failed":
        return {"outcome": "extraction_failed", "diff": None, "hash_changed": False}

    stored = list(stored_emails or [])
    extracted = list(extracted_emails or [])
    hash_changed = bool(new_hash) and bool(stored_hash) and new_hash != stored_hash

    if not extracted:
        # page fetched fine but no contact email found
        return {"outcome": "contact_missing", "diff": None, "hash_changed": hash_changed}

    diff = diff_contacts(stored, extracted)
    if not stored:
        # first time we have data for this page
        return {"outcome": "new_contact", "diff": diff, "hash_changed": hash_changed}
    if diff["new"]:
        return {"outcome": "new_contact", "diff": diff, "hash_changed": hash_changed}
    if diff["missing"]:
        # an existing email is no longer visible: flag as a change for review,
        # but never auto-delete on a single observation.
        return {"outcome": "contact_changed", "diff": diff, "hash_changed": hash_changed}
    return {"outcome": "no_change", "diff": diff, "hash_changed": hash_changed}
