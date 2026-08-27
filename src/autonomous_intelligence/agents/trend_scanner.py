from ..content_models import TrendItem
from ..llm import LLMProvider


class TrendScannerAgent:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(self, count: int) -> list[TrendItem]:
        raw = self.llm.web_research(
            f"Top {count} general news trends today across politics, business, technology, "
            "entertainment, sports, and culture. For each, give the headline, a 2-3 sentence "
            "summary, category, and source URLs."
        )
        prompt = f"""
Raw web research about today's top news trends:
{raw}

Extract exactly {count} distinct top trends. Return ONLY a JSON array with this exact shape:
[{{"rank":1,"title":"...","summary":"...","category":"...","source_urls":["..."],"search_query":"..."}}]
"""
        data = self.llm.extract_json(self.llm.text("You are a news-trend curation agent.", prompt))
        return [TrendItem.model_validate(item) for item in data][:count]
