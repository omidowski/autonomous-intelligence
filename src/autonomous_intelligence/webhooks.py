"""Outbound webhooks - lets a company receive an HTTP callback when its
content changes state (submitted for review, approved, rejected,
published), the same way Buffer/ContentStudio/Zapier integrations do.

Delivery is best-effort and synchronous: a failing or slow endpoint is
logged and skipped, never retried and never allowed to fail the API
request that triggered it. Payloads are signed with HMAC-SHA256 over the
raw body (`X-AI-Signature: sha256=<hex>`) using the per-webhook secret so
the receiver can verify authenticity.

Only `http`/`https` to a public host is allowed - loopback, private, and
link-local targets are refused at registration and again at send time as a
basic SSRF guard.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from . import db
from .config import Settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 3

EVENT_TYPES = (
    "content.submitted_for_review",
    "content.approved",
    "content.rejected",
    "content.published",
)


class InvalidWebhookURL(ValueError):
    """The URL isn't a plain http(s) URL to a routable public host."""


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise InvalidWebhookURL("Webhook URL must be an absolute http(s) URL.")
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise InvalidWebhookURL(f"Cannot resolve webhook host {host!r}.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise InvalidWebhookURL(
                f"Webhook host {host!r} resolves to a non-public address ({ip})."
            )


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(url: str, secret: str, body: bytes, event_type: str) -> None:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "autonomous-intelligence-webhooks/1",
            "X-AI-Event": event_type,
            "X-AI-Signature": sign(secret, body),
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
        resp.read(1)


def deliver(settings: Settings, company_id: int, event_type: str, data: dict[str, Any]) -> int:
    """POST `{event, data}` to every active webhook of `company_id` that is
    subscribed to `event_type`. Returns how many endpoints accepted it.
    Never raises - transport/HTTP errors are logged and counted as
    failures."""
    hooks = db.list_company_webhooks(settings.database_path, company_id, active_only=True)
    if not hooks:
        return 0
    body = json.dumps({"event": event_type, "data": data}, ensure_ascii=False).encode()
    delivered = 0
    for hook in hooks:
        subscribed = hook["event_types"]
        if subscribed and subscribed != "all" and event_type not in subscribed.split(","):
            continue
        try:
            validate_url(hook["url"])
            _post(hook["url"], hook["secret"], body, event_type)
            delivered += 1
        except (InvalidWebhookURL, urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning(
                "webhook delivery failed: company=%s event=%s url=%s err=%s",
                company_id,
                event_type,
                hook["url"],
                exc,
            )
    return delivered


__all__ = ["EVENT_TYPES", "InvalidWebhookURL", "deliver", "sign", "validate_url"]
