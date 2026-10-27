"""URL safety, SSRF protection, robots.txt handling and per-domain rate limiting.

Pure stdlib. `requests` is only needed by the fetcher, not here, so these guards
are fully unit-testable offline.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from typing import Optional
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")
# Hostnames we never fetch regardless of DNS.
BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost")
BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}
# Cloud metadata / link-local endpoints.
BLOCKED_LITERAL_IPS = {"169.254.169.254", "100.100.100.200"}


class UnsafeURLError(ValueError):
    """Raised when a URL is not safe to fetch (SSRF / scheme / internal host)."""


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def validate_url(url: str, *, resolve_dns: bool = True) -> str:
    """Return the URL if safe to fetch, else raise UnsafeURLError.

    Blocks non-http(s) schemes, missing hosts, internal/private/loopback
    addresses, cloud-metadata IPs, and (optionally) hostnames that resolve to
    private IPs — mitigating SSRF via DNS.
    """
    if not url or not isinstance(url, str):
        raise UnsafeURLError("Empty or non-string URL")
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Scheme not allowed: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeURLError("URL has no host")
    if host in BLOCKED_HOSTS or any(host.endswith(s) for s in BLOCKED_HOST_SUFFIXES):
        raise UnsafeURLError(f"Blocked host: {host}")
    # If the host is a literal IP, check it directly.
    host_is_ip = True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        host_is_ip = False
    if host_is_ip:
        if host in BLOCKED_LITERAL_IPS or _is_private_ip(host):
            raise UnsafeURLError(f"Blocked IP host: {host}")
        return url
    if resolve_dns:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise UnsafeURLError(f"DNS resolution failed for {host}: {exc}") from exc
        for info in infos:
            ip = info[4][0]
            if ip in BLOCKED_LITERAL_IPS or _is_private_ip(ip):
                raise UnsafeURLError(f"{host} resolves to a blocked address {ip}")
    return url


def is_safe_url(url: str, *, resolve_dns: bool = True) -> bool:
    """Boolean convenience wrapper around validate_url."""
    try:
        validate_url(url, resolve_dns=resolve_dns)
        return True
    except UnsafeURLError:
        return False


class RobotsCache:
    """Caches and evaluates robots.txt per host (fail-open on fetch error).

    robotparser is imported lazily so this module still loads in restricted
    environments; if it cannot be imported, robots checks fail open (allowed).
    """

    def __init__(self, user_agent: str = "*"):
        self.user_agent = user_agent
        self._cache: dict = {}

    def allowed(self, url: str) -> bool:
        try:
            from urllib import robotparser  # lazy import
        except Exception:
            return True  # robotparser unavailable -> do not block
        parsed = urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._cache.get(host_key, "missing")
        if rp == "missing":
            rp = robotparser.RobotFileParser()
            rp.set_url(f"{host_key}/robots.txt")
            try:
                rp.read()
            except Exception:
                rp = None  # could not read robots -> fail open (allowed)
            self._cache[host_key] = rp
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return True


class RateLimiter:
    """Simple per-host minimum-delay limiter (monotonic clock)."""

    def __init__(self, per_domain_delay: float = 2.0, clock=time.monotonic, sleep=time.sleep):
        self.delay = max(0.0, per_domain_delay)
        self._last: dict[str, float] = {}
        self._clock = clock
        self._sleep = sleep

    def wait(self, url: str) -> float:
        """Block until the per-host delay has elapsed. Returns seconds slept."""
        host = (urlparse(url).hostname or "").lower()
        now = self._clock()
        last = self._last.get(host)
        slept = 0.0
        if last is not None:
            remaining = self.delay - (now - last)
            if remaining > 0:
                self._sleep(remaining)
                slept = remaining
        self._last[host] = self._clock()
        return slept
