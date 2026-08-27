import wave
from io import BytesIO

import pytest

from autonomous_intelligence import text_to_speech


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_synthesize_speech_wraps_pcm_in_valid_wav(monkeypatch):
    fake_pcm = b"\x01\x00" * 100  # 100 frames of 16-bit mono PCM

    monkeypatch.setattr(
        text_to_speech.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(fake_pcm)
    )

    wav_bytes = text_to_speech.synthesize_speech("Hello world", api_key="fake-key")

    with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == text_to_speech.SAMPLE_RATE
        assert wav_file.readframes(wav_file.getnframes()) == fake_pcm


def test_synthesize_speech_raises_on_empty_response(monkeypatch):
    monkeypatch.setattr(
        text_to_speech.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(b"")
    )

    with pytest.raises(text_to_speech.SpeechSynthesisError):
        text_to_speech.synthesize_speech("Hello", api_key="fake-key")


def test_synthesize_speech_raises_on_network_error(monkeypatch):
    import urllib.error

    def raise_error(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(text_to_speech.urllib.request, "urlopen", raise_error)

    with pytest.raises(text_to_speech.SpeechSynthesisError):
        text_to_speech.synthesize_speech("Hello", api_key="fake-key")
