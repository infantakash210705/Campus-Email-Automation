"""Pure-Python extraction of contact fields from HTML/text.

No network access and no third-party dependencies — safe to unit-test with
synthetic HTML fixtures. BeautifulSoup is used *optionally* for nicer link
extraction; if it is not installed, a regex fallback is used so the module
still imports and works.
"""
from __future__ import annotations

import hashlib
import re
from html import unescape
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

try:  # optional dependency
    from bs4 import BeautifulSoup  # type: ignore
    _HAVE_BS4 = True
except Exception:  # pragma: no cover - exercised only when bs4 is missing
    _HAVE_BS4 = False

# Email: conservative, avoids trailing punctuation. Lower-cased by caller.
EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}"
)
# (de-obfuscation handled by _deobfuscate via literal token replacement)
# Indian / international phone numbers (loose; validated/trimmed afterwards).
PHONE_RE = re.compile(
    r"(?:(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{2,5}\)?[\s\-.]?)?\d{3,4}[\s\-.]?\d{3,4})"
)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)

# Mailbox providers that can never be an "official college email".
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "outlook.com",
    "hotmail.com", "live.com", "rediffmail.com", "icloud.com", "proton.me", "protonmail.com",
    "aol.com", "mail.com", "zoho.com", "ymail.com",
}

CONTACT_KEYWORDS = ("contact", "reach-us", "reach_us", "reachus", "get-in-touch")
ADMISSION_KEYWORDS = ("admission", "admissions", "apply", "enquiry", "enquiries")
DEPARTMENT_KEYWORDS = ("department", "departments", "faculty", "academics")


def content_hash(text: str) -> str:
    """Stable SHA-256 of normalised text (whitespace-collapsed)."""
    norm = " ".join((text or "").split())
    return hashlib.sha256(norm.encode("utf-8", "ignore")).hexdigest()


def strip_html(html: str) -> str:
    """Return visible text from HTML (drops script/style). No dependency needed."""
    if not html:
        return ""
    no_sc = SCRIPT_STYLE_RE.sub(" ", html)
    text = TAG_RE.sub(" ", no_sc)
    return unescape(" ".join(text.split()))


def _deobfuscate(text: str) -> str:
    """Turn 'a [at] b [dot] com' into 'a@b.com' so EMAIL_RE can catch it.

    Uses literal token replacement (handles any number of '(dot)' segments).
    """
    t = text or ""
    # Replace bracketed/spaced (at)/(dot) tokens with @ and . respectively.
    t = re.sub(r"[\[(]?\s*(?:at|AT)\s*[\])]?", "@", t)
    t = re.sub(r"[\[(]?\s*(?:dot|DOT)\s*[\])]?", ".", t)
    # collapse accidental spaces introduced around @ and .
    t = re.sub(r"\s*@\s*", "@", t)
    t = re.sub(r"\s*\.\s*", ".", t)
    return t


def extract_emails(html_or_text: str, *, include_free: bool = True) -> list[str]:
    """Return a de-duplicated, lower-cased list of emails found in the content."""
    text = _deobfuscate(strip_html(html_or_text))
    found: list[str] = []
    seen: set[str] = set()
    for raw in EMAIL_RE.findall(text):
        email = raw.lower().strip(".")
        if email in seen:
            continue
        # drop obvious asset filenames caught by accident (e.g. image@2x.png)
        if email.rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js"):
            continue
        domain = email.rpartition("@")[2]
        if not include_free and domain in FREE_EMAIL_DOMAINS:
            continue
        seen.add(email)
        found.append(email)
    return found


def extract_phones(html_or_text: str) -> list[str]:
    """Return plausible phone numbers (7-15 digits once separators removed)."""
    text = strip_html(html_or_text)
    out: list[str] = []
    seen: set[str] = set()
    for raw in PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", raw)
        if not (7 <= len(digits) <= 15):
            continue
        cleaned = raw.strip()
        if digits in seen:
            continue
        seen.add(digits)
        out.append(cleaned)
    return out


def _links_bs4(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        out.append((a.get_text(" ", strip=True).lower(), urljoin(base_url, a["href"])))
    return out


def _links_regex(html: str, base_url: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html or "", re.I | re.S):
        href, inner = m.group(1), strip_html(m.group(2)).lower()
        out.append((inner, urljoin(base_url, href)))
    return out


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Return [(anchor_text_lower, absolute_url)] using bs4 if available."""
    if not html:
        return []
    return _links_bs4(html, base_url) if _HAVE_BS4 else _links_regex(html, base_url)


def classify_link(anchor_text: str, url: str) -> Optional[str]:
    """Return 'contact' | 'admissions' | 'department' if the link looks relevant."""
    hay = f"{anchor_text} {url}".lower()
    if any(k in hay for k in CONTACT_KEYWORDS):
        return "contact"
    if any(k in hay for k in ADMISSION_KEYWORDS):
        return "admissions"
    if any(k in hay for k in DEPARTMENT_KEYWORDS):
        return "department"
    return None


def find_relevant_pages(html: str, base_url: str, *, same_host_only: bool = True) -> list[dict]:
    """Discover contact/admissions/department pages linked from a page."""
    base_host = (urlparse(base_url).hostname or "").lower()
    out: list[dict] = []
    seen: set[str] = set()
    for text, url in extract_links(html, base_url):
        kind = classify_link(text, url)
        if not kind:
            continue
        host = (urlparse(url).hostname or "").lower()
        if same_host_only and host and base_host and host != base_host \
                and not host.endswith("." + base_host) and not base_host.endswith("." + host):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "page_type": kind, "anchor": text})
    return out


def extract_contacts(html: str, *, page_url: str = "", include_free: bool = True) -> dict:
    """Extract a structured contact snapshot from a single page.

    Returns a dict with emails, phones, a content hash and the source url.
    Pure function — safe to call on fixtures.
    """
    emails = extract_emails(html, include_free=include_free)
    phones = extract_phones(html)
    return {
        "source_url": page_url,
        "emails": emails,
        "phones": phones,
        "content_hash": content_hash(strip_html(html)),
    }
