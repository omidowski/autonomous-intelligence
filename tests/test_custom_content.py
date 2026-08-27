from autonomous_intelligence.agents.video_assembler import VideoAssembler
from autonomous_intelligence.config import Settings
from autonomous_intelligence.daily_content_orchestrator import DailyContentOrchestrator


def _demo_settings(tmp_path) -> Settings:
    return Settings(
        ai_provider="demo",
        daily_content_output_dir=str(tmp_path),
        pexels_api_key=None,
        leonardo_api_key=None,
        elevenlabs_api_key=None,
        runway_api_key=None,
    )


def test_run_custom_topic_builds_single_bundle_report(tmp_path):
    orchestrator = DailyContentOrchestrator(
        _demo_settings(tmp_path), video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run_custom_topic("The benefits of a four-day work week")

    assert report.date.startswith("custom-")
    assert len(report.trends) == 1
    assert report.trends[0].title == "The benefits of a four-day work week"
    assert report.trends[0].category == "custom"
    assert len(report.bundles) == 1

    output_dir = tmp_path / "default" / report.date
    assert (output_dir / "manifest.json").is_file()
    assert (output_dir / "trend-1" / "content.json").is_file()


def test_run_custom_topic_scopes_by_company_slug(tmp_path):
    orchestrator = DailyContentOrchestrator(
        _demo_settings(tmp_path), video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run_custom_topic("Remote work productivity tips", company_slug="acme-inc")

    assert (tmp_path / "acme-inc" / report.date / "manifest.json").is_file()


def test_run_custom_topic_passes_through_brand_voice_and_content_model(tmp_path, monkeypatch):
    orchestrator = DailyContentOrchestrator(
        _demo_settings(tmp_path), video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    captured = {}
    original_run = orchestrator.writer.run

    def recording_run(trend, *, brand_voice=None, content_model=None, **kwargs):
        captured["brand_voice"] = brand_voice
        captured["content_model"] = content_model
        return original_run(trend, brand_voice=brand_voice, content_model=content_model, **kwargs)

    monkeypatch.setattr(orchestrator.writer, "run", recording_run)

    orchestrator.run_custom_topic(
        "Sustainable packaging trends", brand_voice="Warm and direct.", content_model="openai/gpt-5"
    )

    assert captured["brand_voice"] == "Warm and direct."
    assert captured["content_model"] == "openai/gpt-5"
