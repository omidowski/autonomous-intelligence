"""Company-slug helpers shared by auth (creating a company) and the daily
content pipeline (scoping output directories per company)."""

from __future__ import annotations

import re
from pathlib import Path

from . import db
from .config import Settings

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(db_path: str, name: str) -> str:
    """Builds a URL-/path-safe slug from a company name, appending `-2`,
    `-3`, ... on collision against existing companies."""
    base = _SLUG_RE.sub("-", name.strip().lower()).strip("-") or "company"
    slug = base
    suffix = 2
    while db.get_company_by_slug(db_path, slug) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def tenant_output_dir(settings: Settings, company_slug: str, date_str: str) -> Path:
    return Path(settings.daily_content_output_dir) / company_slug / date_str


__all__ = ["slugify", "tenant_output_dir"]
