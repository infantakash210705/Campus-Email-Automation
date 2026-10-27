"""HTTP fetching with timeouts, retries/backoff, conditional requests and SSRF guards.

`requests` is imported lazily/gracefully so the rest of the app still imports when
the dependency is not installed. Every fetch is validated by scraper.safety first,
honours robots.txt, and applies per-domain rate limiting.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .safety import RateLimiter, RobotsCache, UnsafeURLError, validate_url


@dataclass
class FetchResult:
    url: str
    ok: bool
    outcome: str                      # ok | unavailable | blocked | not_modified | error
    status: Optional[int] = None
    text: str = ""
    etag: str = ""
    last_modified: str = ""
    error_type: str = ""
    message: str = ""
    elapsed: float = 0.0


@dataclass
class Fetcher:
    """Conservative HTTP client for college pages.

    Parameters mirror the Config fields so you can build one with:
        Fetcher.from_config(cfg)
    """
    user_agent: str = "ShortfundlyCampusBot/1.0"
    timeout: int = 20
    retry_count: int = 3
    per_domain_delay: float = 2.0
    respect_robots: bool = True
    max_bytes: int = 2_000_000
    _robots: RobotsCache = field(init=False, repr=False, default=None)  # type: ignore
    _limiter: RateLimiter = field(init=False, repr=False, default=None)  # type: ignore

    def __post_init__(self) -> None:
        self._robots = RobotsCache(self.user_agent)
        self._limiter = RateLimiter(self.per_domain_delay)

    @classmethod
    def from_config(cls, cfg) -> "Fetcher":
        return cls(
            user_agent=getattr(cfg, "user_agent", "ShortfundlyCampusBot/1.0"),
            timeout=getattr(cfg, "request_timeout", 20),
            retry_count=getattr(cfg, "retry_count", 3),
            per_domain_delay=getattr(cfg, "per_domain_delay", 2.0),
        )

    @staticmethod
    def _requests():
        """Import requests lazily; raise a clear error if it's missing."""
        try:
            import requests  # type: ignore
            return requests
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "The 'requests' package is required for live fetching. "
                "Install dependencies: pip install -r requirements.txt"
            ) from exc

    def fetch(self, url: str, *, etag: str = "", last_modified: str = "",
              sleep=time.sleep) -> FetchResult:
        """Fetch a URL safely. Never raises for network issues — returns a FetchResult.

        Uses conditional headers (If-None-Match / If-Modified-Since) when provided,
        so an unchanged page returns outcome='not_modified' cheaply.
        """
        start = time.monotonic()
        # 1) SSRF / scheme / internal-host validation (raises only on unsafe input).
        try:
            validate_url(url)
        except UnsafeURLError as exc:
            return FetchResult(url=url, ok=False, outcome="blocked",
                               error_type="unsafe_url", message=str(exc),
                               elapsed=time.monotonic() - start)
        # 2) robots.txt
        if self.respect_robots and not self._robots.allowed(url):
            return FetchResult(url=url, ok=False, outcome="blocked",
                               error_type="robots_disallow",
                               message="Disallowed by robots.txt",
                               elapsed=time.monotonic() - start)
        # 3) per-domain rate limit
        self._limiter.wait(url)

        requests = self._requests()
        headers = {"User-Agent": self.user_agent, "Accept": "text/html,*/*"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        last_exc_msg = ""
        last_err_type = ""
        for attempt in range(1, self.retry_count + 1):
            try:
                resp = requests.get(url, headers=headers, timeout=self.timeout,
                                    allow_redirects=True, stream=True)
                # Re-validate the final URL after redirects (SSRF via redirect).
                final_url = resp.url
                try:
                    validate_url(final_url)
                except UnsafeURLError as exc:
                    resp.close()
                    return FetchResult(url=url, ok=False, outcome="blocked",
                                       error_type="unsafe_redirect", message=str(exc),
                                       status=resp.status_code,
                                       elapsed=time.monotonic() - start)
                status = resp.status_code
                etag_out = resp.headers.get("ETag", "")
                lastmod_out = resp.headers.get("Last-Modified", "")
                if status == 304:
                    resp.close()
                    return FetchResult(url=final_url, ok=True, outcome="not_modified",
                                       status=304, etag=etag_out or etag,
                                       last_modified=lastmod_out or last_modified,
                                       elapsed=time.monotonic() - start)
                if status in (403, 401, 429):
                    resp.close()
                    return FetchResult(url=final_url, ok=False, outcome="blocked",
                                       status=status, error_type="http_blocked",
                                       message=f"HTTP {status}",
                                       elapsed=time.monotonic() - start)
                if 500 <= status < 600:
                    last_err_type = "http_server_error"
                    last_exc_msg = f"HTTP {status}"
                    resp.close()
                    raise _Retry()
                if status >= 400:
                    resp.close()
                    return FetchResult(url=final_url, ok=False, outcome="unavailable",
                                       status=status, error_type="http_client_error",
                                       message=f"HTTP {status}",
                                       elapsed=time.monotonic() - start)
                # 2xx: read a bounded amount of text
                ctype = resp.headers.get("Content-Type", "")
                if "html" not in ctype and "text" not in ctype and ctype:
                    resp.close()
                    return FetchResult(url=final_url, ok=False, outcome="unavailable",
                                       status=status, error_type="non_html",
                                       message=f"Non-HTML content-type: {ctype}",
                                       elapsed=time.monotonic() - start)
                content = resp.content[: self.max_bytes]
                resp.close()
                try:
                    text = content.decode(resp.encoding or "utf-8", "replace")
                except Exception:
                    text = content.decode("utf-8", "replace")
                return FetchResult(url=final_url, ok=True, outcome="ok", status=status,
                                   text=text, etag=etag_out, last_modified=lastmod_out,
                                   elapsed=time.monotonic() - start)
            except _Retry:
                pass
            except Exception as exc:
                last_err_type = type(exc).__name__
                last_exc_msg = str(exc)
            # exponential backoff before the next attempt
            if attempt < self.retry_count:
                sleep(min(2 ** attempt, 30) * 0.5)
        return FetchResult(url=url, ok=False, outcome="unavailable",
                           error_type=last_err_type or "fetch_failed",
                           message=last_exc_msg or "All attempts failed",
                           elapsed=time.monotonic() - start)


class _Retry(Exception):
    """Internal signal to trigger a backoff retry."""
