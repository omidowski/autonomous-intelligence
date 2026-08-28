"""Serialize generated content (research reports and daily/custom content
runs) to the download formats every competitor offers - Markdown for
pasting into a doc, JSON for piping into another tool, CSV for a
spreadsheet of just the social posts.

`api.py` exposes these as `GET /research/{id}/export` and
`GET /library/{run_id}/export`; this module has no FastAPI or DB
dependency so the shaping logic stays unit-testable on its own.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Literal

ExportFormat = Literal["md", "json", "csv"]
FORMATS: tuple[ExportFormat, ...] = ("md", "json", "csv")

MEDIA_TYPES: dict[ExportFormat, str] = {
    "md": "text/markdown; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}


def _selected_social_posts(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """One post per platform: the A/B variant the company picked (from the
    `variant_selections` map `api.py` merges onto each bundle), or "A"
    (falling back to the first seen) when nothing was selected."""
    selected: dict[str, str] = bundle.get("variant_selections", {})
    by_platform: dict[str, dict[str, Any]] = {}
    for post in bundle.get("social_posts", []):
        platform = post.get("platform", "")
        want = selected.get(platform, "A")
        chosen = by_platform.get(platform)
        if chosen is None or post.get("variant") == want:
            by_platform[platform] = post
    return list(by_platform.values())


# --- research report -----------------------------------------------------


def research_to_markdown(report: dict[str, Any]) -> str:
    a = report.get("analysis", {})
    lines = [
        f"# {report.get('query', 'Research report')}",
        "",
        "## Executive summary",
        "",
        report.get("executive_summary", ""),
        "",
        "## Recommendations",
        "",
        *[f"- {item}" for item in report.get("recommendations", [])],
        "",
        "## Key findings",
        "",
        *[f"- {item}" for item in a.get("key_findings", [])],
        "",
        "## Opportunities",
        "",
        *[f"- {item}" for item in a.get("opportunities", [])],
        "",
        "## Risks",
        "",
        *[f"- {item}" for item in a.get("risks", [])],
        "",
        "## Open questions",
        "",
        *[f"- {item}" for item in a.get("open_questions", [])],
        "",
        "## Evidence",
        "",
    ]
    for ev in report.get("evidence", []):
        lines.append(f"- **{ev.get('title', '')}** ({ev.get('source', '')})")
    return "\n".join(lines).rstrip() + "\n"


def research_to_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "item"])
    writer.writerow(["query", report.get("query", "")])
    writer.writerow(["executive_summary", report.get("executive_summary", "")])
    for item in report.get("recommendations", []):
        writer.writerow(["recommendation", item])
    a = report.get("analysis", {})
    for key in ("key_findings", "opportunities", "risks", "open_questions"):
        for item in a.get(key, []):
            writer.writerow([key, item])
    return buf.getvalue()


# --- daily / custom content run ---------------------------------------------


def content_to_markdown(run: dict[str, Any]) -> str:
    lines = [f"# Content for {run.get('date', 'run')}", ""]
    for bundle in run.get("bundles", []):
        trend = bundle.get("trend", {})
        lines += [f"## {trend.get('title', 'Untitled')}", ""]
        if trend.get("summary"):
            lines += [trend["summary"], ""]
        lines += ["### Social posts", ""]
        for post in _selected_social_posts(bundle):
            tags = " ".join(post.get("hashtags", []))
            suffix = f"  \n{tags}" if tags else ""
            lines += [f"**{post.get('platform', '')}** — {post.get('text', '')}{suffix}", ""]
        script = bundle.get("short_script") or {}
        if script:
            lines += [
                "### Short script",
                "",
                f"- Hook: {script.get('hook', '')}",
                *[f"- {beat}" for beat in script.get("beats", [])],
                f"- CTA: {script.get('cta', '')}",
                "",
            ]
    return "\n".join(lines).rstrip() + "\n"


def content_to_csv(run: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["trend", "platform", "text", "hashtags"])
    for bundle in run.get("bundles", []):
        title = bundle.get("trend", {}).get("title", "")
        for post in _selected_social_posts(bundle):
            writer.writerow(
                [title, post.get("platform", ""), post.get("text", ""), " ".join(post.get("hashtags", []))]
            )
    return buf.getvalue()


# --- dispatch ----------------------------------------------------------


def render(kind: Literal["research", "content"], data: dict[str, Any], fmt: ExportFormat) -> str:
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported export format: {fmt!r}. Use one of {', '.join(FORMATS)}.")
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False)
    if kind == "research":
        return research_to_markdown(data) if fmt == "md" else research_to_csv(data)
    return content_to_markdown(data) if fmt == "md" else content_to_csv(data)


__all__ = ["FORMATS", "MEDIA_TYPES", "ExportFormat", "render"]
