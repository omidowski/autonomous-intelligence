"""Tests fuer die schlanke Flask-App (`flask_app/`).

Laeuft ohne LLM-Provider: `generation.generate()` faellt dann auf den
deterministischen Offline-Generator zurueck, der dasselbe Schema erfuellt.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from flask_app import create_app
from flask_app.analysis import compare
from flask_app.generation import offline_result
from flask_app.models import CRITERIA, GenerationResult
from flask_app.prompts import (
    CHAIN_OF_THOUGHT,
    DEFAULT_TECHNIQUES,
    FEW_SHOT,
    build_instructions,
    normalize_techniques,
    render_history,
)


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "flask_app.db")


@pytest.fixture()
def client(db_path):
    app = create_app(db_path=db_path, testing=True)
    with app.test_client() as test_client:
        yield test_client


def start_conversation(client, **overrides):
    body = {
        "title": "Launch Fruehwarnsystem",
        "platform": "linkedin",
        "audience": "Head of Operations im Mittelstand",
        "brand_voice": "sachlich, konkret, ohne Buzzwords",
    }
    body.update(overrides)
    response = client.post("/api/conversations", json=body)
    return response


# --- Endpoints -----------------------------------------------------------


def test_health_reports_configuration(client):
    payload = client.get("/api/health").get_json()
    assert payload["status"] == "ok"
    assert set(payload["criteria"]) == set(CRITERIA)
    assert payload["prompt_techniques"] == [FEW_SHOT, CHAIN_OF_THOUGHT]


def test_create_conversation_persists_row(client, db_path):
    response = start_conversation(client)
    assert response.status_code == 201
    conversation = response.get_json()["conversation"]
    assert conversation["platform"] == "linkedin"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM conversations WHERE id = ?", (conversation["id"],)
    ).fetchone()
    conn.close()
    assert row["title"] == "Launch Fruehwarnsystem"


def test_create_conversation_rejects_unknown_platform(client):
    response = start_conversation(client, platform="myspace")
    assert response.status_code == 400
    assert response.get_json()["error"] == "bad_request"


def test_create_conversation_requires_fields(client):
    response = client.post("/api/conversations", json={"title": "Nur ein Titel"})
    assert response.status_code == 400
    assert "platform" in response.get_json()["detail"]


def test_generate_returns_structured_output_and_analysis(client):
    conversation_id = start_conversation(client).get_json()["conversation"]["id"]
    response = client.post(
        f"/api/conversations/{conversation_id}/generate",
        json={"brief": "Wir starten ein Tool, das Lieferverzoegerungen 48h vorher meldet."},
    )
    assert response.status_code == 201
    payload = response.get_json()

    # Structured output: validiert erneut gegen das Schema.
    result = GenerationResult.model_validate(payload["generation"])
    assert sorted(variant.variant for variant in result.variants) == ["A", "B"]

    analysis = payload["comparative_analysis"]
    assert analysis["platform"] == "linkedin"
    assert {item["criterion"] for item in analysis["per_criterion"]} == set(CRITERIA)
    assert analysis["verdict_consistent"] is True
    assert set(analysis["length_check"]) == {"A", "B"}


def test_generate_writes_both_turns_to_messages_table(client, db_path):
    conversation_id = start_conversation(client).get_json()["conversation"]["id"]
    client.post(
        f"/api/conversations/{conversation_id}/generate", json={"brief": "Erster Aufschlag."}
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq", (conversation_id,)
    ).fetchall()
    conn.close()

    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["payload_json"] is None
    stored = json.loads(rows[1]["payload_json"])
    assert "generation" in stored and "comparative_analysis" in stored


def test_generate_rejects_unknown_conversation(client):
    response = client.post("/api/conversations/999/generate", json={"brief": "Hallo"})
    assert response.status_code == 404


def test_generate_requires_brief(client):
    conversation_id = start_conversation(client).get_json()["conversation"]["id"]
    response = client.post(f"/api/conversations/{conversation_id}/generate", json={})
    assert response.status_code == 400


def test_generate_rejects_unknown_technique(client):
    conversation_id = start_conversation(client).get_json()["conversation"]["id"]
    response = client.post(
        f"/api/conversations/{conversation_id}/generate",
        json={"brief": "Test", "techniques": ["magic"]},
    )
    assert response.status_code == 400
    assert "magic" in response.get_json()["detail"]


# --- Konversationshistorie ----------------------------------------------


def test_history_grows_and_is_fed_back_into_the_next_turn(client):
    conversation_id = start_conversation(client).get_json()["conversation"]["id"]

    first = client.post(
        f"/api/conversations/{conversation_id}/generate", json={"brief": "Erster Turn."}
    ).get_json()
    second = client.post(
        f"/api/conversations/{conversation_id}/generate", json={"brief": "Zweiter Turn."}
    ).get_json()

    assert first["history_turns_used"] == 0
    assert second["history_turns_used"] == 2  # user + assistant aus Turn 1
    assert second["turn"] == 4


def test_get_conversation_returns_full_history(client):
    conversation_id = start_conversation(client).get_json()["conversation"]["id"]
    client.post(f"/api/conversations/{conversation_id}/generate", json={"brief": "Turn eins."})
    client.post(f"/api/conversations/{conversation_id}/generate", json={"brief": "Turn zwei."})

    payload = client.get(f"/api/conversations/{conversation_id}").get_json()
    assert payload["message_count"] == 4
    assert [message["role"] for message in payload["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert payload["latest_comparative_analysis"]["platform"] == "linkedin"


def test_get_conversation_404_for_unknown_id(client):
    assert client.get("/api/conversations/4242").status_code == 404


def test_list_conversations_filters_and_counts(client):
    start_conversation(client)
    start_conversation(client, title="TikTok-Teaser", platform="tiktok")

    everything = client.get("/api/conversations").get_json()
    assert everything["count"] == 2

    filtered = client.get("/api/conversations?platform=tiktok").get_json()
    assert filtered["count"] == 1
    assert filtered["conversations"][0]["title"] == "TikTok-Teaser"


def test_list_conversations_rejects_bad_limit(client):
    assert client.get("/api/conversations?limit=0").status_code == 400
    assert client.get("/api/conversations?limit=abc").status_code == 400


# --- Prompt-Engineering --------------------------------------------------


def test_normalize_techniques_defaults_and_validation():
    assert normalize_techniques(None) == list(DEFAULT_TECHNIQUES)
    assert normalize_techniques([FEW_SHOT]) == [FEW_SHOT]
    assert normalize_techniques(CHAIN_OF_THOUGHT) == [CHAIN_OF_THOUGHT]
    with pytest.raises(ValueError):
        normalize_techniques(["nope"])


def test_instructions_include_only_selected_techniques():
    few_shot_only = build_instructions([FEW_SHOT])
    assert "Few-Shot" in few_shot_only
    assert "Schritt fuer Schritt" not in few_shot_only

    cot_only = build_instructions([CHAIN_OF_THOUGHT])
    assert "Schritt fuer Schritt" in cot_only
    assert "Few-Shot" not in cot_only

    both = build_instructions([FEW_SHOT, CHAIN_OF_THOUGHT])
    assert "Few-Shot" in both and "Schritt fuer Schritt" in both


def test_render_history_is_bounded_and_ordered():
    turns = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"turn-{index}"}
        for index in range(10)
    ]
    rendered = render_history(turns)
    assert "turn-9" in rendered
    assert "turn-0" not in rendered  # ueber das Limit hinaus abgeschnitten
    assert rendered.index("turn-4") < rendered.index("turn-9")


def test_render_history_is_empty_for_first_turn():
    assert render_history([]) == ""


# --- Vergleichende Analyse ----------------------------------------------


def _result_with_scores(scores: dict[str, tuple[int, int]], winner: str) -> GenerationResult:
    base = offline_result(
        brief="Testbriefing", platform="linkedin", audience="Ops", brand_voice="sachlich"
    ).model_dump()
    base["analysis"]["criteria"] = [
        {
            "criterion": name,
            "score_a": scores[name][0],
            "score_b": scores[name][1],
            "rationale": "Testbegruendung fuer dieses Kriterium.",
        }
        for name in CRITERIA
    ]
    base["analysis"]["winner"] = winner
    return GenerationResult.model_validate(base)


def test_platform_weighting_can_flip_the_winner():
    # A gewinnt beim Hook, B bei Markenstimme und Zielgruppenrelevanz.
    scores = {
        "hook_strength": (5, 3),
        "brand_voice_fit": (3, 5),
        "platform_fit": (4, 4),
        "cta_clarity": (4, 4),
        "audience_relevance": (4, 4),
    }
    result = _result_with_scores(scores, winner="B")

    linkedin = compare(result, platform="linkedin")
    tiktok = compare(result, platform="tiktok")

    assert linkedin["computed_winner"] == "B"
    assert tiktok["computed_winner"] == "A"
    assert linkedin["totals"]["raw_a"] == linkedin["totals"]["raw_b"]  # ungewichtet Gleichstand


def test_inconsistent_model_verdict_is_flagged():
    scores = {name: (5, 2) for name in CRITERIA}
    result = _result_with_scores(scores, winner="B")  # widerspricht den Punkten
    comparison = compare(result, platform="linkedin")

    assert comparison["computed_winner"] == "A"
    assert comparison["model_winner"] == "B"
    assert comparison["verdict_consistent"] is False


def test_length_check_uses_platform_limit():
    payload = offline_result(
        brief="Kurzes Briefing", platform="x", audience="Devs", brand_voice="knapp"
    ).model_dump()
    payload["variants"][0]["body"] = "w" * 400  # ueber dem X-Limit von 280
    result = GenerationResult.model_validate(payload)

    comparison = compare(result, platform="x")
    assert comparison["length_check"]["A"]["limit"] == 280
    assert comparison["length_check"]["A"]["within_limit"] is False
    assert comparison["length_check"]["A"]["over_by"] == 120
    assert comparison["length_check"]["B"]["within_limit"] is True


def test_offline_result_matches_schema():
    result = offline_result(
        brief="Ein Briefing", platform="linkedin", audience="Ops", brand_voice="sachlich"
    )
    assert isinstance(result, GenerationResult)
    assert len(result.analysis.criteria) == len(CRITERIA)
