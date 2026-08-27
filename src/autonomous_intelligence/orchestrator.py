from __future__ import annotations

from time import perf_counter

from .agents.analyst import AnalysisAgent
from .agents.fact_checker import FactCheckingAgent
from .agents.planner import PlannerAgent
from .agents.reporter import ReportAgent
from .agents.researcher import ResearchAgent
from .config import Settings, get_settings
from .evaluation import Evaluator
from .llm import LLMProvider
from .models import ResearchReport, ResearchRequest
from .rag import InMemoryVectorStore


class ResearchOrchestrator:
    """Coordinates plan -> research -> evaluate -> analyze -> report."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.llm = LLMProvider(self.settings)
        self.store = InMemoryVectorStore(self.llm)
        self.planner = PlannerAgent(self.llm)
        self.researcher = ResearchAgent(self.llm, self.store)
        self.fact_checker = FactCheckingAgent(self.llm)
        self.analyst = AnalysisAgent(self.llm)
        self.reporter = ReportAgent(self.llm)

    def run(
        self,
        request: ResearchRequest,
        *,
        content_model: str | None = None,
        language: str | None = None,
    ) -> ResearchReport:
        """`content_model`/`language` are the same per-company overrides
        `DailyContentOrchestrator` accepts (see `db.companies.content_model`/
        `content_language`) - applied to every text-generating agent except
        `PlannerAgent`'s search_queries (kept in whatever language retrieves
        best, see `agents/planner.py`) and `ResearchAgent`'s raw evidence
        retrieval (untranslated source material - the agents below already
        synthesize it into the target language regardless)."""
        started = perf_counter()
        plan = self.planner.run(request.query, content_model=content_model)
        max_iterations = request.max_iterations or self.settings.max_research_iterations
        evidence = []

        for iteration in range(1, max_iterations + 1):
            evidence.extend(self.researcher.run(plan, iteration=iteration))
            if len(evidence) >= self.settings.min_evidence_items:
                break

        checks = self.fact_checker.run(
            request.query, evidence, content_model=content_model, language=language
        )
        analysis = self.analyst.run(
            request.query, evidence, checks, content_model=content_model, language=language
        )
        summary, recommendations = self.reporter.run(
            request.query, analysis, evidence, checks, content_model=content_model, language=language
        )
        evaluation = Evaluator.evaluate(evidence, checks, perf_counter() - started)

        return ResearchReport(
            query=request.query,
            executive_summary=summary,
            plan=plan,
            evidence=evidence,
            fact_checks=checks,
            analysis=analysis,
            evaluation=evaluation,
            recommendations=recommendations,
            mode=self.llm.mode,
        )
