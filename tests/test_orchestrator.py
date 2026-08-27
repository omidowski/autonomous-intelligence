import subprocess
from pathlib import Path

from autonomous_intelligence.config import Settings
from autonomous_intelligence.models import ResearchRequest
from autonomous_intelligence.orchestrator import ResearchOrchestrator


def test_demo_orchestrator_builds_report():
    settings = Settings(
        ai_provider="demo",
        openai_api_key=None,
        max_research_iterations=1,
        min_evidence_items=2,
    )
    report = ResearchOrchestrator(settings).run(
        ResearchRequest(query="How can agentic AI improve enterprise research workflows?")
    )
    assert report.mode == "demo"
    assert report.plan.search_queries
    assert report.evidence
    assert report.analysis.key_findings
    assert report.recommendations


def test_codex_orchestrator_uses_codex_outputs(monkeypatch):
    monkeypatch.setattr(
        "autonomous_intelligence.llm.shutil.which",
        lambda _: "/usr/local/bin/codex",
    )
    outputs = iter(
        [
            """{
                "objective": "Research the question.",
                "subquestions": ["What is the evidence?"],
                "search_queries": ["primary evidence"]
            }""",
            "Evidence from a primary source: https://example.com/source",
            """[{
                "claim": "The evidence supports the finding.",
                "verdict": "supported",
                "rationale": "The source directly supports it.",
                "confidence": 0.9
            }]""",
            """{
                "key_findings": ["A Codex-backed finding."],
                "opportunities": ["Faster research"],
                "risks": ["Source quality"],
                "open_questions": ["How should sources be ranked?"]
            }""",
            """{
                "executive_summary": "Codex produced this summary.",
                "recommendations": ["Validate the primary source."]
            }""",
        ]
    )
    exec_commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        if command[1:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")
        exec_commands.append(command)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(next(outputs), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("autonomous_intelligence.llm.subprocess.run", fake_run)
    settings = Settings(
        ai_provider="codex",
        codex_cli_path="codex",
        max_research_iterations=1,
        min_evidence_items=1,
    )

    report = ResearchOrchestrator(settings).run(
        ResearchRequest(query="What does the primary evidence show?")
    )

    assert report.mode == "codex"
    assert report.evidence[0].source == "Codex CLI web search"
    assert report.fact_checks[0].claim == "The evidence supports the finding."
    assert report.executive_summary == "Codex produced this summary."
    assert report.evaluation.evidence_count == 1
    assert len(exec_commands) == 5
    assert sum("--search" in command for command in exec_commands) == 1
