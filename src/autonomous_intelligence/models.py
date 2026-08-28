from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from .model_catalog import validate_content_model

_Topic = Annotated[str, Field(min_length=3, max_length=500)]


class _HasContentModel(BaseModel):
    """Mixin for request bodies that accept an optional per-request content
    model (an OpenRouter id from `/auth/content-model-choices`) - overrides
    the company's default for that one call. Empty/omitted -> company
    default."""

    model: str | None = Field(default=None)

    @field_validator("model")
    @classmethod
    def _check_model(cls, value: str | None) -> str | None:
        return validate_content_model(value)


class ResearchRequest(_HasContentModel):
    query: str = Field(min_length=5, max_length=1000)
    max_iterations: int | None = Field(default=None, ge=1, le=5)


class CustomContentRequest(_HasContentModel):
    topic: str = Field(min_length=3, max_length=500)


class BulkCustomContentRequest(_HasContentModel):
    """Queue several custom topics in one call. Runs synchronously (like
    `/custom-content/run`), so the list is capped - each topic is a full
    generation pipeline. Topics that hit the plan's monthly content quota
    are reported as skipped rather than failing the whole batch."""

    topics: list[_Topic] = Field(min_length=1, max_length=10)


class MultiModelContentRequest(BaseModel):
    """Generate the same topic with several models at once, so a reviewer
    can compare and keep the best. Each model is a full generation pipeline
    and is metered/quota-checked individually (like the bulk endpoint), so
    the model list is capped."""

    topic: _Topic
    models: list[str] = Field(min_length=2, max_length=4)

    @field_validator("models")
    @classmethod
    def _check_models(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            resolved = validate_content_model(item)
            if resolved is None:
                raise ValueError("Model ids in `models` cannot be empty.")
            if resolved not in seen:
                seen.append(resolved)
        if len(seen) < 2:
            raise ValueError("`models` must name at least two distinct models to compare.")
        return seen


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
