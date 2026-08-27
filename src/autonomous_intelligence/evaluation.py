from __future__ import annotations

from .models import EvaluationMetrics, Evidence, FactCheck


class Evaluator:
    """Lightweight runtime metrics for research quality and operational monitoring."""

    @staticmethod
    def evaluate(
        evidence: list[Evidence],
        checks: list[FactCheck],
        latency_seconds: float,
    ) -> EvaluationMetrics:
        supported = sum(1 for item in checks if item.verdict == "supported")
        contradicted = sum(1 for item in checks if item.verdict == "contradicted")
        avg_evidence_confidence = (
            sum(item.confidence for item in evidence) / len(evidence) if evidence else 0.0
        )
        claim_support_rate = supported / len(checks) if checks else 0.0
        contradiction_rate = contradicted / len(checks) if checks else 0.0
        return EvaluationMetrics(
            evidence_count=len(evidence),
            fact_check_count=len(checks),
            average_evidence_confidence=round(avg_evidence_confidence, 3),
            claim_support_rate=round(claim_support_rate, 3),
            contradiction_rate=round(contradiction_rate, 3),
            latency_seconds=round(latency_seconds, 3),
        )
