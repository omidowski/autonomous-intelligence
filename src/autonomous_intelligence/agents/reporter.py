from ..llm import LLMProvider
from ..models import AnalysisResult, Evidence, FactCheck


class ReportAgent:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(
        self,
        query: str,
        analysis: AnalysisResult,
        evidence: list[Evidence],
        checks: list[FactCheck],
        *,
        content_model: str | None = None,
        language: str | None = None,
    ) -> tuple[str, list[str]]:
        language_line = f"\nWrite the summary and recommendations in {language}.\n" if language else ""
        prompt = f"""
Question: {query}
Findings: {analysis.model_dump_json()}
Fact checks: {[c.model_dump() for c in checks]}
Evidence count: {len(evidence)}
{language_line}
Return ONLY JSON:
{{
  "executive_summary": "120-200 word decision-oriented summary",
  "recommendations": ["3-5 concrete recommendations"]
}}
"""
        instructions = "You are an executive research-report writer."
        if language:
            instructions = f"{instructions} Write entirely in {language}."
        data = self.llm.extract_json(
            self.llm.generate_text(instructions, prompt, content_model=content_model)
        )
        return str(data["executive_summary"]), list(data["recommendations"])
