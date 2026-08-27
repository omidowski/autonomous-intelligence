"""Stock-photo fallback (Pexels API) for when AI image generation is
unavailable - see `agents/image_generator.py`: on `llm.MediaUnavailableError`
(e.g. `gpt-image-1` blocked pending OpenAI org verification, or a
non-OpenAI/demo provider), search Pexels for a real, topically relevant
photo instead of falling straight to the deterministic placeholder image.

Uses stdlib `urllib.request` for a single simple GET request with an API-key
header - not worth a new HTTP-client dependency for this.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"

# Cloudflare (fronting Pexels) blocks requests with urllib's default
# "Python-urllib/x.y" User-Agent as bot traffic (403, Cloudflare error
# 1010) - a plain browser-style User-Agent is enough to pass.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class StockPhotoError(RuntimeError):
    """No matching/downloadable stock photo - the caller (`ImageGeneratorAgent`)
    treats this the same as `MediaUnavailableError` and falls back to the
    placeholder image."""


@dataclass
class StockPhoto:
    image_bytes: bytes
    photographer: str
    photo_url: str


def search_photo(query: str, *, api_key: str, orientation: str = "portrait") -> StockPhoto:
    """Searches Pexels for `query`, downloads the first result's "large" image
    size. Raises `StockPhotoError` on any failure (no results, network
    error, invalid key, bad response) - never lets a stock-photo hiccup
    break the content pipeline."""
    params = urllib.parse.urlencode({"query": query, "per_page": 1, "orientation": orientation})
    request = urllib.request.Request(
        f"{PEXELS_SEARCH_URL}?{params}",
        headers={"Authorization": api_key, "User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise StockPhotoError(f"Pexels search failed: {exc}") from exc

    photos = data.get("photos") or []
    if not photos:
        raise StockPhotoError(f"No stock photo found for query: {query!r}")

    photo = photos[0]
    try:
        image_url = photo["src"]["large"]
    except (KeyError, TypeError) as exc:
        raise StockPhotoError(f"Unexpected Pexels response shape: {exc}") from exc

    image_request = urllib.request.Request(image_url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(image_request, timeout=15) as response:
            image_bytes = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise StockPhotoError(f"Pexels image download failed: {exc}") from exc

    return StockPhoto(
        image_bytes=image_bytes,
        photographer=photo.get("photographer") or "Unknown",
        photo_url=photo.get("url") or image_url,
    )


__all__ = ["PEXELS_SEARCH_URL", "StockPhoto", "StockPhotoError", "search_photo"]
