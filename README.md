# Autonomous Intelligence

**Multi-Agent Research & Decision Support System using Generative AI, Agentic AI and RAG**

Autonomous Intelligence is an AI Engineering portfolio project that turns a research question into an evidence-backed report through a coordinated set of specialized agents.

## What it demonstrates

- **Generative AI:** planning, synthesis, analysis and report generation
- **Agentic AI:** an orchestrator coordinates specialized agents in a multi-step workflow
- **Tool use:** the Research Agent can use the locally authenticated Codex CLI with live web search
- **RAG:** evidence is embedded and stored in a vector-store adapter for semantic retrieval
- **Evaluation:** a dedicated Fact-Checking Agent classifies claims as supported, mixed, insufficient or contradicted
- **AI Engineering:** typed schemas, API boundary, tests, Docker, CI and environment-based configuration

## Architecture

```text
User / API
    |
    v
Planner Agent
    |
    v
Research Agent ----> Web Search
    |                    |
    +------> Vector Store / RAG
    |
    v
Fact-Checking Agent
    |
    v
Analysis Agent
    |
    v
Report Agent
    |
    v
ResearchReport JSON
```

The implementation is intentionally framework-light. This makes the orchestration, model boundary, data contracts and retrieval mechanics visible instead of hiding them behind an agent framework.

## Agents

| Agent | Responsibility |
|---|---|
| Planner | Decomposes the research goal into subquestions and search queries |
| Researcher | Collects evidence and stores it in the vector store |
| Fact Checker | Challenges important claims and assigns evidence verdicts |
| Analyst | Produces findings, opportunities, risks and open questions |
| Reporter | Creates the executive summary and recommendations |
| Orchestrator | Controls the end-to-end workflow and research loop |

## Quick start

### 1. Create an environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
codex login status
```

The default configuration uses the locally installed Codex CLI and its existing ChatGPT login:

```env
AI_PROVIDER=codex
```

If Codex is not logged in yet, run `codex login`. The application invokes `codex exec`
non-interactively, ephemerally, and with a read-only sandbox. It does not need an API key in
`.env`.

Alternative providers are available explicitly:

- `AI_PROVIDER=demo` uses deterministic local fixtures for tests and presentations.
- `AI_PROVIDER=openai` uses `OPENAI_API_KEY` with the Responses, web search, and Embeddings APIs.

### 3. Run

```bash
uvicorn autonomous_intelligence.api:app --reload
```

Open:

- App: `http://localhost:8000`
- Swagger API: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

### 4. Call the API

```bash
curl -X POST http://localhost:8000/research \
  -H 'Content-Type: application/json' \
  -d '{"query":"How will agentic AI change enterprise software?"}'
```

### 5. Application CLI

```bash
python -m autonomous_intelligence.cli "What are the strongest enterprise use cases for AI agents?"
```

## Daily content pipeline

A second, parallel pipeline scans today's top news trends and generates a full multi-format
content bundle per trend: social posts (X, Instagram, Facebook, LinkedIn, TikTok), a short/reel
script + assembled vertical video, an Instagram/FB story (slide text + images), a podcast script +
narrated audio, and a video shot list.

```bash
python -m autonomous_intelligence.daily_content_cli
# or: make daily-content
```

Output is written to `output/<YYYY-MM-DD>/trend-<n>/` (`content.json`, `images/`, `audio/`,
`video/short.mp4`), plus a top-level `manifest.json` and `daily_report.md`.

`DAILY_TRENDS_COUNT` controls how many trends are processed per run (default 3). Image
(`gpt-image-1`) and speech (`gpt-4o-mini-tts`) generation require model access on the OpenAI
project and are only used when `AI_PROVIDER=openai`; if unavailable, `ImageGeneratorAgent` and
`AudioNarratorAgent` fall back to a labeled placeholder image and a silent audio track so the rest
of the pipeline (including real video assembly) still runs end to end - `demo` mode and "real key,
no model access" take the same fallback path. Video assembly uses `moviepy` + the `imageio-ffmpeg`
bundled binary, no system `ffmpeg` required.

## Workflow

```text
Goal
  ↓
Plan
  ↓
Research / tool use
  ↓
Store evidence + embeddings
  ↓
Check evidence sufficiency
  ↓
Fact-check claims
  ↓
Analyze opportunities / risks
  ↓
Generate report
```

`MAX_RESEARCH_ITERATIONS` and `MIN_EVIDENCE_ITEMS` control the small autonomous research loop.

## API response model

A research run produces:

```json
{
  "query": "...",
  "executive_summary": "...",
  "plan": {},
  "evidence": [],
  "fact_checks": [],
  "analysis": {
    "key_findings": [],
    "opportunities": [],
    "risks": [],
    "open_questions": []
  },
  "evaluation": {
    "evidence_count": 3,
    "claim_support_rate": 1.0,
    "latency_seconds": 0.42
  },
  "recommendations": [],
  "mode": "codex"
}
```

## Tests

```bash
pytest -q
ruff check src tests
```

Tests run without an API key.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

The container defaults to demo mode because it does not contain the host Codex CLI login. Set
`DOCKER_AI_PROVIDER=openai` and provide `OPENAI_API_KEY` to use the API provider in Docker.

## Production roadmap

1. Replace the educational in-memory vector store with **PostgreSQL + pgvector**.
2. Add source normalization, canonical URLs and source-quality scoring.
3. Add document ingestion for PDF, DOCX and web pages.
4. Add tracing for every agent/tool/model call.
5. Build an evaluation suite for groundedness, retrieval quality, task success, latency and token cost.
6. Add human approval gates for high-impact actions.
7. Add authentication, rate limiting and persistent research sessions.
8. Add background job infrastructure for long research tasks.
9. Add observability dashboards and regression evaluation in CI.

## Suggested project-work research question

> **How can a multi-agent Generative AI system improve autonomous research while maintaining factual reliability, traceability and controllable autonomy?**

Possible evaluation dimensions:

- answer groundedness
- source coverage
- claim support rate
- retrieval precision / recall
- latency
- token cost
- success rate by research task type

## Security notes

Treat web and document content as untrusted input. Do not allow retrieved text to directly change tool permissions or system instructions. Use allow-listed tools, schema validation, permission boundaries and human approval for consequential actions.

## License

MIT
