from ..llm import LLMProvider
from ..models import AnalysisResult, Evidence, FactCheck


class AnalysisAgent:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(
        self,
        query: str,
        evidence: list[Evidence],
        checks: list[FactCheck],
        *,
        content_model: str | None = None,
        language: str | None = None,
    ) -> AnalysisResult:
        context = "\n".join(item.content[:1600] for item in evidence)
        verdicts = "\n".join(f"{c.verdict}: {c.claim}" for c in checks)
        language_line = f"\nWrite every field in {language}.\n" if language else ""
        prompt = f"""
Question: {query}
Evidence: {context}
Fact checks: {verdicts}
{language_line}
Return ONLY JSON:
{{
  "key_findings": ["..."],
  "opportunities": ["..."],
  "risks": ["..."],
  "open_questions": ["..."]
}}
"""
        instructions = "You are a senior AI strategy analyst."
        if language:
            instructions = f"{instructions} Respond entirely in {language}."
        data = self.llm.extract_json(
            self.llm.generate_text(instructions, prompt, content_model=content_model)
        )
        return AnalysisResult.model_validate(data)
