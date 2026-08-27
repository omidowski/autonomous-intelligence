"""ElevenLabs TTS fallback for when OpenAI speech synthesis is unavailable -
see `agents/audio_narrator.py`: on `llm.MediaUnavailableError` (e.g. this
OpenAI project has no access to `gpt-4o-mini-tts`/`tts-1`, a 403 - a
different restriction than the image-generation org-verification gate, but
handled the same way: fall back instead of failing the pipeline), synthesize
real narration audio via ElevenLabs instead of a silent placeholder.

Uses stdlib `urllib.request` for a single POST request - not worth a new
HTTP-client dependency for this, matching `stock_photos.py`.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import wave

ELEVENLABS_TTS_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"
"""ElevenLabs' premade "Sarah" voice. Free-tier API keys can only use voices
in the account's own voice library (`GET /v1/voices`), not the full shared
voice library (that 402s with "paid_plan_required") - "Sarah" is one of the
premade voices ElevenLabs adds to every new account's library by default, so
this works out of the box on the free tier."""

SAMPLE_RATE = 24000
"""Matches `media_utils.render_silent_wav`'s sample rate so downstream
`VideoAssembler` moviepy assembly doesn't need to know or care which path
produced the narration file."""

# Some APIs front with bot-detection that blocks urllib's default
# "Python-urllib/x.y" User-Agent (see `stock_photos.py` - Pexels does this
# via Cloudflare) - send a plain browser-style one defensively.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class SpeechSynthesisError(RuntimeError):
    """ElevenLabs synthesis failed (network error, invalid key, empty
    response) - the caller (`AudioNarratorAgent`) treats this the same as
    `MediaUnavailableError` and falls back to the silent placeholder."""


def synthesize_speech(text: str, *, api_key: str, voice_id: str = DEFAULT_VOICE_ID) -> bytes:
    """Synthesizes `text` via ElevenLabs and returns a valid WAV file's
    bytes (16-bit mono PCM at `SAMPLE_RATE`) - requests raw PCM from the API
    and wraps it with a proper WAV header via stdlib `wave`, since ElevenLabs'
    `pcm_*` output formats are headerless."""
    url = f"{ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id=voice_id)}?output_format=pcm_{SAMPLE_RATE}"
    body = json.dumps({"text": text, "model_id": "eleven_multilingual_v2"}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/pcm",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            pcm_bytes = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SpeechSynthesisError(f"ElevenLabs speech synthesis failed: {exc}") from exc

    if not pcm_bytes:
        raise SpeechSynthesisError("ElevenLabs returned no audio data.")

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


__all__ = [
    "DEFAULT_VOICE_ID",
    "ELEVENLABS_TTS_URL_TEMPLATE",
    "SAMPLE_RATE",
    "SpeechSynthesisError",
    "synthesize_speech",
]
