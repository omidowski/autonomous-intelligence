"""Hand-rolled `asyncio` poll loop for per-company daily-run scheduling -
not APScheduler. The requirement is narrow (once/day, per company, UTC
hour+minute) and the config already has to live in `companies` for the
schedule-settings UI, so a second scheduling engine would duplicate state
this app already owns. If cron syntax or multi-instance coordination is
ever needed, APScheduler with a shared job store (or a real task queue) is
the recommended upgrade path - not built here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from . import db, usage
from .config import Settings
from .daily_content_orchestrator import DailyContentOrchestrator

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60


def run_due_companies(settings: Settings) -> None:
    """Synchronous, independently testable: runs the daily content pipeline
    for every company whose scheduled UTC hour/minute has passed today and
    hasn't already run today. Always marks the company as run today (even
    on failure) to avoid retry storms - a failed scheduled run is logged
    and picked up again tomorrow, not retried every poll tick."""
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")

    for company in db.list_companies_with_schedule_enabled(settings.database_path):
        if company["last_scheduled_run_date"] == today:
            continue
        if now.hour != company["daily_run_hour_utc"]:
            continue
        if now.minute < company["daily_run_minute_utc"]:
            continue

        slug = company["slug"]
        try:
            report = DailyContentOrchestrator(settings).run(
                company_slug=slug,
                brand_voice=company["brand_voice"],
                content_model=company["content_model"],
                language=company["content_language"],
                target_duration_seconds=company["target_duration_seconds"],
                image_size=company["image_size"],
                image_quality=company["image_quality"],
            )
            usage.record_daily_content_run(settings, company["id"], len(report.bundles))
        except Exception:
            logger.exception("Scheduled daily-content run failed for company %r", slug)
        finally:
            db.mark_company_run(settings.database_path, slug, today)


async def scheduler_loop(stop_event: asyncio.Event, settings: Settings) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(run_due_companies, settings)
        except Exception:
            logger.exception("Scheduler tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except TimeoutError:
            pass


__all__ = ["POLL_INTERVAL_SECONDS", "run_due_companies", "scheduler_loop"]
