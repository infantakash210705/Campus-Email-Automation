"""College discovery providers.

Per the project's compliance choice, discovery uses ONLY:
  * url_list      - official college website URLs supplied by the user
  * existing_csv  - colleges already present in the contacts CSV / database

No search-engine scraping is performed. A pluggable provider interface is
defined so an API-based provider could be added later behind a credential.
"""
from .providers import (
    DiscoveryCandidate,
    discover_from_existing_contacts,
    discover_from_urls,
    normalize_candidate,
)

__all__ = [
    "DiscoveryCandidate",
    "discover_from_urls",
    "discover_from_existing_contacts",
    "normalize_candidate",
]
