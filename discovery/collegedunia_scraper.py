"""CollegeDunia India colleges scraper.

Fetches the list of colleges from CollegeDunia for ANY state in India
and resolves each college's official website URL, producing a list of dicts
suitable for writing to data/seed_colleges.csv or directly importing via auto_discovery.

Supported states include:
    Karnataka, Tamil Nadu, Maharashtra, Kerala, Delhi NCR, Uttar Pradesh,
    Andhra Pradesh, Telangana, Gujarat, Rajasthan, West Bengal, Punjab,
    Haryana, Madhya Pradesh, Bihar, Odisha, Assam, Goa, Uttarakhand,
    Jharkhand, Chhattisgarh, Himachal Pradesh, Jammu and Kashmir, Chandigarh,
    Puducherry, and all other Indian states and union territories.

Usage (standalone):
    python -m discovery.collegedunia_scraper --state "Tamil Nadu" --limit 10

Usage (as module):
    from discovery.collegedunia_scraper import scrape_colleges, scrape_karnataka_colleges
    colleges = scrape_colleges(state="Tamil Nadu", limit=50)  # any state
    karnataka = scrape_karnataka_colleges(limit=50)            # backward-compatible
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse
import urllib.request
import urllib.error

log = logging.getLogger(__name__)

BASE_URL = "https://collegedunia.com"

# Standard list of Indian States and Union Territories
INDIAN_STATES = [
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chandigarh",
    "Chhattisgarh",
    "Delhi NCR",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jammu and Kashmir",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Puducherry",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
]

# Map friendly state names to CollegeDunia URL slugs
STATE_SLUG_MAP = {
    "andhra pradesh": "andhra-pradesh",
    "arunachal pradesh": "arunachal-pradesh",
    "assam": "assam",
    "bihar": "bihar",
    "chandigarh": "chandigarh",
    "chhattisgarh": "chhattisgarh",
    "delhi": "delhi-ncr",
    "delhi ncr": "delhi-ncr",
    "new delhi": "delhi-ncr",
    "goa": "goa",
    "gujarat": "gujarat",
    "haryana": "haryana",
    "himachal pradesh": "himachal-pradesh",
    "jammu and kashmir": "jammu-and-kashmir",
    "jammu & kashmir": "jammu-and-kashmir",
    "jharkhand": "jharkhand",
    "karnataka": "karnataka",
    "kerala": "kerala",
    "madhya pradesh": "madhya-pradesh",
    "maharashtra": "maharashtra",
    "manipur": "manipur",
    "meghalaya": "meghalaya",
    "mizoram": "mizoram",
    "nagaland": "nagaland",
    "odisha": "odisha",
    "orissa": "odisha",
    "puducherry": "puducherry",
    "pondicherry": "puducherry",
    "punjab": "punjab",
    "rajasthan": "rajasthan",
    "sikkim": "sikkim",
    "tamil nadu": "tamil-nadu",
    "tamilnadu": "tamil-nadu",
    "telangana": "telangana",
    "tripura": "tripura",
    "uttar pradesh": "uttar-pradesh",
    "uttarakhand": "uttarakhand",
    "west bengal": "west-bengal",
}

# Headers to mimic a real browser request
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}


def get_state_slug(state: str) -> str:
    """Convert any state name into the CollegeDunia URL slug."""
    raw = (state or "").strip().lower()
    if raw.endswith("-colleges"):
        raw = raw[:-9]
    if raw in STATE_SLUG_MAP:
        return STATE_SLUG_MAP[raw]
    # Check if a mapped slug matches directly
    for slug in STATE_SLUG_MAP.values():
        if raw == slug:
            return slug
    # Fallback: slugify input
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug or "karnataka"


def get_state_listing_url(state: str) -> str:
    """Return the CollegeDunia listing URL for the given state."""
    slug = get_state_slug(state)
    return f"{BASE_URL}/{slug}-colleges"


def _fetch(url: str, timeout: int = 15) -> str:
    """Fetch a URL and return the HTML as a string."""
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = "utf-8"
            ct = resp.headers.get("Content-Type", "")
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].strip()
            return resp.read().decode(charset, errors="replace")
    except urllib.error.URLError as exc:
        log.warning("Fetch failed for %s: %s", url, exc)
        return ""


def _extract_json_ld(html: str) -> list:
    """Extract all JSON-LD blocks from an HTML page."""
    blocks = []
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            blocks.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass
    return blocks


def _get_college_urls_from_listing(html: str) -> list:
    """Extract college page URLs from the JSON-LD ItemList on the listing page."""
    urls = []
    for block in _extract_json_ld(html):
        if block.get("@type") == "ItemList":
            for item in block.get("itemListElement", []):
                url = item.get("url", "").strip()
                if url and "collegedunia.com" in url:
                    urls.append(url)
    return urls


def _slug_to_name(slug: str) -> str:
    """Convert a URL slug like '25621-national-law-school-of-india-university-nlsiu-bangalore'
    into a readable college name, stripping the leading ID number."""
    slug = re.sub(r"^\d+-", "", slug)
    return slug.replace("-", " ").title()


def _extract_college_info(college_url: str, html: str, default_state: str = "Karnataka") -> dict:
    """Extract official website, city, state and institution type from a college page."""
    result = {
        "name": "",
        "website": "",
        "city": "",
        "state": default_state,
        "institution_type": "",
        "source_url": college_url,
    }

    # Extract name from URL slug as initial fallback
    path = urlparse(college_url).path
    slug = path.strip("/").split("/")[-1]
    result["name"] = _slug_to_name(slug)

    # Try JSON-LD first (most reliable)
    for block in _extract_json_ld(html):
        btype = block.get("@type", "")

        if btype in ("CollegeOrUniversity", "EducationalOrganization", "Organization", "University"):
            name = block.get("name", "")
            if name:
                result["name"] = name
            website = block.get("url", "") or block.get("sameAs", "")
            if isinstance(website, list):
                website = next((u for u in website if "collegedunia" not in u), "")
            if website and "collegedunia" not in str(website):
                result["website"] = str(website).strip()
            address = block.get("address", {})
            if isinstance(address, dict):
                result["city"] = address.get("addressLocality", "")
                region = address.get("addressRegion", "").strip()
                if region:
                    result["state"] = region

    # If no website found from JSON-LD, try canonical link tag
    if not result["website"]:
        canonical_match = re.search(
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', html, re.IGNORECASE
        )
        if canonical_match:
            url_val = canonical_match.group(1)
            if "collegedunia" not in url_val:
                result["website"] = url_val

    # Infer institution type
    itype = ""
    name_lower = result["name"].lower()
    url_lower = college_url.lower()
    if "university" in url_lower or "university" in name_lower:
        itype = "University"
    elif any(k in name_lower for k in ["engineering", "technology", "technical", "polytechnic"]):
        itype = "Engineering"
    elif any(k in name_lower for k in ["medical", "medicine", "nursing", "dental", "pharmacy"]):
        itype = "Medical"
    elif any(k in name_lower for k in ["law", "legal"]):
        itype = "Law"
    elif any(k in name_lower for k in ["management", "business", "mba", "commerce"]):
        itype = "Management"
    elif any(k in name_lower for k in ["arts", "science", "college"]):
        itype = "College"
    result["institution_type"] = itype

    return result


def _extract_name_from_html(html: str, fallback: str) -> str:
    """Try to get the clean college name from og:title or page title."""
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        title = re.split(r"\s*[:\-|]\s*(Collegedunia|Fees|Admission|Review|Courses|Cutoff|Ranking)", title, flags=re.IGNORECASE)[0].strip()
        if title and len(title) > 3:
            return title

    m2 = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m2:
        title = m2.group(1).strip()
        title = re.split(r"\s*[:\-|]\s*(Collegedunia|Fees|Admission|Review|Courses|Cutoff|Ranking)", title, flags=re.IGNORECASE)[0].strip()
        if title and len(title) > 3:
            return title

    return fallback


def _find_official_website_in_html(html: str) -> str:
    """Last-resort: scan HTML for an 'Official Website' link."""
    pattern = re.compile(
        r'[Oo]fficial\s+[Ww]ebsite.*?href=["\']([^"\']+)["\']|'
        r'href=["\']([^"\']+)["\'][^>]*>.*?[Oo]fficial\s+[Ww]ebsite',
        re.DOTALL,
    )
    for m in pattern.finditer(html):
        url = m.group(1) or m.group(2) or ""
        url = url.strip()
        if url and url.startswith("http") and "collegedunia" not in url:
            return url
    return ""


def scrape_colleges(
    state: str = "Karnataka",
    limit: Optional[int] = None,
    delay: float = 1.0,
    verbose: bool = True,
) -> list:
    """Scrape CollegeDunia for colleges in any Indian state.

    Returns a list of dicts with keys:
        name, website, state, city, institution_type, source_url

    Only colleges where an official website could be resolved are returned.

    Args:
        state: Name of the Indian state (e.g. 'Karnataka', 'Tamil Nadu', 'Maharashtra', etc.).
        limit: Maximum number of colleges to process (None = all available on listing).
        delay: Seconds to wait between requests to avoid rate limiting.
        verbose: Print progress to stdout.
    """
    listing_url = get_state_listing_url(state)
    state_display = state.strip().title()

    if verbose:
        print(f"[CollegeDunia] Target State: {state_display}")
        print(f"[CollegeDunia] Fetching listing: {listing_url}")

    html = _fetch(listing_url)
    if not html:
        log.error("Failed to fetch CollegeDunia listing page for %s: %s", state_display, listing_url)
        return []

    college_urls = _get_college_urls_from_listing(html)
    if not college_urls:
        log.error("No college URLs found in JSON-LD for %s -- page structure may have changed", state_display)
        return []

    total_found = len(college_urls)
    if verbose:
        print("[OK] Found %d college URLs in JSON-LD for %s" % (total_found, state_display))

    if limit:
        college_urls = college_urls[:limit]
        if verbose:
            print("[INFO] Processing first %d colleges (limit=%d)" % (len(college_urls), limit))

    results = []
    for i, url in enumerate(college_urls, start=1):
        if verbose and (i % 10 == 0 or i == 1):
            print("  [%d/%d] Fetching: %s" % (i, len(college_urls), url))

        page_html = _fetch(url)
        if not page_html:
            log.warning("Empty response for %s -- skipping", url)
            time.sleep(delay)
            continue

        info = _extract_college_info(url, page_html, default_state=state_display)

        # Improve name from og:title / <title> if JSON-LD name was just the slug
        fallback_name = info["name"]
        better_name = _extract_name_from_html(page_html, fallback_name)
        if better_name != fallback_name:
            info["name"] = better_name

        # Last resort: look for official website link in raw HTML
        if not info["website"]:
            info["website"] = _find_official_website_in_html(page_html)

        if info["website"]:
            results.append(info)
            if verbose:
                print("    [+] %s -> %s" % (info["name"], info["website"]))
        else:
            log.debug("No official website found for %s", url)
            if verbose:
                print("    [-] %s - no website found" % info["name"])

        time.sleep(delay)

    if verbose:
        print(f"\n[DONE] {len(results)} colleges with websites out of {len(college_urls)} processed for {state_display}")

    return results


def scrape_karnataka_colleges(
    limit: Optional[int] = None,
    delay: float = 1.0,
    verbose: bool = True,
) -> list:
    """Backward-compatible alias for scrape_colleges(state='Karnataka', ...)."""
    return scrape_colleges(state="Karnataka", limit=limit, delay=delay, verbose=verbose)


if __name__ == "__main__":
    import argparse
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Scrape colleges for any Indian state from CollegeDunia")
    parser.add_argument("--state", default="Karnataka", help="State name (e.g., 'Tamil Nadu', 'Maharashtra', 'Karnataka')")
    parser.add_argument("--limit", type=int, default=5, help="Number of colleges to scrape (default: 5)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds")
    args = parser.parse_args()

    colleges = scrape_colleges(state=args.state, limit=args.limit, delay=args.delay, verbose=True)
    for c in colleges:
        print("  %s | %s | %s | %s" % (c["name"], c["website"], c["city"], c["state"]))
