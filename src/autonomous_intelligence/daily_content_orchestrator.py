from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path

from . import db
from .agents.audio_narrator import AudioNarratorAgent
from .agents.content_writer import ContentWriterAgent
from .agents.image_generator import ImageGeneratorAgent
from .agents.trend_scanner import TrendScannerAgent
from .agents.video_assembler import VideoAssembler
from .brand_assets import apply_logo_watermark, asset_file_path
from .config import Settings, get_settings
from .content_models import ContentBundle, DailyContentReport, GeneratedAsset, TrendItem
from .heygen_video import HeyGenVideoError, generate_avatar_video
from .kling_video import KlingVideoError
from .kling_video import generate_video_from_image as kling_generate_video
from .llm import LLMProvider
from .runway_video import RunwayVideoError
from .runway_video import generate_video_from_image as runway_generate_video
from .tenancy import tenant_output_dir


class DailyContentOrchestrator:
    """Coordinates scan trends -> write content -> render images/audio -> assemble video."""

    def __init__(self, settings: Settings | None = None, video_assembler: VideoAssembler | None = None):
        self.settings = settings or get_settings()
        self.llm = LLMProvider(self.settings)
        self.scanner = TrendScannerAgent(self.llm)
        self.writer = ContentWriterAgent(self.llm)
        self.image_agent = ImageGeneratorAgent(self.llm, self.settings)
        self.audio_agent = AudioNarratorAgent(self.llm, self.settings)
        self.video_assembler = video_assembler or VideoAssembler()

    def _resolve_logo_path(self, company_slug: str) -> Path | None:
        """Looks up the company's logo brand asset (if any) and resolves it
        to a file path for `apply_logo_watermark()` - `None` if the company
        has no logo uploaded, or its row references a company_id that
        doesn't match a real company (shouldn't happen, but fail safe)."""
        company = db.get_company_by_slug(self.settings.database_path, company_slug)
        if company is None:
            return None
        logo = db.get_logo_asset(self.settings.database_path, company["id"])
        if logo is None:
            return None
        path = asset_file_path(self.settings, company_slug, logo["id"], logo["filename"])
        return path if path.is_file() else None

    def run(
        self,
        *,
        date: datetime | None = None,
        company_slug: str = "default",
        brand_voice: str | None = None,
        content_model: str | None = None,
        language: str | None = None,
        target_duration_seconds: int | None = None,
        image_size: str | None = None,
        image_quality: str | None = None,
    ) -> DailyContentReport:
        run_date = date or datetime.now(UTC)
        date_str = run_date.strftime("%Y-%m-%d")
        output_dir = tenant_output_dir(self.settings, company_slug, date_str)
        output_dir.mkdir(parents=True, exist_ok=True)

        logo_path = self._resolve_logo_path(company_slug)
        trends = self.scanner.run(self.settings.daily_trends_count)
        bundles = []
        for index, trend in enumerate(trends, start=1):
            trend_dir = output_dir / f"trend-{index}"
            bundle = self.writer.run(
                trend,
                brand_voice=brand_voice,
                content_model=content_model,
                language=language,
                target_duration_seconds=target_duration_seconds,
            )
            bundle.assets = self._render_assets(
                bundle,
                trend_dir,
                image_size=image_size,
                image_quality=image_quality,
                logo_path=logo_path,
            )
            (trend_dir / "content.json").write_text(
                bundle.model_dump_json(indent=2), encoding="utf-8"
            )
            bundles.append(bundle)

        report = DailyContentReport(
            date=date_str,
            trends=trends,
            bundles=bundles,
            output_dir=str(output_dir),
            mode=self.llm.mode,
        )
        (output_dir / "manifest.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (output_dir / "daily_report.md").write_text(self._render_markdown(report), encoding="utf-8")
        return report

    def run_custom_topic(
        self,
        topic: str,
        *,
        company_slug: str = "default",
        brand_voice: str | None = None,
        content_model: str | None = None,
        language: str | None = None,
        target_duration_seconds: int | None = None,
        image_size: str | None = None,
        image_quality: str | None = None,
    ) -> DailyContentReport:
        """Generates a content bundle for an arbitrary, user-supplied topic
        instead of a `TrendScannerAgent`-discovered news trend - same
        writer/asset pipeline as `run()`, wrapped in the same
        `DailyContentReport` shape (one synthetic `TrendItem`, one bundle) so
        the API layer, review workflow, and frontend rendering are shared
        unchanged with the daily-trends flow (see `api.py`'s
        `/custom-content/*` routes, which are thin wrappers around this).

        `run_id` (returned as `DailyContentReport.date`) is a `"custom-"`-
        prefixed, timestamp-based identifier rather than a calendar date -
        distinguishes custom runs from daily-trend dates in the shared
        `tenant_output_dir()` layout without colliding with the
        `_DATE_RE`-validated `/daily-content/{date}` route.
        """
        run_id = f"custom-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        output_dir = tenant_output_dir(self.settings, company_slug, run_id)
        output_dir.mkdir(parents=True, exist_ok=True)

        trend = TrendItem(
            rank=1,
            title=topic.strip(),
            summary=topic.strip(),
            category="custom",
            source_urls=[],
            search_query=topic.strip(),
        )
        trend_dir = output_dir / "trend-1"
        bundle = self.writer.run(
            trend,
            brand_voice=brand_voice,
            content_model=content_model,
            language=language,
            target_duration_seconds=target_duration_seconds,
        )
        bundle.assets = self._render_assets(
            bundle,
            trend_dir,
            image_size=image_size,
            image_quality=image_quality,
            logo_path=self._resolve_logo_path(company_slug),
        )
        (trend_dir / "content.json").write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

        report = DailyContentReport(
            date=run_id,
            trends=[trend],
            bundles=[bundle],
            output_dir=str(output_dir),
            mode=self.llm.mode,
        )
        (output_dir / "manifest.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (output_dir / "daily_report.md").write_text(self._render_markdown(report), encoding="utf-8")
        return report

    def _render_assets(
        self,
        bundle: ContentBundle,
        trend_dir: Path,
        *,
        image_size: str | None = None,
        image_quality: str | None = None,
        logo_path: Path | None = None,
    ) -> list[GeneratedAsset]:
        images_dir = trend_dir / "images"
        audio_dir = trend_dir / "audio"
        video_dir = trend_dir / "video"

        def _generate_image(prompt: str, out_path: Path) -> GeneratedAsset:
            asset = self.image_agent.generate(
                prompt,
                out_path,
                search_query=f"{bundle.trend.title} {bundle.trend.category}",
                image_size=image_size,
                image_quality=image_quality,
            )
            # Only watermark real generated/stock imagery, never the
            # deterministic placeholder (a logo on a "PLACEHOLDER" text box
            # would be actively misleading, not branding).
            if logo_path is not None and not asset.is_placeholder:
                apply_logo_watermark(Path(asset.path), logo_path)
            return asset

        assets: list[GeneratedAsset] = [
            _generate_image(
                f"Editorial cover image for a news story about: {bundle.trend.title}. "
                f"{bundle.trend.summary} "
                "Apple-style visual: clean minimalist studio composition, soft gradient "
                "backdrop, premium directional lighting, generous negative space, no text "
                "baked into the image - the restrained premium look of an apple.com product "
                "photo.",
                images_dir / "cover.png",
            )
        ]

        slide_paths: list[Path] = []
        for slide in bundle.story.slides:
            asset = _generate_image(slide.image_prompt, images_dir / f"slide-{slide.slide_number}.png")
            assets.append(asset)
            slide_paths.append(Path(asset.path))

        narration_text = " ".join(
            [bundle.short_script.hook, *bundle.short_script.beats, bundle.short_script.cta]
        )
        narration = self.audio_agent.narrate(narration_text, audio_dir)
        assets.append(narration)

        upgrade = self._generate_video_upgrade(bundle, narration_text, Path(assets[0].path))
        if upgrade is not None:
            video_bytes, is_silent = upgrade
            video_dir.mkdir(parents=True, exist_ok=True)
            video_path = video_dir / "short.mp4"
            video_path.write_bytes(video_bytes)
            assets.append(
                GeneratedAsset(
                    kind="video",
                    path=str(video_path),
                    is_placeholder=False,
                    source="generated",
                    is_silent=is_silent,
                )
            )
            return assets

        try:
            self.video_assembler.assemble(
                image_paths=slide_paths or [Path(assets[0].path)],
                captions=[slide.text for slide in bundle.story.slides] or [bundle.trend.title],
                narration_path=Path(narration.path),
                out_path=video_dir / "short.mp4",
            )
            assets.append(
                GeneratedAsset(
                    kind="video",
                    path=str(video_dir / "short.mp4"),
                    is_placeholder=False,
                    is_silent=narration.is_placeholder,
                )
            )
        except Exception:  # noqa: BLE001 - video rendering is best-effort, never fail the daily run
            assets.append(GeneratedAsset(kind="video", path="", is_placeholder=True))

        return assets

    def _generate_video_upgrade(
        self, bundle: ContentBundle, narration_text: str, cover_path: Path
    ) -> tuple[bytes, bool] | None:
        """Tries each configured "quality upgrade" video provider in
        priority order, returning the first one that succeeds - `None` if
        none are configured or all fail, in which case the caller falls
        back to the moviepy slideshow assembly.

        HeyGen is tried first when configured: a talking AI avatar is a
        fundamentally different (and more differentiated) output than
        motion added to a still image, so it takes priority over the two
        image-to-video upgrades. Between those, Runway is tried before
        Kling only because it was integrated (and its docs verified)
        first - not a quality judgment; see `kling_video.py`'s caveat
        docstring about its unverified API shape.

        The second element of the returned tuple is `is_silent` (see
        `GeneratedAsset.is_silent`): HeyGen bakes in its own narration
        voice track, so it's never silent; Runway/Kling produce b-roll
        motion with no audio mixed in at all, so they always are."""
        if self.settings.heygen_api_key and self.settings.heygen_avatar_id:
            try:
                kwargs = {"voice_id": self.settings.heygen_voice_id} if self.settings.heygen_voice_id else {}
                video_bytes = generate_avatar_video(
                    narration_text,
                    api_key=self.settings.heygen_api_key,
                    avatar_id=self.settings.heygen_avatar_id,
                    **kwargs,
                )
                return video_bytes, False
            except HeyGenVideoError:
                pass  # fall through to the next configured upgrade

        if self.settings.runway_api_key:
            try:
                video_bytes = runway_generate_video(
                    cover_path,
                    f"Subtle, cinematic motion for an editorial video about: {bundle.trend.title}.",
                    api_key=self.settings.runway_api_key,
                )
                return video_bytes, True
            except RunwayVideoError:
                pass  # fall through to the next configured upgrade

        if self.settings.kling_access_key and self.settings.kling_secret_key:
            try:
                video_bytes = kling_generate_video(
                    cover_path,
                    f"Subtle, cinematic motion for an editorial video about: {bundle.trend.title}.",
                    access_key=self.settings.kling_access_key,
                    secret_key=self.settings.kling_secret_key,
                )
                return video_bytes, True
            except KlingVideoError:
                pass  # fall through to the moviepy slideshow assembly

        return None

    @staticmethod
    def _render_markdown(report: DailyContentReport) -> str:
        lines = [f"# Daily Content Report — {report.date}", ""]
        for bundle in report.bundles:
            lines += [f"## {bundle.trend.title}", "", bundle.trend.summary, ""]
            # Only variant A - the markdown report is a quick-glance summary,
            # not the review UI (which shows both variants; see
            # `db.get_selected_variants()`).
            lines += [
                f"- **{post.platform}**: {post.text}"
                for post in bundle.social_posts
                if post.variant in (None, "A")
            ]
            lines.append("")
        return "\n".join(lines)
