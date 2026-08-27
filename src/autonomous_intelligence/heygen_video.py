"""HeyGen (heygen.com) AI-avatar talking-head video generation - an
alternative "quality upgrade" over the moviepy slideshow assembly
(`agents/video_assembler.py::VideoAssembler`), distinct from the Runway/
Kling upgrades: instead of adding motion to the generated cover image,
HeyGen renders a talking digital avatar reading the script, using
HeyGen's own text-to-speech. Requires a pre-registered avatar
(`settings.heygen_avatar_id`) from HeyGen's own dashboard/API - there's
no way to create one from an arbitrary photo in this call, and no image
or narration audio from the rest of the pipeline is reused (HeyGen
generates its own voice track).

HeyGen's generation API is asynchronous (submit a job, poll until done) -
same shape as `leonardo_images.py`/`runway_video.py`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

HEYGEN_API_BASE = "https://api.heygen.com"

DEFAULT_AVATAR_STYLE = "normal"
DEFAULT_VOICE_ID = "1bd001e7e50f421d891986aad5158bc8"
"""A default HeyGen library voice (English) - override with
`settings.heygen_voice_id` once a company picks a specific one."""
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 1280

POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 36
"""Hard cap of ~3 minutes total wait - HeyGen avatar renders commonly take
1-2 minutes."""


class HeyGenVideoError(RuntimeError):
    """HeyGen generation failed, timed out, or returned nothing usable -
    the caller (`daily_content_orchestrator.py`) falls back to the next
    configured video upgrade or the moviepy slideshow assembly."""


def _request(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    body: dict | None = None,
    params: dict | None = None,
) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HeyGenVideoError(f"HeyGen API request failed: {exc}") from exc


def generate_avatar_video(
    script_text: str,
    *,
    api_key: str,
    avatar_id: str,
    voice_id: str = DEFAULT_VOICE_ID,
    avatar_style: str = DEFAULT_AVATAR_STYLE,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> bytes:
    """Submits an avatar-video job for `script_text`, polls until complete,
    and downloads the resulting video's bytes. Raises `HeyGenVideoError` on
    any failure (bad key, unknown avatar/voice id, timeout, empty result)."""
    submission = _request(
        f"{HEYGEN_API_BASE}/v2/video/generate",
        api_key=api_key,
        method="POST",
        body={
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": avatar_id,
                        "avatar_style": avatar_style,
                    },
                    "voice": {"type": "text", "input_text": script_text, "voice_id": voice_id},
                }
            ],
            "dimension": {"width": width, "height": height},
        },
    )
    try:
        video_id = submission["data"]["video_id"]
    except (KeyError, TypeError) as exc:
        raise HeyGenVideoError(f"Unexpected HeyGen submission response: {exc}") from exc

    status_url = f"{HEYGEN_API_BASE}/v1/video_status.get"
    for _ in range(MAX_POLL_ATTEMPTS):
        time.sleep(POLL_INTERVAL_SECONDS)
        result = _request(status_url, api_key=api_key, params={"video_id": video_id})
        try:
            data = result["data"]
            status = data["status"]
        except (KeyError, TypeError) as exc:
            raise HeyGenVideoError(f"Unexpected HeyGen status response: {exc}") from exc

        if status == "completed":
            video_url = data.get("video_url")
            if not video_url:
                raise HeyGenVideoError("HeyGen video completed with no video_url.")
            try:
                with urllib.request.urlopen(video_url, timeout=60) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                raise HeyGenVideoError(f"HeyGen video download failed: {exc}") from exc
        if status == "failed":
            error = data.get("error") or {}
            raise HeyGenVideoError(
                f"HeyGen generation failed: {error.get('message') or 'unknown error'}"
            )

    raise HeyGenVideoError(f"HeyGen generation timed out after {MAX_POLL_ATTEMPTS} polls.")


__all__ = [
    "DEFAULT_AVATAR_STYLE",
    "DEFAULT_HEIGHT",
    "DEFAULT_VOICE_ID",
    "DEFAULT_WIDTH",
    "HEYGEN_API_BASE",
    "HeyGenVideoError",
    "generate_avatar_video",
]
