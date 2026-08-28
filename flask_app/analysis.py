"""Use-case-spezifische vergleichende Analyse.

Das Modell liefert Punktwerte und ein Verdikt (`ComparativeAnalysis`). Hier
wird daraus eine *nachrechenbare* Auswertung: plattformabhaengige Gewichte,
harte Laengen-Checks gegen die Plattform-Limits und ein Abgleich, ob das
Verdikt des Modells zu seinen eigenen Punktwerten passt.

Der Sinn der Trennung: die subjektive Bewertung kommt vom LLM, die
Aggregation und die Plausibilitaetspruefung laufen deterministisch im Code -
so ist das Ergebnis reproduzierbar und ein widerspruechliches Modellverdikt
faellt auf, statt unbemerkt durchzugehen.
"""

from __future__ import annotations

from typing import Any

from .models import CRITERIA, GenerationResult

PLATFORM_WEIGHTS: dict[str, dict[str, float]] = {
    # Ausformulierte Fachposts: Stimme und Zielgruppenpassung tragen mehr als der Hook.
    "linkedin": {
        "hook_strength": 1.0,
        "brand_voice_fit": 1.5,
        "platform_fit": 1.0,
        "cta_clarity": 1.2,
        "audience_relevance": 1.5,
    },
    # Visueller, schneller Feed: der Einstieg entscheidet.
    "instagram": {
        "hook_strength": 1.5,
        "brand_voice_fit": 1.0,
        "platform_fit": 1.3,
        "cta_clarity": 0.8,
        "audience_relevance": 1.2,
    },
    # Kurz und pointiert, CTA faellt kaum ins Gewicht.
    "x": {
        "hook_strength": 1.6,
        "brand_voice_fit": 1.0,
        "platform_fit": 1.3,
        "cta_clarity": 0.7,
        "audience_relevance": 1.1,
    },
    # Erste Sekunde oder gar nicht.
    "tiktok": {
        "hook_strength": 1.8,
        "brand_voice_fit": 0.8,
        "platform_fit": 1.4,
        "cta_clarity": 0.7,
        "audience_relevance": 1.1,
    },
    # Opt-in-Publikum: der Klick am Ende ist das Ziel.
    "newsletter": {
        "hook_strength": 1.1,
        "brand_voice_fit": 1.3,
        "platform_fit": 0.9,
        "cta_clarity": 1.6,
        "audience_relevance": 1.4,
    },
}

PLATFORM_BODY_LIMITS: dict[str, int] = {
    "linkedin": 1300,
    "instagram": 2200,
    "x": 280,
    "tiktok": 300,
    "newsletter": 2000,
}

DEFAULT_WEIGHT = 1.0


def weights_for(platform: str) -> dict[str, float]:
    return PLATFORM_WEIGHTS.get(platform, {name: DEFAULT_WEIGHT for name in CRITERIA})


def _length_check(text: str, limit: int) -> dict[str, Any]:
    length = len(text)
    return {
        "characters": length,
        "limit": limit,
        "within_limit": length <= limit,
        "over_by": max(0, length - limit),
    }


def compare(result: GenerationResult, *, platform: str) -> dict[str, Any]:
    """Rechnet die Modellbewertung zu einer gewichteten Entscheidung aus."""

    weights = weights_for(platform)
    limit = PLATFORM_BODY_LIMITS.get(platform, 2000)

    per_criterion: list[dict[str, Any]] = []
    weighted_a = 0.0
    weighted_b = 0.0
    raw_a = 0
    raw_b = 0

    for score in result.analysis.criteria:
        weight = weights.get(score.criterion, DEFAULT_WEIGHT)
        weighted_a += score.score_a * weight
        weighted_b += score.score_b * weight
        raw_a += score.score_a
        raw_b += score.score_b
        per_criterion.append(
            {
                "criterion": score.criterion,
                "weight": weight,
                "score_a": score.score_a,
                "score_b": score.score_b,
                "weighted_a": round(score.score_a * weight, 2),
                "weighted_b": round(score.score_b * weight, 2),
                "delta": score.score_a - score.score_b,
                "rationale": score.rationale,
            }
        )

    if weighted_a > weighted_b:
        computed_winner: str | None = "A"
    elif weighted_b > weighted_a:
        computed_winner = "B"
    else:
        computed_winner = None  # Gleichstand: kein rechnerischer Sieger.

    decisive = max(per_criterion, key=lambda item: abs(item["delta"]))
    variant_a = result.variant("A")
    variant_b = result.variant("B")

    return {
        "platform": platform,
        "weighting_profile": weights,
        "per_criterion": per_criterion,
        "totals": {
            "raw_a": raw_a,
            "raw_b": raw_b,
            "weighted_a": round(weighted_a, 2),
            "weighted_b": round(weighted_b, 2),
            "margin": round(abs(weighted_a - weighted_b), 2),
        },
        "computed_winner": computed_winner,
        "model_winner": result.analysis.winner,
        "verdict_consistent": computed_winner is None or computed_winner == result.analysis.winner,
        "decisive_criterion": decisive["criterion"] if abs(decisive["delta"]) else None,
        "length_check": {
            "A": _length_check(variant_a.body, limit),
            "B": _length_check(variant_b.body, limit),
        },
        "angles": {"A": variant_a.angle, "B": variant_b.angle},
        "reasoning_steps": result.analysis.reasoning_steps,
        "winner_rationale": result.analysis.winner_rationale,
        "recommended_edit": result.analysis.recommended_edit,
    }
