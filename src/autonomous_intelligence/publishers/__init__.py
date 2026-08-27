from __future__ import annotations

from .base import Publisher, PublishResult
from .stub import StubPublisher

_PUBLISHERS: dict[str, Publisher] = {
    "stub": StubPublisher(),
}


def get_publisher(name: str = "stub") -> Publisher:
    """Registry lookup. Real platform publishers (x/instagram/linkedin)
    register into `_PUBLISHERS` once API credentials exist - no other code
    changes when that happens."""
    try:
        return _PUBLISHERS[name]
    except KeyError:
        raise ValueError(f"Unknown publisher: {name!r}") from None


__all__ = ["PublishResult", "Publisher", "StubPublisher", "get_publisher"]
