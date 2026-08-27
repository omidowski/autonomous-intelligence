from __future__ import annotations

import os
import re
import tempfile
import textwrap
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageOps

os.environ.setdefault("FFMPEG_BINARY", imageio_ffmpeg.get_ffmpeg_exe())

import moviepy as mpy

from ..media_utils import APPLE_ACCENT, load_font

VERTICAL_SIZE = (1080, 1920)

KEN_BURNS_MAX_ZOOM = 1.12
"""Subtle zoom ceiling (12%) for the pan/zoom effect on each slide - static
images with zero motion read as a cheap slideshow rather than short-form
video; this is the well-known "Ken Burns effect" every competing tool
(Reels/TikTok slideshow makers) applies by default."""


def _sanitize_caption(text: str) -> str:
    """Strip characters the caption font (Arial Bold / DejaVu Sans Bold) can't
    render - emoji and other pictographs mostly live above the Basic Multilingual
    Plane and would otherwise draw as tofu boxes."""
    kept = "".join(ch for ch in text if ch == " " or 0x20 <= ord(ch) <= 0xFFFF)
    return re.sub(r"\s+", " ", kept).strip()


class VideoAssembler:
    """Assembles narrated, captioned slideshow videos from images + audio.

    Pure rendering utility - no LLM calls, so it takes no `llm` argument
    (same shape as `Evaluator`).
    """

    def __init__(self, size: tuple[int, int] = VERTICAL_SIZE, fps: int = 30):
        self.width, self.height = size
        self.fps = fps

    def assemble(
        self,
        *,
        image_paths: list[Path],
        captions: list[str],
        narration_path: Path,
        out_path: Path,
    ) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        audio_clip = mpy.AudioFileClip(str(narration_path))
        total_duration = max(audio_clip.duration, 1.0)
        per_slide = total_duration / max(len(image_paths), 1)

        with tempfile.TemporaryDirectory(prefix="daily-content-frames-") as frames_dir:
            frames = self._render_caption_frames(image_paths, captions, Path(frames_dir))
            clips = [
                self._ken_burns(
                    mpy.ImageClip(str(frame)).with_duration(per_slide),
                    per_slide,
                    zoom_in=(index % 2 == 0),
                )
                for index, frame in enumerate(frames)
            ]
            video = mpy.concatenate_videoclips(clips, method="compose").with_audio(audio_clip)
            video.write_videofile(
                str(out_path),
                fps=self.fps,
                codec="libx264",
                # Without an explicit bitrate, moviepy/ffmpeg's default for
                # this mostly-static-image content landed as low as ~300kbps
                # at 1080x1920 - visibly blocky/blurry. 8000k matches
                # Instagram/TikTok's own recommended upload bitrate for
                # vertical 1080p video.
                bitrate="8000k",
                audio_codec="aac",
                audio_bitrate="192k",
                logger=None,
            )
        return out_path

    def _ken_burns(self, clip: mpy.VideoClip, duration: float, *, zoom_in: bool) -> mpy.VideoClip:
        """Applies a subtle zoom over `clip`'s duration (in from 1.0x to
        `KEN_BURNS_MAX_ZOOM`, or out from `KEN_BURNS_MAX_ZOOM` to 1.0x,
        alternating per slide for variety) and composites the result onto a
        fixed `self.width`x`self.height` canvas so the zoomed-in overflow is
        cropped to the frame instead of changing the output resolution."""
        if duration <= 0:
            return clip

        def scale_at(t: float) -> float:
            progress = min(max(t / duration, 0.0), 1.0)
            span = KEN_BURNS_MAX_ZOOM - 1.0
            return (1.0 + span * progress) if zoom_in else (KEN_BURNS_MAX_ZOOM - span * progress)

        zoomed = clip.resized(scale_at).with_position(("center", "center"))
        return mpy.CompositeVideoClip([zoomed], size=(self.width, self.height)).with_duration(duration)

    def _render_caption_frames(
        self, image_paths: list[Path], captions: list[str], frames_dir: Path
    ) -> list[Path]:
        font = load_font(64)
        frames: list[Path] = []
        pairs = list(zip(image_paths, captions)) or [(image_paths[0], "")]
        for index, (image_path, raw_caption) in enumerate(pairs):
            frame = ImageOps.fit(
                Image.open(image_path).convert("RGB"), (self.width, self.height)
            )
            draw = ImageDraw.Draw(frame)
            caption = _sanitize_caption(raw_caption)
            if caption:
                wrapped = textwrap.fill(caption, width=28)
                bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, align="center", spacing=10)
                text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
                x, y = (self.width - text_width) / 2, self.height - text_height - 160
                # Apple-style translucent rounded caption chip with a thin
                # accent-colored top edge, matching the dashboard's card language.
                pad = 28
                box = [x - pad, y - pad, x + text_width + pad, y + text_height + pad]
                overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
                overlay_draw = ImageDraw.Draw(overlay)
                overlay_draw.rounded_rectangle(box, radius=24, fill=(0, 0, 0, 150))
                overlay_draw.rounded_rectangle(
                    [box[0], box[1], box[2], box[1] + 5], radius=2, fill=(*APPLE_ACCENT, 255)
                )
                frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
                draw = ImageDraw.Draw(frame)
                draw.multiline_text(
                    (self.width / 2, y + text_height / 2),
                    wrapped,
                    font=font,
                    fill=(255, 255, 255),
                    anchor="mm",
                    align="center",
                    spacing=10,
                    stroke_width=3,
                    stroke_fill=(0, 0, 0),
                )
            frame_path = frames_dir / f"frame_{index:02d}.png"
            frame.save(frame_path, format="PNG")
            frames.append(frame_path)
        return frames
