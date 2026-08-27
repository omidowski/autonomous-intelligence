from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from .config import Settings

_CODEX_EXECUTION_LIMIT = threading.BoundedSemaphore(value=1)
_CODEX_ENVIRONMENT_ALLOWLIST = {
    "ALL_PROXY",
    "CODEX_HOME",
    "HOME",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NODE_EXTRA_CA_CERTS",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TEMP",
    "TMP",
    "TMPDIR",
}


def _safe_codex_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in _CODEX_ENVIRONMENT_ALLOWLIST}


class MediaUnavailableError(RuntimeError):
    """Raised when image/speech generation isn't available (403, removed model, demo/non-openai provider)."""


class LLMProvider:
    """Adapter for Codex CLI, OpenAI Responses API, and deterministic demo mode."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.resolved_provider
        self.client = None
        self.codex_executable: str | None = None
        self._codex_ready = False

        if self.provider == "openai":
            if not settings.openai_api_key:
                raise RuntimeError("AI_PROVIDER=openai requires OPENAI_API_KEY.")
            from openai import OpenAI

            self.client = OpenAI(api_key=settings.openai_api_key)
        elif self.provider == "openrouter":
            if not settings.openrouter_api_key:
                raise RuntimeError("AI_PROVIDER=openrouter requires OPENROUTER_API_KEY.")
            from openai import OpenAI

            self.client = OpenAI(
                api_key=settings.openrouter_api_key,
                base_url=settings.openrouter_base_url,
            )
        elif self.provider == "codex":
            self.codex_executable = shutil.which(settings.codex_cli_path)
            if self.codex_executable is None:
                raise RuntimeError(f"Codex CLI executable not found: {settings.codex_cli_path!r}.")

    @property
    def is_demo(self) -> bool:
        return self.provider == "demo"

    @property
    def mode(self) -> Literal["live", "codex", "openrouter", "demo"]:
        if self.provider == "openai":
            return "live"
        return self.provider

    def text(self, instructions: str, prompt: str) -> str:
        if self.provider == "demo":
            return self._demo_text(prompt)
        if self.provider == "codex":
            return self._codex_exec(instructions, prompt, web_search=False)
        if self.client is None:
            raise RuntimeError("LLM client is not initialized.")
        if self.provider == "openrouter":
            response = self.client.chat.completions.create(
                model=self.settings.openrouter_model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content or ""
        response = self.client.responses.create(
            model=self.settings.openai_model,
            instructions=instructions,
            input=prompt,
        )
        return response.output_text

    def text_via_openrouter(self, instructions: str, prompt: str, *, model: str) -> str:
        """Routes a single text call through OpenRouter with an explicit
        `model`, independent of `self.provider` - lets content generation use
        Claude/Gemini/Grok/etc. via one OpenRouter account even when the
        app's primary `AI_PROVIDER` is `openai` (or anything else). Requires
        `settings.openrouter_api_key` regardless of the primary provider."""
        if self.provider == "demo":
            return self._demo_text(prompt)
        if not self.settings.openrouter_api_key:
            raise RuntimeError(
                "text_via_openrouter() requires OPENROUTER_API_KEY to be set, regardless of "
                "AI_PROVIDER."
            )
        from openai import OpenAI

        client = OpenAI(
            api_key=self.settings.openrouter_api_key, base_url=self.settings.openrouter_base_url
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""

    def generate_text(self, instructions: str, prompt: str, *, content_model: str | None = None) -> str:
        """Convenience wrapper: routes through `text_via_openrouter()` when
        `content_model` is given, otherwise the primary-provider `text()` -
        the same ternary every content/research agent needs, in one place."""
        if content_model:
            return self.text_via_openrouter(instructions, prompt, model=content_model)
        return self.text(instructions, prompt)

    def web_research(self, query: str) -> str:
        if self.provider == "demo":
            return self._demo_research(query)
        if self.provider == "codex":
            return self._codex_exec(
                (
                    "You are a research agent. Use live web search, prioritize primary and "
                    "reputable sources, separate facts from inference, and include source "
                    "names and URLs."
                ),
                query,
                web_search=True,
            )
        if self.client is None:
            raise RuntimeError("LLM client is not initialized.")
        instructions = (
            "You are a research agent. Search the web, prioritize primary and reputable sources, "
            "separate facts from inference, and include source names/URLs in the answer."
        )
        if self.provider == "openrouter":
            response = self.client.chat.completions.create(
                model=self.settings.openrouter_model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": query},
                ],
                extra_body={"plugins": [{"id": "web"}]},
            )
            return response.choices[0].message.content or ""
        response = self.client.responses.create(
            model=self.settings.openai_model,
            instructions=instructions,
            input=query,
            tools=[{"type": "web_search"}],
        )
        return response.output_text

    def embedding(self, text: str) -> list[float]:
        if self.provider in {"demo", "codex"}:
            return self._demo_embedding(text)
        if self.client is None:
            raise RuntimeError("LLM client is not initialized.")
        model = (
            self.settings.openrouter_embedding_model
            if self.provider == "openrouter"
            else self.settings.openai_embedding_model
        )
        response = self.client.embeddings.create(model=model, input=text)
        return response.data[0].embedding

    def image(self, prompt: str, *, size: str | None = None, quality: str | None = None) -> bytes:
        """`size`/`quality` let a caller override the app-wide
        `settings.openai_image_size` default (e.g. a per-company choice from
        `agents/image_generator.py`) - `quality` is `gpt-image-1`'s own
        `"low"|"medium"|"high"|"auto"` parameter, "auto" (OpenAI's own
        default) when not given."""
        if self.provider != "openai":
            raise MediaUnavailableError(
                f"Image generation requires AI_PROVIDER=openai (got {self.provider!r})."
            )
        try:
            response = self.client.images.generate(
                model=self.settings.openai_image_model,
                prompt=prompt,
                size=size or self.settings.openai_image_size,
                quality=quality or "auto",
                n=1,
            )
        except Exception as exc:  # openai SDK raises various error subclasses (403, 400, network, ...)
            raise MediaUnavailableError(f"Image generation failed: {exc}") from exc
        b64 = response.data[0].b64_json
        if not b64:
            raise MediaUnavailableError("Image generation returned no image data.")
        return base64.b64decode(b64)

    def speech(self, text: str) -> bytes:
        if self.provider != "openai":
            raise MediaUnavailableError(
                f"Speech synthesis requires AI_PROVIDER=openai (got {self.provider!r})."
            )
        try:
            response = self.client.audio.speech.create(
                model=self.settings.openai_tts_model,
                voice=self.settings.openai_tts_voice,
                input=text,
                response_format="wav",
            )
        except Exception as exc:
            raise MediaUnavailableError(f"Speech synthesis failed: {exc}") from exc
        return response.read()

    def _ensure_codex_ready(self) -> None:
        if self._codex_ready:
            return
        if self.codex_executable is None:
            raise RuntimeError("Codex CLI is not configured.")

        try:
            result = subprocess.run(
                [self.codex_executable, "login", "status"],
                capture_output=True,
                check=False,
                env=_safe_codex_environment(),
                text=True,
                timeout=min(self.settings.codex_timeout_seconds, 15),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Could not check Codex CLI authentication.") from exc

        if result.returncode != 0:
            raise RuntimeError("Codex CLI is not logged in. Run `codex login` first.")
        self._codex_ready = True

    def _codex_exec(self, instructions: str, prompt: str, *, web_search: bool) -> str:
        self._ensure_codex_ready()
        if self.codex_executable is None:
            raise RuntimeError("Codex CLI is not configured.")

        with tempfile.TemporaryDirectory(prefix="autonomous-intelligence-codex-") as temp_dir:
            output_path = Path(temp_dir) / "last-message.txt"
            command = [
                self.codex_executable,
                "-a",
                "never",
                "-s",
                "read-only",
                "-C",
                temp_dir,
            ]
            if self.settings.codex_model:
                command.extend(["-m", self.settings.codex_model])
            if web_search:
                command.append("--search")
            command.extend(
                [
                    "exec",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--skip-git-repo-check",
                    "--color",
                    "never",
                    "--output-last-message",
                    str(output_path),
                    "-",
                ]
            )

            environment = _safe_codex_environment()
            combined_prompt = (
                f"{instructions.strip()}\n\n{prompt.strip()}\n\n"
                "Do not modify files or run shell commands. Return only the requested final content."
            )

            try:
                with _CODEX_EXECUTION_LIMIT:
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        check=False,
                        cwd=temp_dir,
                        env=environment,
                        input=combined_prompt,
                        text=True,
                        timeout=self.settings.codex_timeout_seconds,
                    )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"Codex CLI timed out after {self.settings.codex_timeout_seconds} seconds."
                ) from exc
            except OSError as exc:
                raise RuntimeError("Could not start Codex CLI.") from exc

            if result.returncode != 0:
                raise RuntimeError(f"Codex CLI failed with exit code {result.returncode}.")
            if not output_path.is_file():
                raise RuntimeError("Codex CLI did not produce a final response file.")

            output = output_path.read_text(encoding="utf-8").strip()
            if not output:
                raise RuntimeError("Codex CLI produced an empty final response.")
            return output

    @staticmethod
    def extract_json(text: str) -> Any:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                cleaned,
                flags=re.IGNORECASE | re.DOTALL,
            )
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.DOTALL)
        candidate = match.group(1) if match else cleaned
        return json.loads(candidate)

    @staticmethod
    def _demo_text(prompt: str) -> str:
        lower = prompt.lower()
        if "search_query" in lower and "source_urls" in lower:
            return LLMProvider._demo_trends(prompt)
        if "social_posts" in lower and "podcast_script" in lower:
            return LLMProvider._demo_content_bundle()
        if "subquestions" in lower and "search_queries" in lower:
            return json.dumps(
                {
                    "objective": "Build an evidence-backed answer to the research question.",
                    "subquestions": [
                        "What is changing technically?",
                        "What is the measurable business impact?",
                        "What are the main risks and constraints?",
                    ],
                    "search_queries": [
                        "agentic AI enterprise adoption evidence",
                        "LLM agents reliability evaluation enterprise",
                        "generative AI agent governance security",
                    ],
                }
            )
        if "recommendations" in lower:
            return json.dumps(
                {
                    "executive_summary": (
                        "Agentic AI can automate multi-step knowledge work when systems are grounded, "
                        "evaluated and constrained by explicit tools and human approval boundaries."
                    ),
                    "recommendations": [
                        "Start with a narrow, measurable workflow.",
                        "Add retrieval, tracing and automated evaluations before expanding autonomy.",
                        "Require human approval for high-impact actions.",
                    ],
                }
            )
        if "key_findings" in lower:
            return json.dumps(
                {
                    "key_findings": [
                        "Agentic systems combine LLM reasoning with tools, memory and iterative control loops.",
                        "Reliability, evaluation and permission boundaries are central engineering challenges.",
                        "The strongest near-term use cases are bounded workflows with measurable outcomes.",
                    ],
                    "opportunities": [
                        "Workflow automation",
                        "Research acceleration",
                        "Decision support",
                    ],
                    "risks": [
                        "Hallucinations",
                        "Tool misuse",
                        "Cost and latency",
                        "Weak observability",
                    ],
                    "open_questions": ["Which tasks should remain human-approved?"],
                }
            )
        return "Demo response"

    @staticmethod
    def _demo_trends(prompt: str) -> str:
        match = re.search(r"exactly (\d+) distinct", prompt)
        count = int(match.group(1)) if match else 3
        topics = [
            {
                "title": "Major economies debate AI regulation frameworks",
                "summary": (
                    "Lawmakers in several regions are advancing new rules for AI safety, "
                    "transparency and liability, with industry pushing back on scope."
                ),
                "category": "technology",
                "source_urls": ["https://example.com/ai-regulation"],
                "search_query": "AI regulation policy news today",
            },
            {
                "title": "Global markets react to central bank rate signals",
                "summary": (
                    "Equity and bond markets moved after policymakers signaled a shift in "
                    "interest-rate guidance, with investors repricing growth expectations."
                ),
                "category": "business",
                "source_urls": ["https://example.com/markets-rates"],
                "search_query": "central bank interest rate news today",
            },
            {
                "title": "Breakthrough reported in clean energy storage",
                "summary": (
                    "Researchers announced a battery storage advance that could improve "
                    "grid reliability for renewable energy sources."
                ),
                "category": "science",
                "source_urls": ["https://example.com/energy-storage"],
                "search_query": "clean energy storage breakthrough news",
            },
            {
                "title": "Major sports league opens new season with rule changes",
                "summary": (
                    "A widely followed sports league kicked off its season under revised "
                    "rules intended to speed up play and improve safety."
                ),
                "category": "sports",
                "source_urls": ["https://example.com/sports-season"],
                "search_query": "sports league new season rule changes",
            },
            {
                "title": "Streaming platform releases record-breaking series",
                "summary": (
                    "A new series drew record viewership in its opening weekend, renewing "
                    "debate about streaming's effect on traditional media."
                ),
                "category": "entertainment",
                "source_urls": ["https://example.com/streaming-series"],
                "search_query": "streaming series viewership record news",
            },
        ]
        items = []
        for index in range(count):
            topic = topics[index % len(topics)]
            items.append({"rank": index + 1, **topic})
        return json.dumps(items)

    @staticmethod
    def _demo_content_bundle() -> str:
        return json.dumps(
            {
                "social_posts": [
                    {
                        "platform": "x",
                        "variant": "A",
                        "text": "Here's what today's top trend actually means for you \U0001f9f5",
                        "hashtags": ["#news", "#trending"],
                    },
                    {
                        "platform": "x",
                        "variant": "B",
                        "text": "The headline everyone's sharing today - and the part they're leaving out.",
                        "hashtags": ["#news"],
                    },
                    {
                        "platform": "instagram",
                        "variant": "A",
                        "text": "Swipe to see why everyone's talking about this today.",
                        "hashtags": ["#news", "#dailytrend"],
                    },
                    {
                        "platform": "instagram",
                        "variant": "B",
                        "text": "This is the story behind today's biggest headline.",
                        "hashtags": ["#news"],
                    },
                    {
                        "platform": "facebook",
                        "variant": "A",
                        "text": "Today's big story, explained in plain language.",
                        "hashtags": ["#news"],
                    },
                    {
                        "platform": "facebook",
                        "variant": "B",
                        "text": "Here's why today's top story matters more than the headline suggests.",
                        "hashtags": ["#news"],
                    },
                    {
                        "platform": "linkedin",
                        "variant": "A",
                        "text": "What this trend means for teams and decision-makers.",
                        "hashtags": ["#news", "#analysis"],
                    },
                    {
                        "platform": "linkedin",
                        "variant": "B",
                        "text": "The strategic implications of today's top story, in three points.",
                        "hashtags": ["#analysis"],
                    },
                    {
                        "platform": "tiktok",
                        "variant": "A",
                        "text": "You need to know this before your next scroll \U0001f440",
                        "hashtags": ["#news", "#fyp"],
                    },
                    {
                        "platform": "tiktok",
                        "variant": "B",
                        "text": "Nobody's explaining today's story like this.",
                        "hashtags": ["#fyp"],
                    },
                    {
                        "platform": "threads",
                        "variant": "A",
                        "text": "Okay so this happened today - here's the short version.",
                        "hashtags": ["#news"],
                    },
                    {
                        "platform": "threads",
                        "variant": "B",
                        "text": "Genuinely curious what people think about today's top story.",
                        "hashtags": [],
                    },
                    {
                        "platform": "bluesky",
                        "variant": "A",
                        "text": "Today's story, no spin, just what happened.",
                        "hashtags": ["#news"],
                    },
                    {
                        "platform": "bluesky",
                        "variant": "B",
                        "text": "The one thing worth knowing about today's news.",
                        "hashtags": [],
                    },
                    {
                        "platform": "pinterest",
                        "variant": "A",
                        "text": "Save this if you want the quick version of today's biggest story.",
                        "hashtags": ["#news", "#dailytrend"],
                    },
                    {
                        "platform": "pinterest",
                        "variant": "B",
                        "text": "Pin this to catch up on today's story later.",
                        "hashtags": ["#dailytrend"],
                    },
                    {
                        "platform": "reddit",
                        "variant": "A",
                        "text": "Today's top story, summarized without the spin - discuss.",
                        "hashtags": [],
                    },
                    {
                        "platform": "reddit",
                        "variant": "B",
                        "text": "Curious what this community thinks about today's top story.",
                        "hashtags": [],
                    },
                    {
                        "platform": "youtube",
                        "variant": "A",
                        "text": "New video breaking down today's biggest story - up now.",
                        "hashtags": ["#news"],
                    },
                    {
                        "platform": "youtube",
                        "variant": "B",
                        "text": "We break down what today's top story actually means - watch now.",
                        "hashtags": [],
                    },
                ],
                "short_script": {
                    "hook": "You won't believe what just happened today.",
                    "beats": [
                        "Here's the headline everyone's talking about.",
                        "Here's why it actually matters.",
                        "Here's what happens next.",
                    ],
                    "cta": "Follow for tomorrow's biggest story.",
                    "est_duration_seconds": 45,
                },
                "story": {
                    "slides": [
                        {
                            "slide_number": 1,
                            "text": "Today's big story \U0001f447",
                            "image_prompt": "Bold editorial illustration representing breaking news, dramatic lighting",
                        },
                        {
                            "slide_number": 2,
                            "text": "Here's what's happening.",
                            "image_prompt": "Editorial illustration of the core event, clean composition",
                        },
                        {
                            "slide_number": 3,
                            "text": "Why it matters.",
                            "image_prompt": "Conceptual illustration of impact and consequences, warm tones",
                        },
                    ]
                },
                "podcast_script": {
                    "title": "Today's Trend, Explained",
                    "intro": "Welcome back — here's the story everyone's talking about today.",
                    "segments": [
                        "Let's start with what actually happened.",
                        "Now, here's why it matters more than it looks.",
                        "And here's what to watch for next.",
                    ],
                    "outro": "That's today's trend. See you tomorrow.",
                    "est_duration_minutes": 3,
                },
                "video_script": {
                    "title": "Today's Trend, Explained",
                    "shots": [
                        {
                            "shot_number": 1,
                            "description": "Host on camera delivering the hook.",
                            "duration_seconds": 5,
                            "on_screen_text": "Today's big story",
                        },
                        {
                            "shot_number": 2,
                            "description": "B-roll or graphic illustrating the event.",
                            "duration_seconds": 10,
                            "on_screen_text": None,
                        },
                        {
                            "shot_number": 3,
                            "description": "Host explains why it matters.",
                            "duration_seconds": 15,
                            "on_screen_text": "Why it matters",
                        },
                        {
                            "shot_number": 4,
                            "description": "Closing call-to-action.",
                            "duration_seconds": 5,
                            "on_screen_text": "Follow for more",
                        },
                    ],
                },
            }
        )

    @staticmethod
    def _demo_research(query: str) -> str:
        return (
            f"Demo evidence for: {query}\n"
            "Source: OpenAI / public technical documentation - LLM agents can combine models and tools.\n"
            "Source: NIST AI RMF - risk management and evaluation are important for deployed AI systems.\n"
            "Source: Academic agent research - iterative tool use introduces reliability and evaluation challenges."
        )

    @staticmethod
    def _demo_embedding(text: str, dims: int = 64) -> list[float]:
        vector = [0.0] * dims
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % dims
            vector[index] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]
