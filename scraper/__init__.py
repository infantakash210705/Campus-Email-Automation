"""Web scraping package for Shortfundly college data collection.

Modules
-------
* extract   - pure-Python HTML/text field extraction (no network). Fully unit-testable.
* safety    - URL validation + SSRF guards + robots.txt + per-domain rate limiting.
* fetcher   - HTTP fetching with timeouts, retries/backoff, conditional requests.
"""
