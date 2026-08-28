"""Text-Generation: Prompt bauen, LLM aufrufen, Structured Output validieren.

Ablauf pro Aufruf:
1. Historie aus SQLite laden und in den Prompt rendern (`prompts.render_history`).
2. Instruktionen aus den gewaehlten Prompt-Techniken zusammensetzen.
3. LLM aufrufen, JSON extrahieren, gegen `GenerationResult` validieren.
4. Bei Schema-Verstoss genau einen Repair-Turn: dem Modell wird der konkrete
   Validierungsfehler zurueckgegeben.
5. Schlaegt auch das fehl (oder laeuft die App im Demo-Modus ohne Provider),
   greift ein deterministischer Offline-Generator, damit die API auch ohne
   API-Key vollstaendig und schema-konform antwortet.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from pydantic import ValidationError

from autonomous_intelligence.config import Settings
from autonomous_intelligence.llm import LLMProvider

from .models import CRITERIA, GenerationResult
from .prompts import build_instructions, build_prompt


@dataclass(frozen=True)
class GenerationOutcome:
    result: GenerationResult
    model_mode: str
    """"live" / "codex" / "openrouter" / "demo" - oder "offline" wenn der
    deterministische Fallback gegriffen hat."""
    attempts: int
    repaired: bool


@lru_cache(maxsize=1)
def get_provider() -> LLMProvider | None:
    """Provider einmalig aufbauen. `None`, wenn keiner konfigurierbar ist -
    dann laeuft die App im Offline-Modus statt beim Start zu sterben."""

    try:
        return LLMProvider(Settings())
    except Exception:  # noqa: BLE001 - fehlender Key/CLI ist kein Startfehler
        return None


def generate(
    *,
    brief: str,
    platform: str,
    audience: str,
    brand_voice: str,
    history: Sequence[dict],
    techniques: Sequence[str],
    provider: LLMProvider | None,
) -> GenerationOutcome:
    """`provider=None` erzwingt den Offline-Pfad - so laeuft die App (und die
    Testsuite) ohne konfigurierten LLM-Provider, ohne dass hier stillschweigend
    ein echter Provider nachgeladen wird."""

    instructions = build_instructions(techniques)
    prompt = build_prompt(
        brief=brief,
        platform=platform,
        audience=audience,
        brand_voice=brand_voice,
        history=history,
    )

    if provider is None or provider.is_demo:
        return GenerationOutcome(
            result=offline_result(
                brief=brief, platform=platform, audience=audience, brand_voice=brand_voice
            ),
            model_mode="offline",
            attempts=0,
            repaired=False,
        )

    attempts = 0
    last_error: str | None = None
    current_prompt = prompt

    for attempt in range(2):
        attempts = attempt + 1
        try:
            raw = provider.generate_text(instructions, current_prompt)
            payload = LLMProvider.extract_json(raw)
            return GenerationOutcome(
                result=GenerationResult.model_validate(payload),
                model_mode=provider.mode,
                attempts=attempts,
                repaired=attempt > 0,
            )
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            current_prompt = (
                f"{prompt}\n\n"
                "### Korrektur\n"
                "Deine vorherige Antwort war nicht schema-konform. Fehler:\n"
                f"{last_error}\n"
                "Gib jetzt erneut NUR das vollstaendige, gueltige JSON-Objekt aus."
            )
        except Exception as exc:  # noqa: BLE001 - Netzwerk/Provider-Ausfall
            last_error = str(exc)
            break

    # Letzte Rettung: deterministisch, damit der Endpoint nie leer antwortet.
    return GenerationOutcome(
        result=offline_result(
            brief=brief,
            platform=platform,
            audience=audience,
            brand_voice=brand_voice,
            note=f"LLM nicht verwertbar ({last_error})",
        ),
        model_mode="offline",
        attempts=attempts,
        repaired=False,
    )


def summarize_for_history(result: GenerationResult) -> str:
    """Verdichtet einen Assistant-Turn fuer die Prompt-Historie."""

    variant_a = result.variant("A")
    variant_b = result.variant("B")
    return (
        f"Variante A '{variant_a.angle}' vs. Variante B '{variant_b.angle}'; "
        f"Empfehlung: {result.analysis.winner}. "
        f"Naechster Schritt: {result.analysis.recommended_edit}"
    )


# --- Offline-Fallback ----------------------------------------------------


def offline_result(
    *,
    brief: str,
    platform: str,
    audience: str,
    brand_voice: str,
    note: str | None = None,
) -> GenerationResult:
    """Deterministischer, schema-konformer Platzhalter ohne LLM-Aufruf.

    Bewusst erkennbar als Platzhalter (Praefix "[offline]"), damit er in Tests
    und Demos nicht mit echter Modellausgabe verwechselt wird.
    """

    topic = brief.strip().rstrip(".") or "das Briefing"
    short = topic if len(topic) <= 90 else topic[:87] + "..."
    suffix = f" Hinweis: {note}" if note else ""

    return GenerationResult.model_validate(
        {
            "brief_summary": (
                f"[offline] Briefing fuer {platform}, Zielgruppe {audience}, "
                f"Markenstimme {brand_voice}: {short}.{suffix}"
            ),
            "variants": [
                {
                    "variant": "A",
                    "angle": "Problem zuerst",
                    "headline": f"[offline] Das Problem hinter {short}",
                    "body": (
                        f"Variante A greift {short} ueber den Schmerzpunkt der Zielgruppe "
                        f"{audience} auf und bleibt in der Markenstimme: {brand_voice}."
                    ),
                    "call_to_action": "Mehr erfahren.",
                    "hashtags": ["#offline", "#draft"],
                },
                {
                    "variant": "B",
                    "angle": "Ergebnis zuerst",
                    "headline": f"[offline] Das Ergebnis von {short}",
                    "body": (
                        f"Variante B startet beim messbaren Ergebnis von {short} und "
                        f"adressiert {audience} direkt, Ton: {brand_voice}."
                    ),
                    "call_to_action": "Fallstudie ansehen.",
                    "hashtags": ["#offline", "#draft"],
                },
            ],
            "analysis": {
                "reasoning_steps": [
                    "Offline-Modus: kein LLM verfuegbar, Bewertung ist ein Platzhalter.",
                    "Beide Varianten decken die zwei Standardwinkel Problem und Ergebnis ab.",
                ],
                "criteria": [
                    {
                        "criterion": name,
                        "score_a": 3,
                        "score_b": 3,
                        "rationale": "Offline-Platzhalter, keine inhaltliche Bewertung.",
                    }
                    for name in CRITERIA
                ],
                "winner": "A",
                "winner_rationale": (
                    "Offline-Platzhalter: Gleichstand, A wird als Default ausgewiesen."
                ),
                "recommended_edit": (
                    "Setze einen LLM-Provider (AI_PROVIDER + Key), um echte Varianten "
                    "zu erzeugen."
                ),
            },
        }
    )
