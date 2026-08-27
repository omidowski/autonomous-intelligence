import json

from autonomous_intelligence.agents.analyst import AnalysisAgent
from autonomous_intelligence.agents.fact_checker import FactCheckingAgent
from autonomous_intelligence.agents.planner import PlannerAgent
from autonomous_intelligence.agents.reporter import ReportAgent
from autonomous_intelligence.models import AnalysisResult, Evidence


class RecordingLLM:
    is_demo = False

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []
        self.openrouter_calls: list[tuple[str, str, str]] = []

    def generate_text(self, instructions: str, prompt: str, *, content_model: str | None = None) -> str:
        if content_model:
            self.openrouter_calls.append((instructions, prompt, content_model))
        else:
            self.calls.append((instructions, prompt))
        return self.response

    @staticmethod
    def extract_json(text: str):
        return json.loads(text)


def _evidence() -> list[Evidence]:
    return [Evidence(title="t", source="s", content="Some evidence content.", relevance=0.9, confidence=0.8)]


def test_planner_keeps_search_queries_language_neutral():
    llm = RecordingLLM(
        json.dumps({"objective": "o", "subquestions": ["q"], "search_queries": ["query"]})
    )
    PlannerAgent(llm).run("A research question", content_model="openai/gpt-5")

    assert len(llm.openrouter_calls) == 1
    _, prompt, model = llm.openrouter_calls[0]
    assert model == "openai/gpt-5"
    assert "retrieval quality matters" in prompt


def test_fact_checker_language_instruction():
    llm = RecordingLLM(
        json.dumps([{"claim": "c", "verdict": "supported", "rationale": "r", "confidence": 0.9}])
    )
    FactCheckingAgent(llm).run("query", _evidence(), language="German")

    instructions, prompt = llm.calls[0]
    assert "German" in instructions
    assert "German" in prompt


def test_fact_checker_no_language_omits_instruction():
    llm = RecordingLLM(
        json.dumps([{"claim": "c", "verdict": "supported", "rationale": "r", "confidence": 0.9}])
    )
    FactCheckingAgent(llm).run("query", _evidence())

    instructions, prompt = llm.calls[0]
    assert "German" not in instructions
    assert "Write every claim" not in prompt


def test_analyst_language_and_content_model():
    llm = RecordingLLM(
        json.dumps(
            {"key_findings": ["f"], "opportunities": ["o"], "risks": ["r"], "open_questions": ["q"]}
        )
    )
    AnalysisAgent(llm).run(
        "query", _evidence(), [], content_model="anthropic/claude-sonnet-5", language="French"
    )

    assert llm.calls == []
    instructions, prompt, model = llm.openrouter_calls[0]
    assert model == "anthropic/claude-sonnet-5"
    assert "French" in instructions
    assert "French" in prompt


def test_reporter_language_instruction():
    llm = RecordingLLM(json.dumps({"executive_summary": "s", "recommendations": ["r"]}))
    analysis = AnalysisResult(key_findings=[], opportunities=[], risks=[], open_questions=[])

    ReportAgent(llm).run("query", analysis, _evidence(), [], language="Japanese")

    instructions, prompt = llm.calls[0]
    assert "Japanese" in instructions
    assert "Japanese" in prompt
