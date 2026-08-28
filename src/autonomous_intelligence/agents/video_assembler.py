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
from moviepy import vfx

from ..media_utils import APPLE_ACCENT, load_font

VERTICAL_SIZE = (1080, 1920)

KEN_BURNS_MAX_ZOOM = 1.12
"""Subtle zoom ceiling (12%) for the pan/zoom effect on each slide - static
images with zero motion read as a cheap slideshow rather than short-form
video; this is the well-known "Ken Burns effect" every competing tool
(Reels/TikTok slideshow makers) applies by default."""

KEN_BURNS_PAN_FRACTION = 0.5
"""How much of the zoom's spare overflow to actually travel across. Zooming
to `KEN_BURNS_MAX_ZOOM` hides `width*(zoom-1)` pixels outside the frame;
drifting across half of that reads as a deliberate camera move, while
staying comfortably inside the image so no edge is ever exposed. Pure zoom
with no lateral drift is what makes cheap slideshows look mechanical."""

CROSSFADE_SECONDS = 0.45
"""Dissolve between slides. Hard cuts on still images look like a
PowerPoint advance; a sub-half-second dissolve is short enough to keep the
pace of short-form video while removing the jolt."""

OPENING_FADE_SECONDS = 0.4
CLOSING_FADE_SECONDS = 0.6

CAPTION_CHUNK_MAX_CHARS = 52
"""Captions are revealed a phrase at a time rather than as one static block
for the whole slide. A whole paragraph pinned on screen is the single
clearest "auto-generated" tell in this format - every native short-form
video paces its captions with the voice. ~52 characters is about two
rendered lines at the caption size, which is roughly one spoken breath."""

MIN_CHUNK_SECONDS = 0.9
"""Floor on how briefly a caption phrase may be shown. Without it, a slide
with many short phrases would flash text faster than it can be read."""


def _sanitize_caption(text: str) -> str:
    """Strip characters the caption font (Arial Bold / DejaVu Sans Bold) can't
    render - emoji and other pictographs mostly live above the Basic Multilingual
    Plane and would otherwise draw as tofu boxes."""
    kept = "".join(ch for ch in text if ch == " " or 0x20 <= ord(ch) <= 0xFFFF)
    return re.sub(r"\s+", " ", kept).strip()


