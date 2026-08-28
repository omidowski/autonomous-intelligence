"""Covers per-request content-model override (`model` on the research /
custom-content requests, `?model=` on daily-content) and the multi-model
`/custom-content/compare` fan-out.

Most tests monkeypatch the orchestrator to capture the `content_model`
kwarg rather than run the full generation pipeline; a couple run it end to
end in demo mode.
"""

import pytest
from pydantic import ValidationError

from autonomous_intelligence import api, model_catalog, plans
from autonomous_intelligence.models import (
    CustomContentRequest,
    MultiModelContentRequest,
    ResearchRequest,
)

CLAUDE = "anthropic/claude-sonnet-5"
GROK = "x-ai/grok-4.5"


# --- validation ------------------------------------------------------


def test_validate_content_model():
    assert model_catalog.validate_content_model("") is None
    assert model_catalog.validate_content_model(None) is None
    assert model_catalog.validate_content_model(CLAUDE) == CLAUDE
    with pytest.raises(ValueError):
        model_catalog.validate_content_model("openai/not-real")


def test_request_bodies_reject_unknown_model():
    ResearchRequest(query="a long enough query", model=CLAUDE)  # ok
    with pytest.raises(ValidationError):
        ResearchRequest(query="a long enough query", model="bogus/model")
    with pytest.raises(ValidationError):
        CustomContentRequest(topic="some topic", model="bogus/model")


def test_multi_model_request_dedups_and_needs_two():
    req = MultiModelContentRequest(topic="some topic", models=[CLAUDE, GROK, CLAUDE])
    assert req.models == [CLAUDE, GROK]
    with pytest.raises(ValidationError):
        MultiModelContentRequest(topic="some topic", models=[CLAUDE, CLAUDE])
    with pytest.raises(ValidationError):
        MultiModelContentRequest(topic="some topic", models=[CLAUDE, "nope"])


# --- per-request override reaches the orchestrator --------------------


@pytest.fixture
def capture_custom(monkeypatch):
    from autonomous_intelligence.content_models import DailyContentReport

    calls = []

    def _empty_report(run_id: str) -> DailyContentReport:
        return DailyContentReport(
            date=run_id, trends=[], bundles=[], output_dir="x", mode="demo"
        )

    def fake_run_custom_topic(self, topic, *, company_slug, **kwargs):
        calls.append({"topic": topic, **kwargs})
        return _empty_report(f"custom-{len(calls)}")

    def fake_run(self, *, company_slug, **kwargs):
        calls.append({"daily": True, **kwargs})
        return _empty_report("2026-08-27")

    monkeypatch.setattr(api.DailyContentOrchestrator, "run_custom_topic", fake_run_custom_topic)
    monkeypatch.setattr(api.DailyContentOrchestrator, "run", fake_run)
    return calls


def test_custom_run_uses_request_model_over_company_default(client, signup, capture_custom):
    signup(client)
    r = client.post("/custom-content/run", json={"topic": "edge ai", "model": GROK})
    assert r.status_code == 200
    assert capture_custom[-1]["content_model"] == GROK


def test_custom_run_falls_back_to_company_default(client, signup, capture_custom):
    signup(client)
    client.put("/auth/me/content-model", json={"content_model": CLAUDE})
    r = client.post("/custom-content/run", json={"topic": "edge ai"})
    assert r.status_code == 200
    assert capture_custom[-1]["content_model"] == CLAUDE


def test_daily_run_model_query_param(client, signup, capture_custom):
    signup(client)
    assert client.post("/daily-content/run", params={"model": CLAUDE}).status_code == 200
    assert capture_custom[-1]["content_model"] == CLAUDE
    assert client.post("/daily-content/run", params={"model": "nope"}).status_code == 400


def test_research_model_override(client, signup, monkeypatch):
    captured = {}
    signup(client)

    # Unknown model -> 422 before any work.
    assert client.post(
        "/research", json={"query": "a long enough query", "model": "bad/model"}
    ).status_code == 422

    monkeypatch.setattr(
        api.ResearchOrchestrator,
        "run",
        lambda self, request, **kw: captured.update(kw) or _stub_report(),
    )
    r = client.post("/research", json={"query": "a long enough query", "model": GROK})
    assert r.status_code == 200
    assert captured["content_model"] == GROK


def _stub_report():
    from autonomous_intelligence.models import (
        AnalysisResult,
        EvaluationMetrics,
        ResearchPlan,
        ResearchReport,
    )

    return ResearchReport(
        query="q",
        executive_summary="s",
        plan=ResearchPlan(objective="o", subquestions=[], search_queries=[]),
        evidence=[],
        fact_checks=[],
        analysis=AnalysisResult(key_findings=[], opportunities=[], risks=[], open_questions=[]),
        evaluation=EvaluationMetrics(
            evidence_count=0,
            fact_check_count=0,
            average_evidence_confidence=0.0,
            claim_support_rate=0.0,
            contradiction_rate=0.0,
            latency_seconds=0.0,
        ),
        recommendations=[],
        mode="demo",
    )


# --- /custom-content/compare ----------------------------------------


def test_compare_runs_each_model(client, signup, capture_custom):
    signup(client)
    r = client.post(
        "/custom-content/compare", json={"topic": "small language models", "models": [CLAUDE, GROK]}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["requested"] == 2
    assert body["generated"] == 2
    assert [item["model"] for item in body["results"]] == [CLAUDE, GROK]
    assert body["results"][0]["label"] == model_catalog.CONTENT_MODEL_CHOICES[CLAUDE]
    assert {c["content_model"] for c in capture_custom} == {CLAUDE, GROK}


def test_compare_skips_models_over_quota(client, signup, capture_custom, monkeypatch):
    monkeypatch.setitem(plans.PLAN_LIMITS["free"], "content_runs", 1)
    signup(client)
    body = client.post(
        "/custom-content/compare", json={"topic": "topic here", "models": [CLAUDE, GROK]}
    ).json()
    assert body["generated"] == 1
    assert body["skipped"] == 1
    assert [item["status"] for item in body["results"]] == ["generated", "skipped"]


def test_compare_rejects_single_model(client, signup):
    signup(client)
    assert client.post(
        "/custom-content/compare", json={"topic": "topic here", "models": [CLAUDE]}
    ).status_code == 422


def test_compare_end_to_end_produces_fetchable_runs(client, signup):
    """No mocking: both models run the real demo pipeline and each result's
    run_id resolves through the normal custom-content detail route."""
    signup(client)
    body = client.post(
        "/custom-content/compare",
        json={"topic": "the state of AI agents in 2026", "models": [CLAUDE, GROK]},
    ).json()
    assert body["generated"] == 2
    for item in body["results"]:
        detail = client.get(f"/custom-content/{item['run_id']}")
        assert detail.status_code == 200
        assert detail.json()["bundles"]
