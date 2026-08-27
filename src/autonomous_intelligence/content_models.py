from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ContentStatus(str, Enum):
    """Review-workflow status for a generated `ContentBundle`. Lives in
    SQLite (`db.content_bundle_status`), not as a field on `ContentBundle`
    itself - see `review.py` for why: `content.json`/`manifest.json` are
    fully overwritten on every pipeline run, so mutable review state can't
    live there without being clobbered by re-runs."""

    draft = "draft"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"


class TrendItem(BaseModel):
    rank: int = Field(ge=1)
    title: str
    summary: str
    category: str
    source_urls: list[str] = Field(default_factory=list)
    search_query: str


class SocialPost(BaseModel):
    platform: Literal[
        "x", "instagram", "facebook", "linkedin", "tiktok", "threads", "bluesky", "pinterest",
        "reddit", "youtube",
    ]
    text: str
    hashtags: list[str] = Field(default_factory=list)
    variant: Literal["A", "B"] | None = None
    """A/B copy variant for the same platform - `ContentWriterAgent` always
    generates both an "A" and a "B" post per platform (two distinct
    hooks/angles), so `ContentBundle.social_posts` normally has 2 entries
    per platform. `None` only for bundles written before this field
    existed. See `db.get_selected_variants()`/`set_selected_variant()` for
    which one a company has picked to actually publish - "A" is the
    default when nothing has been explicitly picked."""


class ShortScript(BaseModel):
    hook: str
    beats: list[str]
    cta: str
    est_duration_seconds: int = Field(ge=10, le=120)


class StorySlide(BaseModel):
    slide_number: int = Field(ge=1)
    text: str
    image_prompt: str


class StoryContent(BaseModel):
    slides: list[StorySlide]


class PodcastScript(BaseModel):
    title: str
    intro: str
    segments: list[str]
    outro: str
    est_duration_minutes: int = Field(ge=1, le=30)


class ShotListItem(BaseModel):
    shot_number: int = Field(ge=1)
    description: str
    duration_seconds: int = Field(ge=1, le=60)
    on_screen_text: str | None = None


class VideoScript(BaseModel):
    title: str
    shots: list[ShotListItem]


class GeneratedAsset(BaseModel):
    kind: Literal["image", "audio", "video"]
    path: str
    is_placeholder: bool = False
    source: Literal["generated", "stock", "fallback"] | None = None
    """Which provider actually produced this asset when it's not a
    placeholder: "stock" for a real Pexels photo (`stock_photos.py`,
    images only), "fallback" for ElevenLabs narration
    (`text_to_speech.py`, audio only) or Leonardo.ai imagery
    (`leonardo_images.py`, images only) when the primary OpenAI provider
    was unavailable, "generated" for a real Runway AI video clip
    (`runway_video.py`, video only - an opt-in upgrade over the moviepy
    slideshow assembly, not a fallback-on-failure). `None` for the
    primary-provider path, the moviepy slideshow assembly, and
    placeholders."""
    attribution: str | None = None
    """Photographer credit, set only when `source == "stock"` (Pexels asks
    for attribution, even though it isn't strictly required by their
    license)."""
    is_silent: bool = False
    """Video-only: true when the video has no audible narration track, even
    though the video content itself is real (not a placeholder) - distinct
    from `is_placeholder`, which means there's no real video content at
    all. Set on the moviepy slideshow path when narration synthesis fell
    back to a silent placeholder WAV, and on the Runway/Kling image-to-
    video upgrades (which produce silent b-roll, no narration mixed in).
    Never true for HeyGen avatar videos, which carry their own generated
    voice track. See `daily_content_orchestrator.py::_render_assets()`."""


class ContentBundle(BaseModel):
    trend: TrendItem
    social_posts: list[SocialPost]
    short_script: ShortScript
    story: StoryContent
    podcast_script: PodcastScript
    video_script: VideoScript
    assets: list[GeneratedAsset] = Field(default_factory=list)


class DailyContentReport(BaseModel):
    date: str
    trends: list[TrendItem]
    bundles: list[ContentBundle]
    output_dir: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mode: Literal["live", "codex", "openrouter", "demo"]