def _chunk_caption(text: str, max_chars: int = CAPTION_CHUNK_MAX_CHARS) -> list[str]:
    """Split a slide's caption into phrase-sized pieces for progressive
    reveal. Splits on sentence boundaries first so a phrase break lands
    where the voice would pause, then packs whatever is left by word so no
    piece exceeds `max_chars`. Returns `[""]` for empty text so a slide
    without a caption still yields exactly one (captionless) frame."""
    text = _sanitize_caption(text)
    if not text:
        return [""]

    chunks: list[str] = []
    for sentence in re.split(r"(?<=[.!?:;])\s+", text):
        words = sentence.split()
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks or [""]


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
            clips = self._build_slide_clips(image_paths, captions, per_slide, Path(frames_dir))

            # `padding` overlaps each clip with its predecessor by the
            # crossfade length, so the dissolve happens *between* slides
            # instead of appending dead time. The overlap has to be paid
            # back or the video would end before the narration: each slide
            # is rendered `CROSSFADE_SECONDS` longer above.
            video = mpy.concatenate_videoclips(
                clips, method="compose", padding=-CROSSFADE_SECONDS
            )
            video = video.with_effects(
                [vfx.FadeIn(OPENING_FADE_SECONDS), vfx.FadeOut(CLOSING_FADE_SECONDS)]
            )
            video = video.with_duration(total_duration).with_audio(audio_clip)
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

    def _build_slide_clips(
        self,
        image_paths: list[Path],
        captions: list[str],
        per_slide: float,
        frames_dir: Path,
    ) -> list[mpy.VideoClip]:
        """One clip per slide, each already carrying its own progressive
        captions and its own continuous camera move.

        Captions are cut into phrases *within* a slide, but the Ken Burns
        move is computed across the whole slide and each phrase clip is
        handed only its slice of that move - so the camera drifts smoothly
        through the caption changes instead of snapping back on every one.
        """
        pairs = list(zip(image_paths, captions))
        if not pairs and image_paths:
            pairs = [(image_paths[0], "")]

        clips: list[mpy.VideoClip] = []
        for slide_index, (image_path, raw_caption) in enumerate(pairs):
            base = ImageOps.fit(Image.open(image_path).convert("RGB"), (self.width, self.height))

            chunks = _chunk_caption(raw_caption)
            # Never show a phrase for less than MIN_CHUNK_SECONDS: on a
            # short slide that means showing fewer, longer phrases rather
            # than flashing all of them unreadably.
            max_chunks = max(int(per_slide // MIN_CHUNK_SECONDS), 1)
            if len(chunks) > max_chunks:
                chunks = self._merge_to(chunks, max_chunks)

            # Every slide but the first is overlapped into its predecessor
            # by CROSSFADE_SECONDS, so it must be that much longer to keep
            # the slide's visible time equal to `per_slide`.
            slide_duration = per_slide + (CROSSFADE_SECONDS if slide_index else 0.0)
            chunk_duration = slide_duration / len(chunks)

            font = load_font(64)
            chunk_clips: list[mpy.VideoClip] = []
            for chunk_index, chunk in enumerate(chunks):
                frame_path = frames_dir / f"slide_{slide_index:02d}_{chunk_index:02d}.png"
                self._render_caption_frame(base, chunk, font, frame_path)
                chunk_clips.append(
                    self._ken_burns(
                        mpy.ImageClip(str(frame_path)).with_duration(chunk_duration),
                        chunk_duration,
                        slide_index=slide_index,
                        progress_from=chunk_index / len(chunks),
                        progress_to=(chunk_index + 1) / len(chunks),
                    )
                )

            slide = mpy.concatenate_videoclips(chunk_clips, method="compose")
            if slide_index:
                slide = slide.with_effects([vfx.CrossFadeIn(CROSSFADE_SECONDS)])
            clips.append(slide)
        return clips

    @staticmethod
    def _merge_to(chunks: list[str], target: int) -> list[str]:
        """Evenly redistribute `chunks` into at most `target` pieces by
        joining neighbours - used when a slide is too short to give every
        phrase its own readable moment."""
        per_group = -(-len(chunks) // target)  # ceil
        return [
            " ".join(chunks[i : i + per_group]) for i in range(0, len(chunks), per_group)
        ]

    def _ken_burns(
        self,
        clip: mpy.VideoClip,
        duration: float,
        *,
        slide_index: int,
        progress_from: float = 0.0,
        progress_to: float = 1.0,
    ) -> mpy.VideoClip:
        """Applies a continuous zoom *and* lateral drift over `clip`, then
        composites onto a fixed `self.width`x`self.height` canvas so the
        overflow is cropped to frame instead of changing the output
        resolution.

        `progress_from`/`progress_to` are this clip's slice of its slide's
        overall move, so a slide split into several caption phrases still
        reads as one unbroken camera movement.

        Direction alternates by `slide_index` (zoom in/out, and one of four
        drift directions) so consecutive slides don't move identically -
        identical motion on every slide is as mechanical-looking as no
        motion at all.
        """
        if duration <= 0:
            return clip

        zoom_in = slide_index % 2 == 0
        # 4 drift directions cycled independently of the zoom direction, so
        # the in/out and the drift don't stay locked in the same pairing.
        drift_x, drift_y = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)][slide_index % 4]
        span = KEN_BURNS_MAX_ZOOM - 1.0

        def progress_at(t: float) -> float:
            local = min(max(t / duration, 0.0), 1.0)
            return progress_from + (progress_to - progress_from) * local

        def scale_at(t: float) -> float:
            progress = progress_at(t)
            return (1.0 + span * progress) if zoom_in else (KEN_BURNS_MAX_ZOOM - span * progress)

        def position_at(t: float) -> tuple[float, float]:
            scale = scale_at(t)
            # Spare pixels hidden outside the frame at this zoom level; half
            # of it is available in each direction from centre.
            slack_x = self.width * (scale - 1.0) / 2.0
            slack_y = self.height * (scale - 1.0) / 2.0
            # Travel from one side of the slack to the other across the
            # slide, scaled down by KEN_BURNS_PAN_FRACTION so the drift
            # never reaches the image edge.
            travel = (progress_at(t) - 0.5) * 2.0 * KEN_BURNS_PAN_FRACTION
            centre_x = (self.width - self.width * scale) / 2.0
            centre_y = (self.height - self.height * scale) / 2.0
            return (
                centre_x + drift_x * travel * slack_x,
                centre_y + drift_y * travel * slack_y,
            )

        zoomed = clip.resized(scale_at).with_position(position_at)
        return mpy.CompositeVideoClip([zoomed], size=(self.width, self.height)).with_duration(
            duration
        )

    def _render_caption_frame(
        self, base: Image.Image, caption: str, font, out_path: Path
    ) -> Path:
        """Draw one caption phrase onto a copy of the slide's fitted image.

        Takes an already-fitted `base` rather than a path because every
        phrase of a slide shares the same background - re-opening and
        re-fitting the source image per phrase would be wasted work.
        """
        frame = base.copy()
        if caption:
            draw = ImageDraw.Draw(frame)
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
        frame.save(out_path, format="PNG")
        return out_path
