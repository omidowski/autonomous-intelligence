from autonomous_intelligence.config import Settings
from autonomous_intelligence.llm import LLMProvider
from autonomous_intelligence.rag import InMemoryVectorStore


def test_vector_store_returns_results():
    llm = LLMProvider(Settings(ai_provider="demo", openai_api_key=None))
    store = InMemoryVectorStore(llm)
    store.add("1", "agentic AI uses tools and planning")
    store.add("2", "classical databases store relational data")
    results = store.search("AI agents and tools", k=1)
    assert len(results) == 1
    assert results[0][0].id in {"1", "2"}
