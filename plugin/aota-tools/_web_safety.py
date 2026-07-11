"""SSRF / DNS rebinding safety module for AOTA web fetch tools.

Provides URL validation, IP range checking, and DNS-level protection
against requests to private/internal/loopback addresses.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

MAX_REDIRECTS = 5


class WebFetchError(Exception):
    """Compact, model-facing error for web fetch safety violations."""


# ---------------------------------------------------------------------------
# IP range checks
# ---------------------------------------------------------------------------

_PRIVATE_RANGES: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# Additional ranges that should never be contacted
_ADDITIONAL_FORBIDDEN: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("ff00::/8"),
]


def is_ip_global(addr: str) -> bool:
    """Check whether *addr* is a global (public, non-private) IP address.

    Returns ``True`` if the address is public and routable on the open
    internet.  Returns ``False`` for loopback, private, link-local,
    multicast, reserved, and unspecified addresses.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False

    if ip.is_loopback:
        return False
    if ip.is_private:
        return False
    if ip.is_link_local:
        return False
    if ip.is_multicast:
        return False
    if ip.is_reserved:
        return False
    if ip.is_unspecified:
        return False

    # Also check our extra forbidden ranges
    for net in _ADDITIONAL_FORBIDDEN:
        if ip in net:
            return False

    return True


def validate_ip_global(hostname: str) -> None:
    """Resolve *hostname* and verify ALL A/AAAA records are global IPs.

    Raises ``WebFetchError`` if:
    - DNS resolution fails
    - ANY resolved address is non-global (private / loopback / ...)
    """
    try:
        addrs = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise WebFetchError(f"DNS resolution failed for '{hostname}': {e}") from e

    seen: set[str] = set()
    for fam, _type, _proto, _canon, sockaddr in addrs:
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        if not is_ip_global(ip_str):
            raise WebFetchError(
                f"resolved address {ip_str} for '{hostname}' is not a global (public) IP"
            )


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

def validate_url(url: str) -> str:
    """Validate *url* and return a normalized URL string.

    Rules:
    - Only ``http`` and ``https`` schemes are allowed.
    - URL credentials (``user:pass@host``) are rejected.
    - Hostname is required.
    - Only ports 80 and 443 are allowed.
    """
    if not isinstance(url, str) or not url.strip():
        raise WebFetchError("url is required")

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise WebFetchError(
            f"unsupported scheme '{parsed.scheme}'; only http and https are allowed"
        )

    if parsed.username or parsed.password:
        raise WebFetchError("URL credentials (user:pass@) are not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise WebFetchError("URL must have a hostname")

    port = parsed.port
    if port is not None and port not in (80, 443):
        raise WebFetchError(f"port {port} is not allowed; only ports 80 and 443")

    return url
