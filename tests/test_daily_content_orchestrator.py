import json
from pathlib import Path

from autonomous_intelligence import daily_content_orchestrator
from autonomous_intelligence.agents.video_assembler import VideoAssembler
from autonomous_intelligence.config import Settings
from autonomous_intelligence.daily_content_orchestrator import DailyContentOrchestrator


def test_demo_pipeline_builds_report_and_assets(tmp_path):
    settings = Settings(
        ai_provider="demo",
        daily_trends_count=2,
        daily_content_output_dir=str(tmp_path),
        pexels_api_key=None,
        leonardo_api_key=None,
        elevenlabs_api_key=None,
        runway_api_key=None,
        kling_access_key=None,
        kling_secret_key=None,
        heygen_api_key=None,
        heygen_avatar_id=None,
    )
    orchestrator = DailyContentOrchestrator(
        settings, video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run()

    assert report.mode == "demo"
    assert len(report.trends) == 2
    assert len(report.bundles) == 2

    output_dir = tmp_path / "default" / report.date
    assert (output_dir / "manifest.json").is_file()
    assert (output_dir / "daily_report.md").is_file()

    trend_dir = output_dir / "trend-1"
    assert (trend_dir / "content.json").is_file()
    assert (trend_dir / "video" / "short.mp4").is_file()
    assert (trend_dir / "video" / "short.mp4").stat().st_size > 0

    content = json.loads((trend_dir / "content.json").read_text(encoding="utf-8"))
    assert content["social_posts"]
    assert content["short_script"]["hook"]
    assert content["story"]["slides"]
    assert content["podcast_script"]["title"]
    assert content["video_script"]["shots"]

    for bundle in report.bundles:
        assert bundle.assets
        non_video = [a for a in bundle.assets if a.kind != "video"]
        assert non_video and all(a.is_placeholder for a in non_video)
        video_assets = [a for a in bundle.assets if a.kind == "video"]
        assert len(video_assets) == 1
        # The assembled video file itself is real (genuine Ken Burns motion
        # from the placeholder images), even though its narration track is
        # silent (demo mode has no TTS provider) - `is_placeholder=False` /
        # `is_silent=True` captures that distinction (see
        # `GeneratedAsset.is_silent`), rather than collapsing "no real
        # video" and "video with silent audio" into one flag.
        assert video_assets[0].is_placeholder is False
        assert video_assets[0].is_silent is True


def test_company_slug_scopes_output_directory(tmp_path):
    settings = Settings(
        ai_provider="demo",
        daily_trends_count=1,
        daily_content_output_dir=str(tmp_path),
        pexels_api_key=None,
        leonardo_api_key=None,
        elevenlabs_api_key=None,
        runway_api_key=None,
        kling_access_key=None,
        kling_secret_key=None,
        heygen_api_key=None,
        heygen_avatar_id=None,
    )
    orchestrator = DailyContentOrchestrator(
        settings, video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run(company_slug="acme-inc")

    assert (tmp_path / "acme-inc" / report.date / "manifest.json").is_file()
    assert not (tmp_path / report.date).exists()


def test_runway_video_used_when_key_configured_skips_moviepy_assembly(tmp_path, monkeypatch):
    def fake_generate_video_from_image(image_path, prompt, *, api_key, model=None, ratio=None, duration_seconds=None):
        assert api_key == "fake-key"
        return b"fake-runway-bytes"

    monkeypatch.setattr(
        daily_content_orchestrator, "runway_generate_video", fake_generate_video_from_image
    )

    settings = Settings(
        ai_provider="demo",
        daily_trends_count=1,
        daily_content_output_dir=str(tmp_path),
        pexels_api_key=None,
        leonardo_api_key=None,
        elevenlabs_api_key=None,
        runway_api_key="fake-key",
        kling_access_key=None,
        kling_secret_key=None,
        heygen_api_key=None,
        heygen_avatar_id=None,
    )
    orchestrator = DailyContentOrchestrator(
        settings, video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run()

    video_assets = [a for b in report.bundles for a in b.assets if a.kind == "video"]
    assert len(video_assets) == 1
    assert video_assets[0].is_placeholder is False
    assert video_assets[0].source == "generated"
    assert Path(video_assets[0].path).read_bytes() == b"fake-runway-bytes"


def test_runway_failure_falls_back_to_moviepy_assembly(tmp_path, monkeypatch):
    def failing_generate_video_from_image(image_path, prompt, *, api_key, model=None, ratio=None, duration_seconds=None):
        from autonomous_intelligence.runway_video import RunwayVideoError

        raise RunwayVideoError("moderation rejected")

    monkeypatch.setattr(
        daily_content_orchestrator, "runway_generate_video", failing_generate_video_from_image
    )

    settings = Settings(
        ai_provider="demo",
        daily_trends_count=1,
        daily_content_output_dir=str(tmp_path),
        pexels_api_key=None,
        leonardo_api_key=None,
        elevenlabs_api_key=None,
        runway_api_key="fake-key",
        kling_access_key=None,
        kling_secret_key=None,
        heygen_api_key=None,
        heygen_avatar_id=None,
    )
    orchestrator = DailyContentOrchestrator(
        settings, video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run()

    video_assets = [a for b in report.bundles for a in b.assets if a.kind == "video"]
    assert len(video_assets) == 1
    assert video_assets[0].source is None  # moviepy slideshow path, not Runway
    assert video_assets[0].path.endswith("short.mp4")


def _settings_with_video_upgrades(tmp_path, **overrides):
    defaults = {
        "ai_provider": "demo",
        "daily_trends_count": 1,
        "daily_content_output_dir": str(tmp_path),
        "pexels_api_key": None,
        "leonardo_api_key": None,
        "elevenlabs_api_key": None,
        "runway_api_key": None,
        "kling_access_key": None,
        "kling_secret_key": None,
        "heygen_api_key": None,
        "heygen_avatar_id": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_heygen_avatar_video_used_when_configured(tmp_path, monkeypatch):
    def fake_generate_avatar_video(script_text, *, api_key, avatar_id, **kwargs):
        assert api_key == "fake-heygen-key"
        assert avatar_id == "fake-avatar"
        return b"fake-heygen-bytes"

    monkeypatch.setattr(
        daily_content_orchestrator, "generate_avatar_video", fake_generate_avatar_video
    )

    settings = _settings_with_video_upgrades(
        tmp_path, heygen_api_key="fake-heygen-key", heygen_avatar_id="fake-avatar"
    )
    orchestrator = DailyContentOrchestrator(
        settings, video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run()

    video_assets = [a for b in report.bundles for a in b.assets if a.kind == "video"]
    assert len(video_assets) == 1
    assert video_assets[0].source == "generated"
    assert Path(video_assets[0].path).read_bytes() == b"fake-heygen-bytes"


def test_heygen_takes_priority_over_runway_when_both_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(
        daily_content_orchestrator,
        "generate_avatar_video",
        lambda *a, **kw: b"fake-heygen-bytes",
    )

    def runway_should_not_be_called(*args, **kwargs):
        raise AssertionError("Runway should not be tried when HeyGen is configured and succeeds")

    monkeypatch.setattr(
        daily_content_orchestrator, "runway_generate_video", runway_should_not_be_called
    )

    settings = _settings_with_video_upgrades(
        tmp_path,
        heygen_api_key="fake-heygen-key",
        heygen_avatar_id="fake-avatar",
        runway_api_key="fake-runway-key",
    )
    orchestrator = DailyContentOrchestrator(
        settings, video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run()

    video_assets = [a for b in report.bundles for a in b.assets if a.kind == "video"]
    assert Path(video_assets[0].path).read_bytes() == b"fake-heygen-bytes"


def test_kling_used_when_runway_unconfigured(tmp_path, monkeypatch):
    def fake_kling_generate(image_path, prompt, *, access_key, secret_key, **kwargs):
        assert access_key == "fake-access"
        assert secret_key == "fake-secret"
        return b"fake-kling-bytes"

    monkeypatch.setattr(
        daily_content_orchestrator, "kling_generate_video", fake_kling_generate
    )

    settings = _settings_with_video_upgrades(
        tmp_path, kling_access_key="fake-access", kling_secret_key="fake-secret"
    )
    orchestrator = DailyContentOrchestrator(
        settings, video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run()

    video_assets = [a for b in report.bundles for a in b.assets if a.kind == "video"]
    assert Path(video_assets[0].path).read_bytes() == b"fake-kling-bytes"


def test_kling_tried_after_runway_fails(tmp_path, monkeypatch):
    def failing_runway(*args, **kwargs):
        from autonomous_intelligence.runway_video import RunwayVideoError

        raise RunwayVideoError("moderation rejected")

    monkeypatch.setattr(daily_content_orchestrator, "runway_generate_video", failing_runway)
    monkeypatch.setattr(
        daily_content_orchestrator,
        "kling_generate_video",
        lambda *a, **kw: b"fake-kling-bytes",
    )

    settings = _settings_with_video_upgrades(
        tmp_path,
        runway_api_key="fake-runway-key",
        kling_access_key="fake-access",
        kling_secret_key="fake-secret",
    )
    orchestrator = DailyContentOrchestrator(
        settings, video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run()

    video_assets = [a for b in report.bundles for a in b.assets if a.kind == "video"]
    assert Path(video_assets[0].path).read_bytes() == b"fake-kling-bytes"


def test_all_video_upgrades_failing_falls_back_to_moviepy(tmp_path, monkeypatch):
    def failing_heygen(*args, **kwargs):
        from autonomous_intelligence.heygen_video import HeyGenVideoError

        raise HeyGenVideoError("bad avatar id")

    def failing_runway(*args, **kwargs):
        from autonomous_intelligence.runway_video import RunwayVideoError

        raise RunwayVideoError("moderation rejected")

    def failing_kling(*args, **kwargs):
        from autonomous_intelligence.kling_video import KlingVideoError

        raise KlingVideoError("timed out")

    monkeypatch.setattr(daily_content_orchestrator, "generate_avatar_video", failing_heygen)
    monkeypatch.setattr(daily_content_orchestrator, "runway_generate_video", failing_runway)
    monkeypatch.setattr(daily_content_orchestrator, "kling_generate_video", failing_kling)

    settings = _settings_with_video_upgrades(
        tmp_path,
        heygen_api_key="fake-heygen-key",
        heygen_avatar_id="fake-avatar",
        runway_api_key="fake-runway-key",
        kling_access_key="fake-access",
        kling_secret_key="fake-secret",
    )
    orchestrator = DailyContentOrchestrator(
        settings, video_assembler=VideoAssembler(size=(96, 170), fps=6)
    )

    report = orchestrator.run()

    video_assets = [a for b in report.bundles for a in b.assets if a.kind == "video"]
    assert len(video_assets) == 1
    assert video_assets[0].source is None  # moviepy slideshow path
    assert video_assets[0].path.endswith("short.mp4")
