from datetime import UTC, datetime

from autonomous_intelligence import db, scheduler, usage
from autonomous_intelligence.config import Settings


def _settings(tmp_path) -> Settings:
    return Settings(
        ai_provider="demo",
        daily_trends_count=1,
        daily_content_output_dir=str(tmp_path / "output"),
        database_path=str(tmp_path / "app.db"),
    )


def test_due_company_triggers_exactly_one_run(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    db.init_db(settings.database_path)
    company_id = db.insert_company(settings.database_path, slug="acme", name="Acme")
    now = datetime.now(UTC)
    db.update_company_schedule(
        settings.database_path, company_id, enabled=True, hour_utc=now.hour, minute_utc=now.minute
    )

    calls = []

    class FakeOrchestrator:
        def __init__(self, settings):
            self.settings = settings

        def run(self, *, company_slug, brand_voice=None, content_model=None, **kwargs):
            calls.append((company_slug, brand_voice, content_model))

            class Report:
                def __init__(self):
                    self.bundles = []

            return Report()

    monkeypatch.setattr(scheduler, "DailyContentOrchestrator", FakeOrchestrator)

    scheduler.run_due_companies(settings)

    assert calls == [("acme", None, None)]

    row = db.get_company_by_slug(settings.database_path, "acme")
    assert row["last_scheduled_run_date"] == now.strftime("%Y-%m-%d")

    summary = usage.get_monthly_summary(settings, company_id)
    assert summary["content_runs"] == 1


def test_already_run_today_is_skipped(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    db.init_db(settings.database_path)
    company_id = db.insert_company(settings.database_path, slug="acme", name="Acme")
    now = datetime.now(UTC)
    db.update_company_schedule(
        settings.database_path, company_id, enabled=True, hour_utc=now.hour, minute_utc=now.minute
    )
    db.mark_company_run(settings.database_path, "acme", now.strftime("%Y-%m-%d"))

    calls = []
    monkeypatch.setattr(
        scheduler, "DailyContentOrchestrator", lambda settings: type("O", (), {"run": lambda self, **kw: calls.append(kw)})()
    )

    scheduler.run_due_companies(settings)

    assert calls == []


def test_disabled_company_is_skipped(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    db.init_db(settings.database_path)
    db.insert_company(settings.database_path, slug="acme", name="Acme")
    # daily_run_enabled defaults to 0 / not set - never scheduled.

    calls = []
    monkeypatch.setattr(
        scheduler, "DailyContentOrchestrator", lambda settings: type("O", (), {"run": lambda self, **kw: calls.append(kw)})()
    )

    scheduler.run_due_companies(settings)

    assert calls == []


def test_not_yet_due_company_is_skipped(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    db.init_db(settings.database_path)
    company_id = db.insert_company(settings.database_path, slug="acme", name="Acme")
    future_hour = (datetime.now(UTC).hour + 2) % 24
    db.update_company_schedule(
        settings.database_path, company_id, enabled=True, hour_utc=future_hour, minute_utc=0
    )

    calls = []
    monkeypatch.setattr(
        scheduler, "DailyContentOrchestrator", lambda settings: type("O", (), {"run": lambda self, **kw: calls.append(kw)})()
    )

    scheduler.run_due_companies(settings)

    assert calls == []
