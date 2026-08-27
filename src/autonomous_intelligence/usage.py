"""Lightweight usage/cost tracking (rough estimates, not real OpenAI billing
data - this app doesn't query the OpenAI usage API). Every research call and
daily-content run (scheduled or manual) logs one `usage_log` row so
companies can see roughly how much they're using/spending per month."""

from __future__ import annotations

from datetime import UTC, datetime

from . import db
from .config import Settings

ESTIMATED_COST_USD_PER_RESEARCH_CALL = 0.05
"""Rough flat estimate for a single `/research` run (planner + research loop
+ fact-checking + analysis, several LLM calls) - a ballpark for the usage
dashboard, not a precise cost."""

ESTIMATED_COST_USD_PER_CONTENT_TREND = 0.08
"""Rough estimate per generated trend bundle (article/social copy generation
- excludes image/audio, which are currently either free demo placeholders or
blocked pending OpenAI org verification, see README/known issues)."""


def _current_month_prefix() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def record_research(settings: Settings, company_id: int) -> None:
    db.insert_usage_log(
        settings.database_path,
        company_id=company_id,
        kind="research",
        units=1,
        estimated_cost_usd=ESTIMATED_COST_USD_PER_RESEARCH_CALL,
    )


def record_daily_content_run(settings: Settings, company_id: int, trend_count: int) -> None:
    db.insert_usage_log(
        settings.database_path,
        company_id=company_id,
        kind="daily_content_run",
        units=trend_count,
        estimated_cost_usd=ESTIMATED_COST_USD_PER_CONTENT_TREND * trend_count,
    )


def get_monthly_summary(settings: Settings, company_id: int) -> dict:
    rows = db.get_usage_summary(settings.database_path, company_id, since=_current_month_prefix())
    by_kind = {row["kind"]: row for row in rows}

    research = by_kind.get("research")
    content = by_kind.get("daily_content_run")

    research_calls = research["calls"] if research else 0
    content_runs = content["calls"] if content else 0
    trends_generated = content["units"] if content else 0
    estimated_cost_usd = round(
        (research["estimated_cost_usd"] if research else 0.0)
        + (content["estimated_cost_usd"] if content else 0.0),
        2,
    )

    return {
        "period": _current_month_prefix(),
        "research_calls": research_calls,
        "content_runs": content_runs,
        "trends_generated": trends_generated,
        "estimated_cost_usd": estimated_cost_usd,
    }


__all__ = [
    "ESTIMATED_COST_USD_PER_CONTENT_TREND",
    "ESTIMATED_COST_USD_PER_RESEARCH_CALL",
    "get_monthly_summary",
    "record_daily_content_run",
    "record_research",
]
