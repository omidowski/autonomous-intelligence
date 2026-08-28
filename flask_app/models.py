"""Structured Output: Pydantic-Schemas, gegen die jede LLM-Antwort validiert
wird. Das JSON-Schema aus diesen Modellen wird zusaetzlich woertlich in den
Prompt gerendert (`prompts.py`), damit Modell und Validierung dieselbe
Vertragsdefinition teilen.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Platform = Literal["linkedin", "instagram", "x", "tiktok", "newsletter"]

CRITERIA: tuple[str, ...] = (
    "hook_strength",
    "brand_voice_fit",
    "platform_fit",
    "cta_clarity",
    "audience_relevance",
)
"""Use-case-spezifische Bewertungskriterien fuer Marketing-Copy. Bewusst
nicht generisch ("Qualitaet", "Kreativitaet"), sondern an der Aufgabe
orientiert: Was entscheidet, ob ein Social-Post auf dieser Plattform bei
dieser Zielgruppe funktioniert."""

Criterion = Literal[
    "hook_strength",
    "brand_voice_fit",
    "platform_fit",
    "cta_clarity",
    "audience_relevance",
]


class ContentVariant(BaseModel):
    """Eine von zwei Copy-Varianten zum selben Briefing."""

    variant: Literal["A", "B"]
    angle: str = Field(min_length=3, description="Der inhaltliche Winkel in wenigen Worten.")
    headline: str = Field(min_length=3)
    body: str = Field(min_length=10)
    call_to_action: str = Field(min_length=3)
    hashtags: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("hashtags", mode="before")
    @classmethod
    def _normalize_hashtags(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [f"#{tag.lstrip('#')}" for tag in value if isinstance(tag, str) and tag.strip()]


class CriterionScore(BaseModel):
    """Punktwertung beider Varianten auf genau einem Kriterium."""

    criterion: Criterion
    score_a: int = Field(ge=1, le=5)
    score_b: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=5)


class ComparativeAnalysis(BaseModel):
    """Der vergleichende Teil: erst Begruendung, dann Wertung, dann Verdikt.

    `reasoning_steps` steht im Schema absichtlich VOR `criteria` und `winner`:
    das ist die Chain-of-Thought-Technik als erzwungenes Ausgabefeld - das
    Modell muss die Abwaegung ausformulieren, bevor es sich festlegt.
    """

    reasoning_steps: list[str] = Field(min_length=2, max_length=6)
    criteria: list[CriterionScore] = Field(min_length=len(CRITERIA), max_length=len(CRITERIA))
    winner: Literal["A", "B"]
    winner_rationale: str = Field(min_length=10)
    recommended_edit: str = Field(min_length=10)

    @model_validator(mode="after")
    def _criteria_are_complete(self) -> ComparativeAnalysis:
        seen = [item.criterion for item in self.criteria]
        if sorted(seen) != sorted(CRITERIA):
            missing = sorted(set(CRITERIA) - set(seen))
            raise ValueError(f"criteria muss genau {list(CRITERIA)} abdecken; fehlt: {missing}")
        return self


class GenerationResult(BaseModel):
    """Vollstaendige, validierte Antwort des Text-Generation-Endpoints."""

    brief_summary: str = Field(min_length=10)
    variants: list[ContentVariant] = Field(min_length=2, max_length=2)
    analysis: ComparativeAnalysis

    @model_validator(mode="after")
    def _variants_are_a_and_b(self) -> GenerationResult:
        labels = sorted(variant.variant for variant in self.variants)
        if labels != ["A", "B"]:
            raise ValueError("variants muss genau eine Variante 'A' und eine 'B' enthalten")
        return self

    def variant(self, label: str) -> ContentVariant:
        return next(item for item in self.variants if item.variant == label)


def result_json_schema() -> dict:
    """Das Schema, das woertlich in den Prompt gerendert wird."""

    return GenerationResult.model_json_schema()
