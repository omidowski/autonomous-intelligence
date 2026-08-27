from ..llm import LLMProvider
from ..models import ResearchPlan


class PlannerAgent:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(self, query: str, *, content_model: str | None = None) -> ResearchPlan:
        prompt = f"""
Research question: {query}
Return ONLY valid JSON with this exact shape:
{{
  "objective": "...",
  "subquestions": ["..."],
  "search_queries": ["..."]
}}
Create 3-5 focused subquestions and 3-5 strong search queries.

Keep subquestions and search_queries in whatever language gives the best
search results for this topic (usually English) regardless of what
language the final report will be written in - retrieval quality matters
more here than matching the report's output language.
"""
        raw = self.llm.generate_text(
            "You are a research-planning agent.", prompt, content_model=content_model
        )
        data = self.llm.extract_json(raw)
        return ResearchPlan.model_validate(data)
