"""Programmatic API keys - let a company drive the product from its own
scripts/automations instead of a browser session.

A key is shown exactly once, at creation, and only its SHA-256 is stored.
Unlike user passwords (`auth.hash_password`, PBKDF2 with 200k iterations), a
plain fast hash is the right choice here: the key is 32 bytes of
`secrets.token_urlsafe` entropy, so there is no dictionary to attack and
stretching would only add latency to every authenticated request.

Keys authenticate as `member`, never `owner`. Everything a machine needs -
research, content generation, review, publish, export - is member-level,
while account-shaping actions (billing, team, webhooks, keys themselves)
stay behind a real human session. A leaked key therefore cannot escalate
into the account.
"""

from __future__ import annotations

import hashlib
import secrets

KEY_PREFIX = "ai_live_"
"""Marks a string as one of our keys on sight - lets `auth.py` tell an API
key from a session token in the same `Authorization` header, and makes leaked
keys greppable in logs and repo scanners."""

_SECRET_BYTES = 32


def generate_key() -> tuple[str, str, str]:
    """Returns `(full_key, prefix, key_hash)`. `full_key` is returned to the
    caller once and never stored; `prefix` is the displayable first chunk so
    a key can be recognized in a list without revealing it."""
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    full_key = f"{KEY_PREFIX}{secret}"
    return full_key, full_key[: len(KEY_PREFIX) + 6], hash_key(full_key)


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def looks_like_api_key(candidate: str) -> bool:
    return candidate.startswith(KEY_PREFIX)


__all__ = ["KEY_PREFIX", "generate_key", "hash_key", "looks_like_api_key"]
