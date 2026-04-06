"""SSRF-safe checks for outbound webhook HTTPS URLs (DNS + resolved IPs)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class WebhookUrlUnsafeError(ValueError):
    """HTTPS URL host resolves to a non-public address or failed resolution."""


def _ip_is_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast,
    )


def assert_webhook_target_ips_safe_for_url(url: str) -> None:
    """Resolve hostname and ensure no A/AAAA record maps to a forbidden address."""
    trimmed = url.strip()
    parsed = urlparse(trimmed)
    if parsed.scheme != "https":
        msg = "url must use HTTPS"
        raise WebhookUrlUnsafeError(msg)
    host = parsed.hostname
    if not host:
        msg = "url must include a host"
        raise WebhookUrlUnsafeError(msg)

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        msg = f"url host could not be resolved: {exc}"
        raise WebhookUrlUnsafeError(msg) from exc

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _ip_is_blocked(addr):
            msg = "url resolves to a non-public address"
            raise WebhookUrlUnsafeError(msg)
