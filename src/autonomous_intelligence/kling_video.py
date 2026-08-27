"""Kling AI (klingai.com) image-to-video generation - an alternative
"quality upgrade" over the moviepy slideshow assembly, alongside Runway
(`runway_video.py`) and HeyGen (`heygen_video.py`) in the priority-ordered
upgrade chain tried by `daily_content_orchestrator.py`.

CAVEAT: Kling's official API reference (kling.ai/document-api) could not
be fetched while writing this module (the docs site blocked automated
fetches); the request/response field names below are best-effort,
cross-referenced from third-party API aggregators and open-source client
wrappers rather than the primary source. Treat this integration as
unverified until exercised against a real key - if generation 404s or a
response is missing an expected field, check the current reference at
https://kling.ai/document-api/ before assuming the rest of the pipeline
is broken.

Kling's generation API is asynchronous (submit a job, poll until done),
and authenticates via a short-lived HS256 JWT signed from an access-key/
secret-key pair (not a static bearer token like Runway/Leonardo/HeyGen) -
see `_generate_jwt()`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

KLING_API_BASE = "https://api-singapore.klingai.com"

DEFAULT_MODEL_NAME = "kling-v1"
DEFAULT_MODE = "std"
DEFAULT_DURATION_SECONDS = "5"

JWT_TTL_SECONDS = 1800
"""Kling JWTs are short-lived (~30 min per the auth docs) - re-signed on
every request rather than cached, since a single image-to-video job can
outlive one token across the poll loop."""

POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 36
"""Hard cap of ~3 minutes total wait before giving up and falling back to
the next configured video upgrade or the moviepy slideshow assembly."""


class KlingVideoError(RuntimeError):
    """Kling generation failed, timed out, or returned nothing usable - the
    caller (`daily_content_orchestrator.py`) falls back to the next
    configured video upgrade or the moviepy slideshow assembly."""


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_jwt(access_key: str, secret_key: str) -> str:
    """Signs a short-lived HS256 JWT from an access-key/secret-key pair, per
    Kling's auth scheme - implemented with stdlib `hmac`/`hashlib` rather
    than adding a JWT dependency for one call site."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": access_key, "exp": now + JWT_TTL_SECONDS, "nbf": now - 5}
    signing_input = (
        _base64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _base64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )
    signature = hmac.new(
        secret_key.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_base64url(signature)}"


def _image_to_base64(image_path: Path) -> str:
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


def _request(url: str, *, token: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise KlingVideoError(f"Kling API request failed: {exc}") from exc


def generate_video_from_image(
    image_path: Path,
    prompt: str,
    *,
    access_key: str,
    secret_key: str,
    model_name: str = DEFAULT_MODEL_NAME,
    mode: str = DEFAULT_MODE,
    duration_seconds: str = DEFAULT_DURATION_SECONDS,
) -> bytes:
    """Submits an image-to-video job for `image_path`, polls until complete,
    and downloads the resulting video's bytes. Raises `KlingVideoError` on
    any failure (bad key, moderation rejection, timeout, empty result)."""
    submission = _request(
        f"{KLING_API_BASE}/v1/videos/image2video",
        token=_generate_jwt(access_key, secret_key),
        method="POST",
        body={
            "model_name": model_name,
            "image": _image_to_base64(image_path),
            "prompt": prompt,
            "mode": mode,
            "duration": duration_seconds,
        },
    )
    try:
        task_id = submission["data"]["task_id"]
    except (KeyError, TypeError) as exc:
        raise KlingVideoError(f"Unexpected Kling submission response: {exc}") from exc

    status_url = f"{KLING_API_BASE}/v1/videos/image2video/{task_id}"
    for _ in range(MAX_POLL_ATTEMPTS):
        time.sleep(POLL_INTERVAL_SECONDS)
        result = _request(status_url, token=_generate_jwt(access_key, secret_key))
        try:
            data = result["data"]
            status = data["task_status"]
        except (KeyError, TypeError) as exc:
            raise KlingVideoError(f"Unexpected Kling status response: {exc}") from exc

        if status == "succeed":
            videos = (data.get("task_result") or {}).get("videos") or []
            if not videos:
                raise KlingVideoError("Kling task succeeded with no videos.")
            video_url = videos[0].get("url")
            if not video_url:
                raise KlingVideoError("Kling result video has no url.")
            try:
                with urllib.request.urlopen(video_url, timeout=60) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                raise KlingVideoError(f"Kling video download failed: {exc}") from exc
        if status == "failed":
            raise KlingVideoError(f"Kling task failed: {data.get('task_status_msg') or 'unknown error'}")

    raise KlingVideoError(f"Kling task timed out after {MAX_POLL_ATTEMPTS} polls.")


__all__ = [
    "DEFAULT_DURATION_SECONDS",
    "DEFAULT_MODE",
    "DEFAULT_MODEL_NAME",
    "KLING_API_BASE",
    "KlingVideoError",
    "generate_video_from_image",
]
