from __future__ import annotations

import textwrap
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CAPTION_FONT_CANDIDATES = [
    "/System/Library/Fonts/SFNS.ttf",  # San Francisco (Apple system font), macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # macOS fallback
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # common Linux (cloud routine)
]

# Apple system color palette (dark mode), reused across generated placeholder
# assets and video captions so everything the pipeline renders matches the
# dashboard's Apple-style look.
APPLE_BG = (0, 0, 0)
APPLE_CARD_BG = (28, 28, 30)
APPLE_TEXT = (245, 245, 247)
APPLE_TEXT_SECONDARY = (152, 152, 157)
APPLE_ACCENT = (255, 69, 58)  # system red, matches the dashboard's --accent


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in CAPTION_FONT_CANDIDATES:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def render_placeholder_image(
    prompt: str, out_path: Path, size: tuple[int, int] = (1024, 1792)
) -> None:
    """Deterministic local stand-in for a real AI-generated image, used when
    image generation is unavailable (demo mode, missing model access, etc.)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    image = Image.new("RGB", size, color=APPLE_BG)
    draw = ImageDraw.Draw(image)

    label_font = load_font(32)
    body_font = load_font(44)

    # Apple-style "chip" for the placeholder label, same shape language as the
    # dashboard's status pills.
    chip_text = "PLACEHOLDER"
    chip_bbox = draw.textbbox((0, 0), chip_text, font=label_font)
    chip_w, chip_h = chip_bbox[2] - chip_bbox[0], chip_bbox[3] - chip_bbox[1]
    pad_x, pad_y = 20, 12
    draw.rounded_rectangle(
        [48, 48, 48 + chip_w + pad_x * 2, 48 + chip_h + pad_y * 2],
        radius=(chip_h + pad_y * 2) / 2,
        fill=APPLE_ACCENT,
    )
    draw.text((48 + pad_x, 48 + pad_y), chip_text, font=label_font, fill=(255, 255, 255))
    draw.text(
        (48, 48 + chip_h + pad_y * 2 + 16),
        "Enable image model access to render this for real.",
        font=label_font,
        fill=APPLE_TEXT_SECONDARY,
    )

    wrapped = textwrap.fill(prompt, width=28)
    draw.multiline_text(
        (width / 2, height / 2),
        wrapped,
        font=body_font,
        fill=APPLE_TEXT,
        anchor="mm",
        align="center",
        spacing=14,
    )

    image.save(out_path, format="PNG")


def render_silent_wav(
    text: str, out_path: Path, *, words_per_minute: int = 150, sample_rate: int = 24000
) -> None:
    """Silent stand-in audio track, sized to roughly match how long the real
    narration would take to speak, used when speech synthesis is unavailable."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    word_count = max(len(text.split()), 1)
    duration_seconds = max(word_count / words_per_minute * 60.0, 1.0)
    frame_count = int(duration_seconds * sample_rate)

    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frame_count)
