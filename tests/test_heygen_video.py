import json

import pytest

from autonomous_intelligence import heygen_video


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_generate_avatar_video_polls_until_completed(monkeypatch):
    submit_payload = json.dumps({"data": {"video_id": "vid-1"}}).encode("utf-8")
    processing_payload = json.dumps({"data": {"status": "processing"}}).encode("utf-8")
    completed_payload = json.dumps(
        {"data": {"status": "completed", "video_url": "https://cdn.heygen.com/out/1.mp4"}}
    ).encode("utf-8")

    responses = iter([submit_payload, processing_payload, completed_payload])
    calls = []

    def fake_urlopen(request_or_url, timeout=None):
        url = request_or_url if isinstance(request_or_url, str) else request_or_url.full_url
        calls.append(url)
        if url == "https://cdn.heygen.com/out/1.mp4":
            return _FakeResponse(b"fake-video-bytes")
        return _FakeResponse(next(responses))

    monkeypatch.setattr(heygen_video.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(heygen_video.time, "sleep", lambda seconds: None)

    video_bytes = heygen_video.generate_avatar_video(
        "hello world", api_key="fake-key", avatar_id="avatar-1"
    )

    assert video_bytes == b"fake-video-bytes"
    assert calls[0] == f"{heygen_video.HEYGEN_API_BASE}/v2/video/generate"
    assert calls[-1] == "https://cdn.heygen.com/out/1.mp4"


def test_generate_avatar_video_raises_on_failed_status(monkeypatch):
    submit_payload = json.dumps({"data": {"video_id": "vid-1"}}).encode("utf-8")
    failed_payload = json.dumps(
        {"data": {"status": "failed", "error": {"message": "bad avatar_id"}}}
    ).encode("utf-8")

    responses = iter([submit_payload, failed_payload])
    monkeypatch.setattr(
        heygen_video.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(next(responses))
    )
    monkeypatch.setattr(heygen_video.time, "sleep", lambda seconds: None)

    with pytest.raises(heygen_video.HeyGenVideoError, match="bad avatar_id"):
        heygen_video.generate_avatar_video("hello world", api_key="fake-key", avatar_id="avatar-1")


def test_generate_avatar_video_raises_on_timeout(monkeypatch):
    submit_payload = json.dumps({"data": {"video_id": "vid-1"}}).encode("utf-8")
    processing_payload = json.dumps({"data": {"status": "processing"}}).encode("utf-8")

    def counting_urlopen(*args, **kwargs):
        result = _FakeResponse(submit_payload if counting_urlopen.calls == 0 else processing_payload)
        counting_urlopen.calls += 1
        return result

    counting_urlopen.calls = 0

    monkeypatch.setattr(heygen_video.urllib.request, "urlopen", counting_urlopen)
    monkeypatch.setattr(heygen_video.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(heygen_video, "MAX_POLL_ATTEMPTS", 2)

    with pytest.raises(heygen_video.HeyGenVideoError, match="timed out"):
        heygen_video.generate_avatar_video("hello world", api_key="fake-key", avatar_id="avatar-1")


def test_generate_avatar_video_raises_on_bad_submission_response(monkeypatch):
    monkeypatch.setattr(
        heygen_video.urllib.request,
        "urlopen",
        lambda *a, **kw: _FakeResponse(json.dumps({"data": {}}).encode("utf-8")),
    )

    with pytest.raises(heygen_video.HeyGenVideoError, match="Unexpected HeyGen submission response"):
        heygen_video.generate_avatar_video("hello world", api_key="fake-key", avatar_id="avatar-1")
