"""Leonardo.ai image-generation fallback - see `agents/image_generator.py`:
when OpenAI image generation is unavailable, try a real AI-generated image
from Leonardo.ai before falling back further to a Pexels stock photo
(`stock_photos.py`) and finally the placeholder. Unlike Pexels, this
produces a genuinely generated image matching the original AI prompt
(closer to the original intent), so it's tried first in the fallback chain.

Leonardo's generation API is asynchronous (submit a job, poll until done) -
unlike the single-request Pexels/ElevenLabs calls, this module blocks on a
short poll loop with a hard timeout so a slow/stuck job can't hang the
content pipeline indefinitely.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

LEONARDO_API_BASE = "https://cloud.leonardo.ai/api/rest/v1"

DEFAULT_MODEL_ID = "6bef9f1b-29cb-40c7-b9df-32b51c1f67d3"
"""Leonardo Phoenix - a current flagship general-purpose model, reasonable
default for editorial-style imagery without asking the user to pick one."""

POLL_INTERVAL_SECONDS = 3
MAX_POLL_ATTEMPTS = 20
"""Hard cap of ~60s total wait (`POLL_INTERVAL_SECONDS * MAX_POLL_ATTEMPTS`)
before giving up and falling back further down the chain."""


class LeonardoImageError(RuntimeError):
    """Leonardo generation failed, timed out, or returned nothing usable -
    the caller (`ImageGeneratorAgent`) treats this the same as
    `MediaUnavailableError`/`StockPhotoError` and falls back further."""


def _request(url: str, *, api_key: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LeonardoImageError(f"Leonardo API request failed: {exc}") from exc


def generate_image(
    prompt: str,
    *,
    api_key: str,
    model_id: str = DEFAULT_MODEL_ID,
    width: int = 1024,
    height: int = 1536,
) -> bytes:
    """Submits a generation job, polls until complete, and downloads the
    first generated image's bytes. Raises `LeonardoImageError` on any
    failure (bad key, moderation rejection, timeout, empty result)."""
    submission = _request(
        f"{LEONARDO_API_BASE}/generations",
        api_key=api_key,
        method="POST",
        body={"prompt": prompt, "modelId": model_id, "width": width, "height": height, "num_images": 1},
    )
    try:
        generation_id = submission["sdGenerationJob"]["generationId"]
    except (KeyError, TypeError) as exc:
        raise LeonardoImageError(f"Unexpected Leonardo submission response: {exc}") from exc

    status_url = f"{LEONARDO_API_BASE}/generations/{generation_id}"
    for _ in range(MAX_POLL_ATTEMPTS):
        time.sleep(POLL_INTERVAL_SECONDS)
        result = _request(status_url, api_key=api_key)
        try:
            generation = result["generations_by_pk"]
            status = generation["status"]
        except (KeyError, TypeError) as exc:
            raise LeonardoImageError(f"Unexpected Leonardo status response: {exc}") from exc

        if status == "COMPLETE":
            images = generation.get("generated_images") or []
            if not images:
                raise LeonardoImageError("Leonardo generation completed with no images.")
            image_url = images[0].get("url")
            if not image_url:
                raise LeonardoImageError("Leonardo generation image has no URL.")
            try:
                with urllib.request.urlopen(image_url, timeout=30) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                raise LeonardoImageError(f"Leonardo image download failed: {exc}") from exc
        if status == "FAILED":
            raise LeonardoImageError("Leonardo generation failed (status=FAILED).")

    raise LeonardoImageError(f"Leonardo generation timed out after {MAX_POLL_ATTEMPTS} polls.")


__all__ = [
    "DEFAULT_MODEL_ID",
    "LEONARDO_API_BASE",
    "LeonardoImageError",
    "generate_image",
]
