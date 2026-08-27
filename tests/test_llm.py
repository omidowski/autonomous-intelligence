import subprocess
from pathlib import Path

import pytest

from autonomous_intelligence.config import Settings
from autonomous_intelligence.llm import LLMProvider, MediaUnavailableError


def test_codex_provider_runs_ephemeral_read_only_web_research(monkeypatch):
    calls: list[tuple[list[str], dict]] = []

    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-inherited")
    monkeypatch.setattr(
        "autonomous_intelligence.llm.shutil.which",
        lambda _: "/usr/local/bin/codex",
    )

    def fake_run(command: list[str], **kwargs):
        calls.append((command, kwargs))
        if command[1:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")

        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("Codex research result", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("autonomous_intelligence.llm.subprocess.run", fake_run)
    llm = LLMProvider(
        Settings(
            ai_provider="codex",
            codex_cli_path="codex",
            codex_timeout_seconds=30,
        )
    )

    assert llm.web_research("current research evidence") == "Codex research result"
    assert len(calls) == 2

    command, options = calls[1]
    assert command[0] == "/usr/local/bin/codex"
    assert command[command.index("-s") + 1] == "read-only"
    assert "--search" in command
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--skip-git-repo-check" in command
    assert command[-1] == "-"
    assert "current research evidence" in options["input"]
    assert "OPENAI_API_KEY" not in options["env"]
    assert "UNRELATED_SECRET" not in options["env"]


def test_codex_provider_reports_cli_failure(monkeypatch):
    monkeypatch.setattr(
        "autonomous_intelligence.llm.shutil.which",
        lambda _: "/usr/local/bin/codex",
    )

    def fake_run(command: list[str], **kwargs):
        if command[1:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")
        return subprocess.CompletedProcess(command, 1, "", "request failed")

    monkeypatch.setattr("autonomous_intelligence.llm.subprocess.run", fake_run)
    llm = LLMProvider(Settings(ai_provider="codex", codex_cli_path="codex"))

    with pytest.raises(RuntimeError, match="exit code 1"):
        llm.text("Be concise.", "Answer this question.")


def test_demo_provider_image_and_speech_raise_media_unavailable():
    llm = LLMProvider(Settings(ai_provider="demo"))

    with pytest.raises(MediaUnavailableError):
        llm.image("a red circle on white background")
    with pytest.raises(MediaUnavailableError):
        llm.speech("hello")


def test_codex_provider_image_and_speech_raise_media_unavailable(monkeypatch):
    monkeypatch.setattr(
        "autonomous_intelligence.llm.shutil.which",
        lambda _: "/usr/local/bin/codex",
    )
    llm = LLMProvider(Settings(ai_provider="codex", codex_cli_path="codex"))

    with pytest.raises(MediaUnavailableError):
        llm.image("a red circle on white background")
    with pytest.raises(MediaUnavailableError):
        llm.speech("hello")


def test_text_via_openrouter_requires_key_regardless_of_primary_provider():
    llm = LLMProvider(Settings(ai_provider="demo", openrouter_api_key=None))
    llm.provider = "openai"  # simulate a primary provider other than demo/openrouter

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        llm.text_via_openrouter("Be concise.", "Hello", model="anthropic/claude-3.5-sonnet")


def test_text_via_openrouter_uses_demo_text_in_demo_mode():
    llm = LLMProvider(Settings(ai_provider="demo"))

    result = llm.text_via_openrouter("Be concise.", "Answer this.", model="anthropic/claude-3.5-sonnet")

    assert result == "Demo response"


def test_text_via_openrouter_calls_openrouter_with_given_model(monkeypatch):
    captured = {}

    class FakeChoice:
        def __init__(self, content):
            self.message = type("Message", (), {"content": content})()

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"choices": [FakeChoice("Claude says hi")]})()

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeOpenAIClient:
        def __init__(self, *, api_key, base_url=None):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.chat = FakeChat()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAIClient)

    llm = LLMProvider(
        Settings(ai_provider="openai", openai_api_key="sk-test", openrouter_api_key="or-test")
    )

    result = llm.text_via_openrouter(
        "Be concise.", "Answer this.", model="anthropic/claude-3.5-sonnet"
    )

    assert result == "Claude says hi"
    assert captured["api_key"] == "or-test"
    assert captured["model"] == "anthropic/claude-3.5-sonnet"
