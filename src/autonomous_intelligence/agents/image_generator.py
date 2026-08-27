from pathlib import Path

from ..config import Settings
from ..content_models import GeneratedAsset
from ..leonardo_images import LeonardoImageError, generate_image
from ..llm import LLMProvider, MediaUnavailableError
from ..media_utils import render_placeholder_image
from ..stock_photos import StockPhotoError, search_photo


def _parse_size(image_size: str | None) -> tuple[int, int] | None:
    """Parses an OpenAI-style `"WIDTHxHEIGHT"` string (e.g. `"1024x1536"`)
    into `(width, height)` - returns `None` for `"auto"`/unset/malformed
    values, letting the caller fall back to its own default."""
    if not image_size or image_size == "auto":
        return None
    try:
        width_str, height_str = image_size.lower().split("x", 1)
        return int(width_str), int(height_str)
    except (ValueError, AttributeError):
        return None


def _orientation_for(image_size: str | None) -> str:
    """Maps a `"WIDTHxHEIGHT"` size to Pexels' `orientation` search
    parameter - defaults to "portrait" (this app's original default,
    matching short-form vertical video) when unset/unparseable."""
    parsed = _parse_size(image_size)
    if parsed is None:
        return "portrait"
    width, height = parsed
    if width > height:
        return "landscape"
    if width == height:
        return "square"
    return "portrait"


class ImageGeneratorAgent:
    """Four-tier fallback: AI generation (`llm.image`) -> Leonardo.ai AI
    generation (only if `settings.leonardo_api_key` is configured) -> Pexels
    stock photo (only if `settings.pexels_api_key` is configured) ->
    deterministic placeholder. Each tier only runs if the previous one
    failed/is unavailable - see module docstrings in `leonardo_images.py`/
    `stock_photos.py` for why Leonardo is tried before Pexels (a generated
    image matching the prompt is closer to the original intent than a
    stock photo)."""

    def __init__(self, llm: LLMProvider, settings: Settings | None = None):
        self.llm = llm
        self.settings = settings

    def generate(
        self,
        prompt: str,
        out_path: Path,
        *,
        search_query: str | None = None,
        image_size: str | None = None,
        image_quality: str | None = None,
    ) -> GeneratedAsset:
        """`image_size` (`"WIDTHxHEIGHT"`, e.g. `"1024x1536"`, matching
        `gpt-image-1`'s own size strings) and `image_quality`
        (`"low"|"medium"|"high"|"auto"`) are optional per-company overrides
        (see `db.companies.image_size`/`image_quality`) - `None` falls back
        to each provider's own default. Threaded into every fallback tier
        that supports a size concept (Leonardo's width/height, Pexels'
        orientation, the placeholder image) - `image_quality` only applies
        to the primary OpenAI tier, since none of the fallbacks expose a
        comparable quality knob."""
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            out_path.write_bytes(self.llm.image(prompt, size=image_size, quality=image_quality))
            return GeneratedAsset(kind="image", path=str(out_path), is_placeholder=False)
        except MediaUnavailableError:
            pass

        leonardo_key = self.settings.leonardo_api_key if self.settings is not None else None
        if leonardo_key:
            try:
                parsed_size = _parse_size(image_size)
                kwargs = {}
                if parsed_size is not None:
                    kwargs["width"], kwargs["height"] = parsed_size
                out_path.write_bytes(generate_image(prompt, api_key=leonardo_key, **kwargs))
                return GeneratedAsset(
                    kind="image", path=str(out_path), is_placeholder=False, source="fallback"
                )
            except LeonardoImageError:
                pass

        pexels_key = self.settings.pexels_api_key if self.settings is not None else None
        if pexels_key:
            try:
                photo = search_photo(
                    search_query or prompt,
                    api_key=pexels_key,
                    orientation=_orientation_for(image_size),
                )
                out_path.write_bytes(photo.image_bytes)
                return GeneratedAsset(
                    kind="image",
                    path=str(out_path),
                    is_placeholder=False,
                    source="stock",
                    attribution=f"Photo by {photo.photographer} on Pexels",
                )
            except StockPhotoError:
                pass

        placeholder_size = _parse_size(image_size) or (1024, 1792)
        render_placeholder_image(prompt, out_path, size=placeholder_size)
        return GeneratedAsset(kind="image", path=str(out_path), is_placeholder=True)
