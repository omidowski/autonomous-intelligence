"""Runway (runwayml.com) image-to-video generation - an optional upgrade
over the existing image-slideshow assembly in
`agents/video_assembler.py::VideoAssembler`. Unlike the Pexels/Leonardo/
ElevenLabs fallbacks, this isn't triggered by a primary-provider failure -
there's no "OpenAI video generation" to fall back from. It's tried first,
as a quality upgrade, only when `settings.runway_api_key` is configured;
`daily_content_orchestrator.py` falls back to the existing moviepy slideshow
assembly whenever Runway is unconfigured or a generation fails.

Real money per generation (no free tier like Pexels/ElevenLabs/Leonardo) -
only ever called when a company/operator has explicitly configured a key.

Runway's generation API is asynchronous (submit a job, poll until done) -
same shape as `leonardo_images.py`.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path

RUNWAY_API_BASE = "https://api.dev.runwayml.com/v1"
RUNWAY_API_VERSION = "2024-11-06"

DEFAULT_MODEL = "gen4_turbo"
DEFAULT_RATIO = "768:1280"
DEFAULT_DURATION_SECONDS = 5

POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 24
"""Hard cap of ~2 minutes total wait before giving up and falling back to
the moviepy slideshow assembly - Runway generations commonly take 30-90s."""


class RunwayVideoError(RuntimeError):
    """Runway generation failed, timed out, or returned nothing usable - the
    caller (`daily_content_orchestrator.py`) falls back to the existing
    moviepy slideshow assembly."""


def _image_to_data_uri(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(image_path.name)
    mime_type = mime_type or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _request(url: str, *, api_key: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Runway-Version": RUNWAY_API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RunwayVideoError(f"Runway API request failed: {exc}") from exc


def generate_video_from_image(
    image_path: Path,
    prompt: str,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    ratio: str = DEFAULT_RATIO,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
) -> bytes:
    """Submits an image-to-video job for `image_path`, polls until complete,
    and downloads the resulting video's bytes. Raises `RunwayVideoError` on
    any failure (bad key, moderation rejection, timeout, empty result)."""
    submission = _request(
        f"{RUNWAY_API_BASE}/image_to_video",
        api_key=api_key,
        method="POST",
        body={
            "promptImage": _image_to_data_uri(image_path),
            "promptText": prompt,
            "model": model,
            "ratio": ratio,
            "duration": duration_seconds,
        },
    )
    try:
        task_id = submission["id"]
    except (KeyError, TypeError) as exc:
        raise RunwayVideoError(f"Unexpected Runway submission response: {exc}") from exc

    status_url = f"{RUNWAY_API_BASE}/tasks/{task_id}"
    for _ in range(MAX_POLL_ATTEMPTS):
        time.sleep(POLL_INTERVAL_SECONDS)
        task = _request(status_url, api_key=api_key)
        status = task.get("status")

        if status == "SUCCEEDED":
            outputs = task.get("output") or []
            if not outputs:
                raise RunwayVideoError("Runway task succeeded with no output.")
            try:
                with urllib.request.urlopen(outputs[0], timeout=60) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                raise RunwayVideoError(f"Runway video download failed: {exc}") from exc
        if status == "FAILED":
            raise RunwayVideoError(f"Runway task failed: {task.get('failure') or 'unknown error'}")

    raise RunwayVideoError(f"Runway task timed out after {MAX_POLL_ATTEMPTS} polls.")


__all__ = [
    "DEFAULT_DURATION_SECONDS",
    "DEFAULT_MODEL",
    "DEFAULT_RATIO",
    "RUNWAY_API_BASE",
    "RunwayVideoError",
    "generate_video_from_image",
]
