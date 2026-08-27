import wave
from io import BytesIO

from PIL import Image

from autonomous_intelligence import leonardo_images, stock_photos, text_to_speech
from autonomous_intelligence.agents.audio_narrator import AudioNarratorAgent
from autonomous_intelligence.agents.image_generator import ImageGeneratorAgent
from autonomous_intelligence.agents.video_assembler import VideoAssembler
from autonomous_intelligence.config import Settings
from autonomous_intelligence.llm import MediaUnavailableError


class _UnavailableLLM:
    def image(self, prompt: str, *, size: str | None = None, quality: str | None = None) -> bytes:
        raise MediaUnavailableError("no access")

    def speech(self, text: str) -> bytes:
        raise MediaUnavailableError("no access")


def test_image_generator_falls_back_to_placeholder(tmp_path):
    agent = ImageGeneratorAgent(_UnavailableLLM())
    out_path = tmp_path / "cover.png"

    asset = agent.generate("a red circle on white background", out_path)

    assert asset.is_placeholder is True
    assert asset.source is None
    assert out_path.is_file()
    with Image.open(out_path) as image:
        assert image.size == (1024, 1792)


def test_image_generator_falls_back_to_placeholder_when_no_pexels_key(tmp_path):
    settings = Settings(pexels_api_key=None, leonardo_api_key=None)
    agent = ImageGeneratorAgent(_UnavailableLLM(), settings)
    out_path = tmp_path / "cover.png"

    asset = agent.generate("a red circle on white background", out_path)

    assert asset.is_placeholder is True


def test_image_generator_uses_stock_photo_when_pexels_key_configured(tmp_path, monkeypatch):
    def fake_search_photo(query, *, api_key, orientation="portrait"):
        assert query == "custom search query"
        assert api_key == "fake-key"
        return stock_photos.StockPhoto(
            image_bytes=b"fake-image-bytes",
            photographer="Jane Doe",
            photo_url="https://www.pexels.com/photo/1/",
        )

    monkeypatch.setattr(
        "autonomous_intelligence.agents.image_generator.search_photo", fake_search_photo
    )

    settings = Settings(pexels_api_key="fake-key", leonardo_api_key=None)
    agent = ImageGeneratorAgent(_UnavailableLLM(), settings)
    out_path = tmp_path / "cover.png"

    asset = agent.generate(
        "a red circle on white background", out_path, search_query="custom search query"
    )

    assert asset.is_placeholder is False
    assert asset.source == "stock"
    assert asset.attribution == "Photo by Jane Doe on Pexels"
    assert out_path.read_bytes() == b"fake-image-bytes"


def test_image_generator_falls_back_to_placeholder_when_stock_search_fails(tmp_path, monkeypatch):
    def failing_search_photo(query, *, api_key, orientation="portrait"):
        raise stock_photos.StockPhotoError("no results")

    monkeypatch.setattr(
        "autonomous_intelligence.agents.image_generator.search_photo", failing_search_photo
    )

    settings = Settings(pexels_api_key="fake-key", leonardo_api_key=None)
    agent = ImageGeneratorAgent(_UnavailableLLM(), settings)
    out_path = tmp_path / "cover.png"

    asset = agent.generate("a red circle on white background", out_path)

    assert asset.is_placeholder is True
    assert asset.source is None


def test_image_generator_uses_leonardo_when_key_configured(tmp_path, monkeypatch):
    def fake_generate_image(prompt, *, api_key, model_id=leonardo_images.DEFAULT_MODEL_ID, width=1024, height=1536):
        assert prompt == "a red circle on white background"
        assert api_key == "fake-key"
        return b"fake-leonardo-bytes"

    monkeypatch.setattr(
        "autonomous_intelligence.agents.image_generator.generate_image", fake_generate_image
    )

    settings = Settings(leonardo_api_key="fake-key", pexels_api_key=None)
    agent = ImageGeneratorAgent(_UnavailableLLM(), settings)
    out_path = tmp_path / "cover.png"

    asset = agent.generate("a red circle on white background", out_path)

    assert asset.is_placeholder is False
    assert asset.source == "fallback"
    assert out_path.read_bytes() == b"fake-leonardo-bytes"


