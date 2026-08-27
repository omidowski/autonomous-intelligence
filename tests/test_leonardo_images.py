import json

import pytest

from autonomous_intelligence import leonardo_images


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_generate_image_polls_until_complete(monkeypatch):
    submit_payload = json.dumps({"sdGenerationJob": {"generationId": "gen-1"}}).encode("utf-8")
    pending_payload = json.dumps(
        {"generations_by_pk": {"status": "PENDING"}}
    ).encode("utf-8")
    complete_payload = json.dumps(
        {
            "generations_by_pk": {
                "status": "COMPLETE",
                "generated_images": [{"url": "https://cdn.leonardo.ai/img/1.jpg"}],
            }
        }
    ).encode("utf-8")

    responses = iter([submit_payload, pending_payload, complete_payload])
    calls = []

    def fake_urlopen(request_or_url, timeout=None):
        url = request_or_url if isinstance(request_or_url, str) else request_or_url.full_url
        calls.append(url)
        if url == "https://cdn.leonardo.ai/img/1.jpg":
            return _FakeResponse(b"fake-image-bytes")
        return _FakeResponse(next(responses))

    monkeypatch.setattr(leonardo_images.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(leonardo_images.time, "sleep", lambda seconds: None)

    image_bytes = leonardo_images.generate_image("a red circle", api_key="fake-key")

    assert image_bytes == b"fake-image-bytes"
    assert calls[0] == f"{leonardo_images.LEONARDO_API_BASE}/generations"
    assert calls[-1] == "https://cdn.leonardo.ai/img/1.jpg"


def test_generate_image_raises_on_failed_status(monkeypatch):
    submit_payload = json.dumps({"sdGenerationJob": {"generationId": "gen-1"}}).encode("utf-8")
    failed_payload = json.dumps({"generations_by_pk": {"status": "FAILED"}}).encode("utf-8")

    responses = iter([submit_payload, failed_payload])
    monkeypatch.setattr(
        leonardo_images.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(next(responses))
    )
    monkeypatch.setattr(leonardo_images.time, "sleep", lambda seconds: None)

    with pytest.raises(leonardo_images.LeonardoImageError):
        leonardo_images.generate_image("a red circle", api_key="fake-key")


def test_generate_image_raises_on_timeout(monkeypatch):
    submit_payload = json.dumps({"sdGenerationJob": {"generationId": "gen-1"}}).encode("utf-8")
    pending_payload = json.dumps({"generations_by_pk": {"status": "PENDING"}}).encode("utf-8")

    def counting_urlopen(*args, **kwargs):
        result = _FakeResponse(submit_payload if counting_urlopen.calls == 0 else pending_payload)
        counting_urlopen.calls += 1
        return result

    counting_urlopen.calls = 0

    monkeypatch.setattr(leonardo_images.urllib.request, "urlopen", counting_urlopen)
    monkeypatch.setattr(leonardo_images.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(leonardo_images, "MAX_POLL_ATTEMPTS", 2)

    with pytest.raises(leonardo_images.LeonardoImageError, match="timed out"):
        leonardo_images.generate_image("a red circle", api_key="fake-key")
