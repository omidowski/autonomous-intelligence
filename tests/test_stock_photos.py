import json
import urllib.error

import pytest

from autonomous_intelligence import stock_photos


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_search_photo_returns_first_result(monkeypatch):
    search_payload = json.dumps(
        {
            "photos": [
                {
                    "src": {"large": "https://images.pexels.com/photos/1/large.jpg"},
                    "photographer": "Jane Doe",
                    "url": "https://www.pexels.com/photo/1/",
                }
            ]
        }
    ).encode("utf-8")

    calls = []

    def fake_urlopen(request_or_url, timeout=None):
        url = request_or_url if isinstance(request_or_url, str) else request_or_url.full_url
        calls.append(url)
        if "api.pexels.com" in url:
            return _FakeResponse(search_payload)
        return _FakeResponse(b"fake-image-bytes")

    monkeypatch.setattr(stock_photos.urllib.request, "urlopen", fake_urlopen)

    photo = stock_photos.search_photo("breaking news", api_key="fake-key")

    assert photo.image_bytes == b"fake-image-bytes"
    assert photo.photographer == "Jane Doe"
    assert photo.photo_url == "https://www.pexels.com/photo/1/"
    assert len(calls) == 2


def test_search_photo_raises_on_no_results(monkeypatch):
    empty_payload = json.dumps({"photos": []}).encode("utf-8")
    monkeypatch.setattr(
        stock_photos.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(empty_payload)
    )

    with pytest.raises(stock_photos.StockPhotoError):
        stock_photos.search_photo("an extremely obscure query", api_key="fake-key")


def test_search_photo_raises_on_network_error(monkeypatch):
    def raise_error(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(stock_photos.urllib.request, "urlopen", raise_error)

    with pytest.raises(stock_photos.StockPhotoError):
        stock_photos.search_photo("news", api_key="fake-key")
