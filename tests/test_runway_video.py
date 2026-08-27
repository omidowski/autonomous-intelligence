import json

import pytest
from PIL import Image

from autonomous_intelligence import runway_video


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


def test_generate_video_from_image_polls_until_succeeded(tmp_path, monkeypatch):
    submit_payload = json.dumps({"id": "task-1"}).encode("utf-8")
    pending_payload = json.dumps({"status": "RUNNING"}).encode("utf-8")
    succeeded_payload = json.dumps(
        {"status": "SUCCEEDED", "output": ["https://cdn.runwayml.com/out/1.mp4"]}
    ).encode("utf-8")

    responses = iter([submit_payload, pending_payload, succeeded_payload])
    calls = []

    def fake_urlopen(request_or_url, timeout=None):
        url = request_or_url if isinstance(request_or_url, str) else request_or_url.full_url
        calls.append(url)
        if url == "https://cdn.runwayml.com/out/1.mp4":
            return _FakeResponse(b"fake-video-bytes")
        return _FakeResponse(next(responses))

    monkeypatch.setattr(runway_video.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(runway_video.time, "sleep", lambda seconds: None)

    video_bytes = runway_video.generate_video_from_image(
        _fake_image(tmp_path), "subtle motion", api_key="fake-key"
    )

    assert video_bytes == b"fake-video-bytes"
    assert calls[0] == f"{runway_video.RUNWAY_API_BASE}/image_to_video"
    assert calls[-1] == "https://cdn.runwayml.com/out/1.mp4"


def test_generate_video_from_image_raises_on_failed_status(tmp_path, monkeypatch):
    submit_payload = json.dumps({"id": "task-1"}).encode("utf-8")
    failed_payload = json.dumps({"status": "FAILED", "failure": "moderation"}).encode("utf-8")

    responses = iter([submit_payload, failed_payload])
    monkeypatch.setattr(
        runway_video.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(next(responses))
    )
    monkeypatch.setattr(runway_video.time, "sleep", lambda seconds: None)

    with pytest.raises(runway_video.RunwayVideoError):
        runway_video.generate_video_from_image(_fake_image(tmp_path), "subtle motion", api_key="fake-key")


def test_generate_video_from_image_raises_on_timeout(tmp_path, monkeypatch):
    submit_payload = json.dumps({"id": "task-1"}).encode("utf-8")
    pending_payload = json.dumps({"status": "RUNNING"}).encode("utf-8")

    def counting_urlopen(*args, **kwargs):
        result = _FakeResponse(submit_payload if counting_urlopen.calls == 0 else pending_payload)
        counting_urlopen.calls += 1
        return result

    counting_urlopen.calls = 0

    monkeypatch.setattr(runway_video.urllib.request, "urlopen", counting_urlopen)
    monkeypatch.setattr(runway_video.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(runway_video, "MAX_POLL_ATTEMPTS", 2)

    with pytest.raises(runway_video.RunwayVideoError, match="timed out"):
        runway_video.generate_video_from_image(_fake_image(tmp_path), "subtle motion", api_key="fake-key")
