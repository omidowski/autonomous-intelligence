"""Per-company brand asset storage (currently: a single logo) and the
watermark step that overlays it on generated images - see
`daily_content_orchestrator.py::_render_assets()`, which calls
`apply_logo_watermark()` on every generated image when
`db.get_logo_asset()` finds one for the company.

Files live on disk under `settings.brand_assets_dir`, DB rows in
`db.brand_assets` track metadata (filename/content_type/is_logo) - same
split as the daily-content pipeline (DB for queryable state, filesystem for
the actual bytes).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .config import Settings

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

WATERMARK_WIDTH_FRACTION = 0.16
"""Logo width as a fraction of the target image's width - small enough to
stay a corner watermark, not dominate the frame."""

WATERMARK_MARGIN_FRACTION = 0.04


def company_assets_dir(settings: Settings, company_slug: str) -> Path:
    return Path(settings.brand_assets_dir) / company_slug


def asset_file_path(settings: Settings, company_slug: str, asset_id: int, filename: str) -> Path:
    return company_assets_dir(settings, company_slug) / f"{asset_id}_{filename}"


def save_asset_file(
    settings: Settings, company_slug: str, asset_id: int, filename: str, content: bytes
) -> Path:
    path = asset_file_path(settings, company_slug, asset_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def delete_asset_file(settings: Settings, company_slug: str, asset_id: int, filename: str) -> None:
    path = asset_file_path(settings, company_slug, asset_id, filename)
    path.unlink(missing_ok=True)


def apply_logo_watermark(image_path: Path, logo_path: Path) -> None:
    """Overlays `logo_path` in the bottom-right corner of `image_path`,
    scaled to `WATERMARK_WIDTH_FRACTION` of the image's width, and
    overwrites `image_path` in place. Silently no-ops if either file can't
    be opened as an image - a broken watermark should never break the
    content pipeline (mirrors the "never fail the daily run" video-assembly
    fallback in `daily_content_orchestrator.py`)."""
    try:
        with Image.open(image_path) as base_image:
            base = base_image.convert("RGBA")
            with Image.open(logo_path) as logo_image:
                logo = logo_image.convert("RGBA")

                target_width = max(int(base.width * WATERMARK_WIDTH_FRACTION), 1)
                scale = target_width / logo.width
                target_height = max(int(logo.height * scale), 1)
                logo = logo.resize((target_width, target_height), Image.Resampling.LANCZOS)

                margin = int(base.width * WATERMARK_MARGIN_FRACTION)
                position = (base.width - logo.width - margin, base.height - logo.height - margin)
                base.alpha_composite(logo, dest=position)
                base.convert("RGB").save(image_path, format="PNG")
    except (OSError, ValueError):
        pass


__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "MAX_UPLOAD_BYTES",
    "apply_logo_watermark",
    "asset_file_path",
    "company_assets_dir",
    "delete_asset_file",
    "save_asset_file",
]
