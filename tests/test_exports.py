"""Covers `exports.py` (pure shaping, no I/O) and the
`/research/{id}/export` + `/library/{run_id}/export` endpoints.
"""

import csv
import io
import json

import pytest

from autonomous_intelligence import exports

_RESEARCH = {
    "query": "State of agentic AI",
    "executive_summary": "It is early but moving fast.",
    "recommendations": ["Invest in evals", "Keep humans in the loop"],
    "analysis": {
        "key_findings": ["Tool use is the unlock"],
        "opportunities": ["Vertical agents"],
        "risks": ["Prompt injection"],
        "open_questions": ["Who owns the memory?"],
    },
    "evidence": [{"title": "A paper", "source": "arxiv.org"}],
}

_RUN = {
    "date": "2026-08-27",
    "bundles": [
        {
            "trend": {"title": "Rockets", "summary": "They go up."},
            "variant_selections": {"x": "B"},
            "social_posts": [
                {"platform": "x", "text": "A-side", "variant": "A", "hashtags": ["#a"]},
                {"platform": "x", "text": "B-side", "variant": "B", "hashtags": ["#b"]},
                {"platform": "linkedin", "text": "Pro take", "variant": "A", "hashtags": []},
            ],
            "short_script": {"hook": "Look up", "beats": ["b1", "b2"], "cta": "Subscribe"},
        }
    ],
}


def test_render_rejects_unknown_format():
    with pytest.raises(ValueError):
        exports.render("research", _RESEARCH, "pdf")  # type: ignore[arg-type]


def test_research_markdown_has_all_sections():
    md = exports.render("research", _RESEARCH, "md")
    for heading in ("# State of agentic AI", "## Executive summary", "## Recommendations", "## Risks"):
        assert heading in md
    assert "- Invest in evals" in md


def test_research_csv_is_parseable_and_flat():
    rows = list(csv.reader(io.StringIO(exports.render("research", _RESEARCH, "csv"))))
    assert rows[0] == ["section", "item"]
    assert ["recommendation", "Invest in evals"] in rows
    assert ["risks", "Prompt injection"] in rows


def test_content_export_uses_selected_ab_variant():
    md = exports.render("content", _RUN, "md")
    assert "B-side" in md and "A-side" not in md  # x picked variant B
    assert "Pro take" in md  # linkedin has no selection -> variant A

    rows = list(csv.reader(io.StringIO(exports.render("content", _RUN, "csv"))))
    assert rows[0] == ["trend", "platform", "text", "hashtags"]
    x_row = next(r for r in rows if r[1] == "x")
    assert x_row[2] == "B-side"


def test_json_export_round_trips():
    assert json.loads(exports.render("content", _RUN, "json")) == _RUN


# --- endpoints ---------------------------------------------------------


def test_research_export_endpoint(client, signup):
    signup(client)
    client.post("/research", json={"query": "a sufficiently long research query"})
    rid = client.get("/research/history").json()[0]["id"]

    r = client.get(f"/research/{rid}/export", params={"format": "md"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("# ")

    assert client.get(f"/research/{rid}/export", params={"format": "pdf"}).status_code == 400
    assert client.get("/research/99999/export").status_code == 404


def test_library_export_endpoint(client, signup):
    signup(client)
    client.post("/daily-content/run")
    run_id = client.get("/library").json()[0]["id"]

    r = client.get(f"/library/{run_id}/export", params={"format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["trend", "platform", "text", "hashtags"]
    assert len(rows) > 1

    assert client.get("/library/not-a-valid-id/export").status_code == 400
    assert client.get("/library/2020-01-01/export").status_code == 404
