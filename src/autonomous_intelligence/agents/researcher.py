from ..llm import LLMProvider
from ..models import Evidence, ResearchPlan
from ..rag import InMemoryVectorStore


class ResearchAgent:
    def __init__(self, llm: LLMProvider, store: InMemoryVectorStore):
        self.llm = llm
        self.store = store

    def run(self, plan: ResearchPlan, iteration: int = 1) -> list[Evidence]:
        evidence: list[Evidence] = []
        source = {
            "codex": "Codex CLI web search",
            "openai": "OpenAI web search",
            "openrouter": "OpenRouter web search",
            "demo": "Demo knowledge base",
        }[self.llm.provider]
        for index, search_query in enumerate(plan.search_queries):
            raw = self.llm.web_research(search_query)
            item = Evidence(
                title=f"Research result {iteration}.{index + 1}",
                source=source,
                content=raw,
                relevance=0.85,
                confidence=0.55 if self.llm.is_demo else 0.75,
            )
            evidence.append(item)
            self.store.add(
                f"evidence-{iteration}-{index}",
                raw,
                {"query": search_query, "title": item.title},
            )
        return evidence
