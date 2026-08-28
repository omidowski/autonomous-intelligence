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

## Choosing the content model

Content and research generation can run on ChatGPT, Claude, Gemini or Grok - all through one
OpenRouter key (`OPENROUTER_API_KEY`), no per-vendor SDK. `GET /auth/content-model-choices`
lists the available ids.

There are three levels, most specific wins:

1. **Per request** - `"model"` in the body of `/research`, `/custom-content/run` and
   `/custom-content/bulk`, or `?model=` on `/daily-content/run`.
2. **Per company** - `PUT /auth/me/content-model` (owner only), the default for every run.
3. **App default** - `AI_PROVIDER` / `Settings.resolved_provider` when neither is set.

```bash
curl -X POST http://localhost:8000/custom-content/run \
  -H 'Content-Type: application/json' \
  -d '{"topic":"agentic AI in logistics","model":"anthropic/claude-sonnet-5"}'
```

### Comparing models side by side

`POST /custom-content/compare` generates the *same* topic with 2-4 models in one call and returns
every run next to the others, so a reviewer can keep the best one. Each model is a full pipeline,
metered and quota-checked on its own - a model that hits the monthly limit comes back as
`skipped` instead of failing the batch.

```bash
curl -X POST http://localhost:8000/custom-content/compare \
  -H 'Content-Type: application/json' \
  -d '{"topic":"small language models","models":["anthropic/claude-sonnet-5","x-ai/grok-4.5"]}'
```

## Subscriptions, quotas and exports

- **Billing** - real Stripe subscriptions: `/billing/plans`, `/billing/checkout`,
  `/billing/portal`, `/billing/status`, and a signature-verified, idempotent `/billing/webhook`
  that keeps `companies.subscription_plan`/`subscription_status` in sync with what Stripe
  actually charged. With no Stripe key configured these return 503 rather than faking success.
- **Plan quotas** - `plans.py` holds the monthly cap per plan per metered resource (`research`,
  `content_runs`), counted from the same `usage_log` rows behind `/usage`. Over the cap, a
  metered endpoint returns **402**. Every limit ships as `None` (unlimited), so enabling billing
  does not retroactively lock existing companies out - set an integer to start enforcing.
- **Exports** - `GET /research/{id}/export` and `GET /library/{run_id}/export` with
  `?format=md|json|csv`, returned as a download.
- **Bulk topics** - `POST /custom-content/bulk` runs up to 10 custom topics in one request,
  skipping (not failing) the ones over quota.

## Programmatic API keys

Drive the product from your own scripts instead of a browser session.

- `GET/POST/DELETE /api-keys` (owner only). The key is shown **once**, at creation;
  only its SHA-256 is stored.
- Use it as `Authorization: Bearer ai_live_...`.
- Keys act with **member** permissions: they can run research, generate and review content,
  publish, schedule and export - but cannot touch billing, team, webhooks or keys. A leaked
  key therefore cannot escalate into the account.
- Revocation takes effect on the next request; the row is kept so `last_used_at` stays
  auditable.

```bash
curl -H "Authorization: Bearer $AI_KEY" http://localhost:8000/library
```

## Publishing calendar

Approved content can be queued to go out later instead of only "publish now".

- `POST /daily-content/{date}/trend/{n}/schedule` with `{"scheduled_for": "...", "platforms": [...]}`
  queues one slot per platform. Re-posting the same slot is a no-op, not a duplicate.
- `GET /schedule` is the calendar (filter with `?status=pending|published|failed|canceled`),
  `DELETE /schedule/{id}` cancels a still-pending slot.
- The existing scheduler loop drains due slots every 60s. Approval is re-checked at send
  time: content rejected between scheduling and its slot is **failed, not posted**. A failed
  slot is never retried, so a broken bundle can't cause a retry storm.
- Because content can now sit queued for days, **approval can be withdrawn**
  (`approved -> rejected`, "Withdraw approval" in the UI). That single action stops every
  queued slot for the bundle at once, instead of having to cancel each one and risk missing
  one. It does not retract anything already published.

### Real vs. stubbed platforms

`GET /schedule/platforms` reports which platforms publish for real. LinkedIn, X and Facebook
go live per platform as soon as that platform's credentials are set (see `.env.example`);
everything else routes to the no-network stub publisher. Publishing to a platform whose
credentials are missing is reported as a **failed** publish - a post that did not happen never
looks like one that did.

## Outbound webhooks

Companies can subscribe to their own content events - the integration point competitors expose
for Zapier/n8n-style automation.

- `GET/POST/DELETE /webhooks` (owner only). The signing secret is returned **once**, at creation.
- Events: `content.submitted_for_review`, `content.approved`, `content.rejected`,
  `content.published`.
- Each delivery is signed: `X-AI-Signature: sha256=<hmac-sha256 of the raw body>`, with the event
  name in `X-AI-Event`.
- Delivery is best-effort and never blocks or fails the API call that triggered it. Targets must
  be public http(s) URLs - loopback, private and link-local addresses are rejected as a basic
  SSRF guard.

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
