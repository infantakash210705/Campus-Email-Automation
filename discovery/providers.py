"""Discovery providers: turn inputs into normalized candidate colleges.

Pure-Python (stdlib + optional pandas for CSV). No network calls here — the
candidates are later fetched/extracted by the monitoring pipeline. Candidates
are never auto-approved; they enter the dataset as 'discovered'/'pending_review'.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import urlparse


def normalize_host(url: str) -> str:
    """Return the lower-cased host without a leading www., for dedupe."""
    u = (url or "").strip()
    if u and "://" not in u:
        u = "https://" + u
    host = (urlparse(u).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    return u


def college_key(name: str, city: str = "") -> str:
    def norm(s: str) -> str:
        return re.sub(r"[\W_]+", " ", (s or "").casefold()).strip()
    return f"{norm(name)}|{norm(city)}"


@dataclass
class DiscoveryCandidate:
    name: str
    website: str
    website_host: str = ""
    state: str = ""
    city: str = ""
    institution_type: str = ""
    source: str = "url_list"            # url_list | existing_csv | manual
    seed_urls: list = field(default_factory=list)   # pages to check first
    notes: str = ""

    @property
    def key(self) -> str:
        return college_key(self.name, self.city)


def normalize_candidate(cand: DiscoveryCandidate) -> DiscoveryCandidate:
    cand.website = normalize_url(cand.website)
    cand.website_host = normalize_host(cand.website)
    cand.name = " ".join((cand.name or "").split())
    cand.state = " ".join((cand.state or "").split())
    cand.city = " ".join((cand.city or "").split())
    return cand


def _dedupe(cands: Iterable[DiscoveryCandidate]) -> list:
    out: list = []
    seen_host: set = set()
    seen_key: set = set()
    for c in cands:
        c = normalize_candidate(c)
        if not c.website_host:
            continue
        if c.website_host in seen_host or c.key in seen_key:
            continue
        seen_host.add(c.website_host)
        seen_key.add(c.key)
        out.append(c)
    return out


def discover_from_urls(
    entries: Iterable,
    *,
    default_state: str = "",
    default_city: str = "",
    institution_type: str = "",
    limit: Optional[int] = None,
) -> list:
    """Build candidates from supplied official URLs.

    `entries` may be:
      * a plain URL string, or
      * a dict with keys: url (required), name, state, city, institution_type
    The college name defaults to the host if not provided.
    """
    cands: list = []
    for e in entries:
        if isinstance(e, str):
            url, name, state, city, itype = e, "", default_state, default_city, institution_type
        elif isinstance(e, dict):
            url = e.get("url") or e.get("website") or ""
            name = e.get("name") or e.get("college_name") or ""
            state = e.get("state") or default_state
            city = e.get("city") or default_city
            itype = e.get("institution_type") or institution_type
        else:
            continue
        url = normalize_url(url)
        if not url:
            continue
        host = normalize_host(url)
        if not name:
            name = host
        cands.append(DiscoveryCandidate(
            name=name, website=url, website_host=host, state=state, city=city,
            institution_type=itype, source="url_list", seed_urls=[url],
        ))
    result = _dedupe(cands)
    return result[:limit] if limit else result


def discover_from_existing_contacts(
    rows: Iterable[dict],
    *,
    limit: Optional[int] = None,
) -> list:
    """Build candidates from existing contact rows that have a website.

    Each row is a mapping with at least 'college_name' and 'website' (and
    optionally state/city). Rows without a usable website are skipped.
    """
    cands: list = []
    for r in rows:
        website = r.get("website") or ""
        if not normalize_host(website):
            continue
        source_url = r.get("source_url") or website
        cands.append(DiscoveryCandidate(
            name=r.get("college_name") or r.get("name") or "",
            website=website,
            state=r.get("state", ""),
            city=r.get("city", ""),
            source="existing_csv",
            seed_urls=[u for u in {normalize_url(website), normalize_url(source_url)} if u],
        ))
    result = _dedupe(cands)
    return result[:limit] if limit else result
