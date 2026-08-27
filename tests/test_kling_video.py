import json

import pytest
from PIL import Image

from autonomous_intelligence import kling_video


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_image(tmp_path):
    path = tmp_path / "cover.png"
    Image.new("RGB", (16, 16), color=(10, 20, 30)).save(path)
    return path


def test_generate_jwt_produces_three_dot_separated_hs256_token():
    token = kling_video._generate_jwt("access-key", "secret-key")
    parts = token.split(".")
    assert len(parts) == 3

    import base64
    import json as _json

    header = _json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
    assert header == {"alg": "HS256", "typ": "JWT"}


def test_generate_video_from_image_polls_until_succeeded(tmp_path, monkeypatch):
    submit_payload = json.dumps({"data": {"task_id": "task-1"}}).encode("utf-8")
    pending_payload = json.dumps({"data": {"task_status": "processing"}}).encode("utf-8")
    succeeded_payload = json.dumps(
        {
            "data": {
                "task_status": "succeed",
                "task_result": {"videos": [{"url": "https://cdn.klingai.com/out/1.mp4"}]},
            }
        }
    ).encode("utf-8")

    responses = iter([submit_payload, pending_payload, succeeded_payload])
    calls = []

    def fake_urlopen(request_or_url, timeout=None):
        url = request_or_url if isinstance(request_or_url, str) else request_or_url.full_url
        calls.append(url)
        if url == "https://cdn.klingai.com/out/1.mp4":
            return _FakeResponse(b"fake-video-bytes")
        return _FakeResponse(next(responses))

    monkeypatch.setattr(kling_video.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(kling_video.time, "sleep", lambda seconds: None)

    video_bytes = kling_video.generate_video_from_image(
        _fake_image(tmp_path), "subtle motion", access_key="ak", secret_key="sk"
    )

    assert video_bytes == b"fake-video-bytes"
    assert calls[0] == f"{kling_video.KLING_API_BASE}/v1/videos/image2video"
    assert calls[-1] == "https://cdn.klingai.com/out/1.mp4"


def test_generate_video_from_image_raises_on_failed_status(tmp_path, monkeypatch):
    submit_payload = json.dumps({"data": {"task_id": "task-1"}}).encode("utf-8")
    failed_payload = json.dumps(
        {"data": {"task_status": "failed", "task_status_msg": "moderation"}}
    ).encode("utf-8")

    responses = iter([submit_payload, failed_payload])
    monkeypatch.setattr(
        kling_video.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(next(responses))
    )
    monkeypatch.setattr(kling_video.time, "sleep", lambda seconds: None)

    with pytest.raises(kling_video.KlingVideoError, match="moderation"):
        kling_video.generate_video_from_image(
            _fake_image(tmp_path), "subtle motion", access_key="ak", secret_key="sk"
        )


def test_generate_video_from_image_raises_on_timeout(tmp_path, monkeypatch):
    submit_payload = json.dumps({"data": {"task_id": "task-1"}}).encode("utf-8")
    pending_payload = json.dumps({"data": {"task_status": "processing"}}).encode("utf-8")

    def counting_urlopen(*args, **kwargs):
        result = _FakeResponse(submit_payload if counting_urlopen.calls == 0 else pending_payload)
        counting_urlopen.calls += 1
        return result

    counting_urlopen.calls = 0

    monkeypatch.setattr(kling_video.urllib.request, "urlopen", counting_urlopen)
    monkeypatch.setattr(kling_video.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(kling_video, "MAX_POLL_ATTEMPTS", 2)

    with pytest.raises(kling_video.KlingVideoError, match="timed out"):
        kling_video.generate_video_from_image(
            _fake_image(tmp_path), "subtle motion", access_key="ak", secret_key="sk"
        )
