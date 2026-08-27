from pathlib import Path

from ..config import Settings
from ..content_models import GeneratedAsset
from ..llm import LLMProvider, MediaUnavailableError
from ..media_utils import render_silent_wav
from ..text_to_speech import SpeechSynthesisError, synthesize_speech


class AudioNarratorAgent:
    """Two-tier fallback: OpenAI speech synthesis (`llm.speech`) -> ElevenLabs
    (only if `settings.elevenlabs_api_key` is configured) -> silent
    placeholder WAV. Mirrors `ImageGeneratorAgent`'s fallback chain - see its
    docstring."""

    def __init__(self, llm: LLMProvider, settings: Settings | None = None):
        self.llm = llm
        self.settings = settings

    def narrate(self, text: str, out_dir: Path, base_name: str = "narration") -> GeneratedAsset:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{base_name}.wav"

        try:
            out_path.write_bytes(self.llm.speech(text))
            return GeneratedAsset(kind="audio", path=str(out_path), is_placeholder=False)
        except MediaUnavailableError:
            pass

        api_key = self.settings.elevenlabs_api_key if self.settings is not None else None
        if api_key:
            try:
                out_path.write_bytes(synthesize_speech(text, api_key=api_key))
                return GeneratedAsset(
                    kind="audio", path=str(out_path), is_placeholder=False, source="fallback"
                )
            except SpeechSynthesisError:
                pass

        render_silent_wav(text, out_path)
        return GeneratedAsset(kind="audio", path=str(out_path), is_placeholder=True)
