"""Curated OpenRouter model IDs offered as a per-company content-generation
model choice (see `db.companies.content_model`, `auth_api.py`'s
`/auth/me/content-model`). OpenRouter (https://openrouter.ai) fronts all of
these behind one API key - no separate Anthropic/Google/xAI SDK integration
needed. `None`/unset means "use the app's default provider"
(`Settings.resolved_provider`, currently OpenAI).

These are just the models offered in the UI dropdown - any OpenRouter model
ID works with `LLMProvider.text_via_openrouter()`, this list is a curated
subset for a clean picker, not a hard allowlist enforced server-side.
"""

from __future__ import annotations

CONTENT_MODEL_CHOICES: dict[str, str] = {
    "openai/gpt-5": "ChatGPT (GPT-5)",
    "anthropic/claude-sonnet-5": "Claude (Sonnet 5)",
    "google/gemini-3-flash-preview": "Gemini (3 Flash)",
    "x-ai/grok-4.5": "Grok (4.5)",
}
"""Current as of 2026-08 (checked live against OpenRouter's `/api/v1/models`
- these model slugs get renamed/deprecated over time, unlike a fixed
`gpt-4o-mini`-style pin elsewhere in this app; re-check periodically."""
"""OpenRouter model ID -> human-readable label, in display order."""

__all__ = ["CONTENT_MODEL_CHOICES"]
