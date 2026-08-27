from ..llm import LLMProvider
from ..models import Evidence, FactCheck


class FactCheckingAgent:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(
        self,
        query: str,
        evidence: list[Evidence],
        *,
        content_model: str | None = None,
        language: str | None = None,
    ) -> list[FactCheck]:
        if not evidence:
            return [
                FactCheck(
                    claim=query,
                    verdict="insufficient",
                    rationale="No evidence was retrieved.",
                    confidence=0.95,
                )
            ]
        joined = "\n\n".join(item.content[:2500] for item in evidence)
        language_line = f"\nWrite every claim and rationale in {language}.\n" if language else ""
        prompt = f"""
Question: {query}
Evidence:\n{joined}
{language_line}
Identify up to 4 important claims and assess them. Return ONLY a JSON array:
[
  {{"claim":"...","verdict":"supported|mixed|insufficient|contradicted", "rationale":"...","confidence":0.0}}
]
"""
        if self.llm.is_demo:
            return [
                FactCheck(
                    claim="Agentic AI requires explicit reliability and governance controls.",
                    verdict="supported",
                    rationale="Multiple demo evidence items point to evaluation and risk-management needs.",
                    confidence=0.72,
                )
            ]
        instructions = "You are a skeptical fact-checking agent."
        if language:
            instructions = f"{instructions} Respond entirely in {language}."
        data = self.llm.extract_json(
            self.llm.generate_text(instructions, prompt, content_model=content_model)
        )
        return [FactCheck.model_validate(item) for item in data]
