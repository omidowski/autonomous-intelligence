from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    query: str = Field(min_length=5, max_length=1000)
    max_iterations: int | None = Field(default=None, ge=1, le=5)


class CustomContentRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=500)


class ResearchPlan(BaseModel):
    objective: str
    subquestions: list[str]
    search_queries: list[str]


class Evidence(BaseModel):
    title: str
    source: str
    content: str
    relevance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


class FactCheck(BaseModel):
    claim: str
    verdict: Literal["supported", "mixed", "insufficient", "contradicted"]
    rationale: str
    confidence: float = Field(ge=0, le=1)


class EvaluationMetrics(BaseModel):
    evidence_count: int
    fact_check_count: int
    average_evidence_confidence: float = Field(ge=0, le=1)
    claim_support_rate: float = Field(ge=0, le=1)
    contradiction_rate: float = Field(ge=0, le=1)
    latency_seconds: float = Field(ge=0)


class AnalysisResult(BaseModel):
    key_findings: list[str]
    opportunities: list[str]
    risks: list[str]
    open_questions: list[str]


class ResearchReport(BaseModel):
    query: str
    executive_summary: str
    plan: ResearchPlan
    evidence: list[Evidence]
    fact_checks: list[FactCheck]
    analysis: AnalysisResult
    evaluation: EvaluationMetrics
    recommendations: list[str]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mode: Literal["live", "codex", "openrouter", "demo"]