def test_image_generator_falls_back_to_pexels_when_leonardo_fails(tmp_path, monkeypatch):
    def failing_generate_image(prompt, *, api_key, model_id=leonardo_images.DEFAULT_MODEL_ID, width=1024, height=1536):
        raise leonardo_images.LeonardoImageError("moderation rejected")

    def fake_search_photo(query, *, api_key, orientation="portrait"):
        return stock_photos.StockPhoto(
            image_bytes=b"fake-pexels-bytes", photographer="Jane Doe", photo_url="https://x"
        )

    monkeypatch.setattr(
        "autonomous_intelligence.agents.image_generator.generate_image", failing_generate_image
    )
    monkeypatch.setattr(
        "autonomous_intelligence.agents.image_generator.search_photo", fake_search_photo
    )

    settings = Settings(leonardo_api_key="fake-key", pexels_api_key="fake-key")
    agent = ImageGeneratorAgent(_UnavailableLLM(), settings)
    out_path = tmp_path / "cover.png"

    asset = agent.generate("a red circle on white background", out_path)

    assert asset.source == "stock"
    assert out_path.read_bytes() == b"fake-pexels-bytes"


def test_audio_narrator_falls_back_to_silent_wav(tmp_path):
    agent = AudioNarratorAgent(_UnavailableLLM())

    asset = agent.narrate("This is a short narration script for testing.", tmp_path)

    assert asset.is_placeholder is True
    assert asset.source is None
    out_path = tmp_path / "narration.wav"
    assert out_path.is_file()
    with wave.open(str(out_path), "rb") as wav_file:
        assert wav_file.getnframes() > 0


def test_audio_narrator_falls_back_to_silent_wav_when_no_elevenlabs_key(tmp_path):
    settings = Settings(elevenlabs_api_key=None)
    agent = AudioNarratorAgent(_UnavailableLLM(), settings)

    asset = agent.narrate("This is a short narration script for testing.", tmp_path)

    assert asset.is_placeholder is True


def test_audio_narrator_uses_elevenlabs_when_key_configured(tmp_path, monkeypatch):
    def fake_synthesize_speech(text, *, api_key, voice_id=text_to_speech.DEFAULT_VOICE_ID):
        assert text == "This is a short narration script for testing."
        assert api_key == "fake-key"
        buffer = BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(text_to_speech.SAMPLE_RATE)
            wav_file.writeframes(b"\x01\x00" * 10)
        return buffer.getvalue()

    monkeypatch.setattr(
        "autonomous_intelligence.agents.audio_narrator.synthesize_speech", fake_synthesize_speech
    )

    settings = Settings(elevenlabs_api_key="fake-key")
    agent = AudioNarratorAgent(_UnavailableLLM(), settings)

    asset = agent.narrate("This is a short narration script for testing.", tmp_path)

    assert asset.is_placeholder is False
    assert asset.source == "fallback"
    out_path = tmp_path / "narration.wav"
    with wave.open(str(out_path), "rb") as wav_file:
        assert wav_file.getnframes() == 10


def test_audio_narrator_falls_back_to_silent_wav_when_elevenlabs_fails(tmp_path, monkeypatch):
    def failing_synthesize_speech(text, *, api_key, voice_id=text_to_speech.DEFAULT_VOICE_ID):
        raise text_to_speech.SpeechSynthesisError("no access")

    monkeypatch.setattr(
        "autonomous_intelligence.agents.audio_narrator.synthesize_speech", failing_synthesize_speech
    )

    settings = Settings(elevenlabs_api_key="fake-key")
    agent = AudioNarratorAgent(_UnavailableLLM(), settings)

    asset = agent.narrate("This is a short narration script for testing.", tmp_path)

    assert asset.is_placeholder is True
    assert asset.source is None


def test_video_assembler_writes_playable_mp4(tmp_path):
    image_paths = []
    for i in range(2):
        path = tmp_path / f"slide-{i}.png"
        Image.new("RGB", (64, 64), color=(10 * i, 20, 30)).save(path)
        image_paths.append(path)

    narration_path = tmp_path / "narration.wav"
    with wave.open(str(narration_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(b"\x00\x00" * 8000)  # 1 second of silence

    out_path = tmp_path / "short.mp4"
    assembler = VideoAssembler(size=(96, 170), fps=6)

    result = assembler.assemble(
        image_paths=image_paths,
        captions=["First slide", "Second slide"],
        narration_path=narration_path,
        out_path=out_path,
    )

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0
