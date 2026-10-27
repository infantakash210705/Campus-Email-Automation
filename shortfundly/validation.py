"""Validation and normalisation helpers for contact records."""
from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse

CONTACT_FIELDS = (
    "state", "city", "college_name", "website",
    "contact_person", "department", "email", "source_url",
)
REQUIRED_FIELDS = ("state", "city", "college_name", "email")
FIELD_LABELS = {
    "state": "State", "city": "City", "college_name": "College name",
    "website": "Official website", "contact_person": "Contact person",
    "department": "Department", "email": "Official email", "source_url": "Source page URL",
}

EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)

# Mailbox providers that can never prove "official college email".
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "outlook.com",
    "hotmail.com", "live.com", "rediffmail.com", "icloud.com", "proton.me", "protonmail.com",
    "aol.com", "mail.com", "zoho.com", "ymail.com",
}
# Shared suffixes that must not count as "the same organisation".
PUBLIC_SUFFIXES = {
    "in", "com", "org", "net", "edu", "gov", "ac.in", "edu.in", "co.in", "org.in", "gov.in",
    "nic.in", "res.in", "net.in", "ac.uk", "co.uk", "org.uk", "edu.au", "com.au",
}


def clean(value: Any) -> str:
    """Return a stripped, whitespace-collapsed string ('' for None/NaN)."""
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    return " ".join(str(value).split())


def validate_email(email: Any) -> bool:
    e = clean(email)
    if not e or len(e) > 254 or " " in e or not EMAIL_RE.match(e):
        return False
    local = e.rpartition("@")[0]
    return not (len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local)


def normalize_url(value: Any) -> str:
    v = clean(value)
    if not v:
        return ""
    if "://" not in v and not v.lower().startswith(("mailto:", "javascript:", "data:", "file:")):
        v = "https://" + v
    return v


def validate_url(value: Any) -> bool:
    v = clean(value)
    if not v or " " in v:
        return False
    parsed = urlparse(v)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname) and "." in parsed.hostname


def host_of(url: Any) -> str:
    host = (urlparse(normalize_url(url)).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def email_domain(email: Any) -> str:
    return clean(email).lower().rpartition("@")[2]


def domains_related(a: str, b: str) -> bool:
    """True when two hostnames belong to the same organisation (heuristic)."""
    a, b = a.lower().strip("."), b.lower().strip(".")
    if not a or not b:
        return False
    if a == b:
        return True
    if a.endswith("." + b) and b not in PUBLIC_SUFFIXES and "." in b:
        return True
    if b.endswith("." + a) and a not in PUBLIC_SUFFIXES and "." in a:
        return True
    return False


def email_matches_website(email: Any, website: Any) -> bool:
    domain = email_domain(email)
    if not domain or domain in FREE_EMAIL_DOMAINS:
        return False
    return domains_related(domain, host_of(website))


def college_key(name: Any, city: Any) -> str:
    def norm(s: Any) -> str:
        return re.sub(r"[\W_]+", " ", clean(s).casefold()).strip()
    return f"{norm(name)}|{norm(city)}"


def clean_record(raw: Mapping[str, Any]) -> dict:
    rec = {f: clean(raw.get(f, "")) for f in CONTACT_FIELDS}
    rec["email"] = rec["email"].lower()
    rec["website"] = normalize_url(rec["website"])
    rec["source_url"] = normalize_url(rec["source_url"])
    return rec


def validate_contact(rec: Mapping[str, Any]) -> list[str]:
    """Return a list of human-readable problems (empty list = valid)."""
    errors: list[str] = []
    for f in REQUIRED_FIELDS:
        if not clean(rec.get(f)):
            errors.append(f"Missing required field: {FIELD_LABELS[f]}")
    email = clean(rec.get("email"))
    if email and not validate_email(email):
        errors.append(f"Invalid email format: {email}")
    for f in ("website", "source_url"):
        v = clean(rec.get(f))
        if v and not validate_url(v):
            errors.append(f"Invalid URL in {FIELD_LABELS[f]}: {v}")
    return errors
