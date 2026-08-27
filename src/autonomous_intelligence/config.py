from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ai_provider: Literal["auto", "codex", "openai", "openrouter", "demo"] = "auto"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.5"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_image_model: str = "gpt-image-1"
    openai_image_size: str = "1024x1536"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_embedding_model: str = "openai/text-embedding-3-small"
    codex_cli_path: str = "codex"
    codex_model: str | None = None
    codex_timeout_seconds: int = 180
    app_env: str = "development"
    max_research_iterations: int = 2
    min_evidence_items: int = 3
    daily_trends_count: int = 3
    daily_content_output_dir: str = "output"
    database_path: str = "data/app.db"
    session_cookie_name: str = "ai_session"
    session_ttl_days: int = 14
    pbkdf2_iterations: int = 200_000
    brand_assets_dir: str = "assets"
    """Where uploaded per-company brand assets (currently: logo) are stored
    on disk - `<brand_assets_dir>/<company_slug>/<asset_id>_<filename>`,
    mirroring `daily_content_output_dir`'s tenant-scoped layout."""
    leonardo_api_key: str | None = None
    """Optional fallback for `agents/image_generator.py`, tried before
    Pexels: when AI image generation is unavailable (`MediaUnavailableError`,
    e.g. `gpt-image-1` pending OpenAI org verification), generate a real AI
    image via Leonardo.ai instead of searching stock photos - closer to the
    original prompt/intent. Get a key at https://leonardo.ai/. Leaving this
    unset skips straight to the Pexels/placeholder fallback."""
    pexels_api_key: str | None = None
    """Optional fallback for `agents/image_generator.py`: when AI image
    generation (and Leonardo, if configured) is unavailable, search Pexels
    for a matching stock photo instead of falling straight to the
    placeholder. Free tier, get a key at https://www.pexels.com/api/.
    Leaving this unset preserves the previous placeholder-only behavior."""
    elevenlabs_api_key: str | None = None
    """Optional fallback for `agents/audio_narrator.py`: when OpenAI speech
    synthesis is unavailable (`MediaUnavailableError`, e.g. this OpenAI
    project has no access to gpt-4o-mini-tts/tts-1), synthesize narration via
    ElevenLabs instead of a silent placeholder. Free tier, get a key at
    https://elevenlabs.io/. Leaving this unset preserves the previous
    silent-placeholder-only behavior."""
    runway_api_key: str | None = None
    """Optional upgrade for `daily_content_orchestrator.py`: generate a real
    AI video clip (image-to-video, `runway_video.py`) from the cover image
    instead of the moviepy slideshow assembly. Unlike the other fallbacks,
    this has no free tier - real money per generation - so it's only ever
    tried when explicitly configured. Leaving this unset preserves the
    existing slideshow-assembly-only behavior. Get a key at
    https://runwayml.com/."""
    kling_access_key: str | None = None
    kling_secret_key: str | None = None
    """Optional upgrade for `daily_content_orchestrator.py`, alternative to
    Runway: image-to-video via Kling AI (`kling_video.py`). Both keys are
    required together (Kling authenticates with a JWT signed from an
    access-key/secret-key pair, not a single bearer token). No free tier.
    Get keys at https://kling.ai/document-api/."""
    heygen_api_key: str | None = None
    heygen_avatar_id: str | None = None
    heygen_voice_id: str | None = None
    """Optional upgrade for `daily_content_orchestrator.py`: instead of
    motion added to the generated cover image (Runway/Kling), render a
    talking AI avatar reading the script (`heygen_video.py`) - a
    fundamentally different look (digital presenter vs. b-roll), so when
    multiple video upgrades are configured HeyGen is tried first. Both
    `heygen_api_key` and `heygen_avatar_id` are required together (no
    generic default avatar exists - one must be created/picked in HeyGen's
    own dashboard first); `heygen_voice_id` is optional and falls back to
    `heygen_video.DEFAULT_VOICE_ID`. No free tier. Get a key and avatar at
    https://app.heygen.com/."""
    app_base_url: str = "http://localhost:8000"
    """Absolute base URL this app is served at - Stripe Checkout/portal
    sessions require absolute success/cancel/return URLs, unlike every
    other route in this app (which only ever needs relative paths, since
    the frontend calls its own origin). Set to the real public URL in
    production; the localhost default only works for local testing."""
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: str | None = None
    """`billing.py`/`billing_api.py`: real subscription billing via Stripe
    Checkout + a customer portal + a webhook that keeps
    `companies.subscription_plan`/`subscription_status` in sync. All three
    are required together for real payments - `stripe_secret_key` to call
    the API, `stripe_webhook_secret` to verify that webhook events actually
    came from Stripe (`stripe.Webhook.construct_event`), and
    `stripe_publishable_key` isn't currently used server-side but is
    exposed to the frontend for a future embedded flow. Leaving
    `stripe_secret_key` unset makes `/billing/checkout` and `/billing/portal`
    return 503 - unlike the content-generation fallbacks elsewhere in this
    file, there is deliberately no stub/demo mode for money: silently
    pretending a checkout succeeded would be actively misleading rather
    than a harmless placeholder. Get keys at https://dashboard.stripe.com/apikeys
    and https://dashboard.stripe.com/webhooks."""
    stripe_price_id_starter: str | None = None
    stripe_price_id_pro: str | None = None
    """Stripe Price IDs (`price_...`) for the two paid plans in
    `billing.PLAN_CHOICES` - created in the Stripe dashboard under
    Product catalog, one recurring price per plan. A plan whose price id
    isn't configured is hidden from `/billing/plans` rather than offered
    with a broken checkout button."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def resolved_provider(self) -> Literal["codex", "openai", "openrouter", "demo"]:
        if self.ai_provider == "auto":
            if self.openai_api_key:
                return "openai"
            if self.openrouter_api_key:
                return "openrouter"
            return "demo"
        return self.ai_provider

    @property
    def demo_mode(self) -> bool:
        return self.resolved_provider == "demo"


@lru_cache
def get_settings() -> Settings:
    return Settings()
