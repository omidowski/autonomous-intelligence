"""Prompt-Engineering.

Zwei Techniken sind hier getrennt implementiert und pro Request zuschaltbar
(`techniques` im Request-Body), damit ihr Effekt nachvollziehbar bleibt:

1. FEW_SHOT      - `few_shot_block()`: ein vollstaendig durchgerechnetes
                   Beispiel (Briefing -> erwartetes JSON) im Prompt. Fixiert
                   Format, Tonlage und Detailtiefe, bevor das Modell die
                   echte Aufgabe sieht.
2. CHAIN_OF_THOUGHT - `chain_of_thought_block()`: erzwingt eine explizite,
                   schrittweise Abwaegung *vor* dem Verdikt und verankert sie
                   im Ausgabeschema (`analysis.reasoning_steps`), statt sie
                   nur unsichtbar "im Kopf" passieren zu lassen.

Beide sind additiv: Default ist `["few_shot", "chain_of_thought"]`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from .models import CRITERIA, result_json_schema

FEW_SHOT = "few_shot"
CHAIN_OF_THOUGHT = "chain_of_thought"
SUPPORTED_TECHNIQUES: tuple[str, ...] = (FEW_SHOT, CHAIN_OF_THOUGHT)
DEFAULT_TECHNIQUES: tuple[str, ...] = (FEW_SHOT, CHAIN_OF_THOUGHT)

HISTORY_TURN_LIMIT = 6
"""Wie viele vorangegangene Turns in den Prompt gerendert werden. Begrenzt,
damit lange Konversationen das Kontextfenster nicht sprengen - die volle
Historie bleibt in SQLite und ueber GET /api/conversations/<id> abrufbar."""

BASE_INSTRUCTIONS = """\
Du bist ein Senior Content Strategist fuer B2B- und Consumer-Social-Media.
Zu jedem Briefing lieferst du GENAU ZWEI Copy-Varianten mit deutlich
unterschiedlichem Winkel (nicht zwei Umformulierungen derselben Idee) und
danach eine vergleichende Analyse dieser beiden Varianten.

Harte Regeln:
- Antworte AUSSCHLIESSLICH mit einem einzigen JSON-Objekt. Kein Fliesstext,
  keine Markdown-Codefences, keine Kommentare.
- Halte dich exakt an das vorgegebene JSON-Schema.
- Bewerte auf jedem der Kriterien beide Varianten mit 1-5 (5 = am besten).
- `winner` muss zu den vergebenen Punktwerten passen.
- Schreibe in der Sprache des Briefings.
"""

_FEW_SHOT_EXAMPLE_BRIEF = """\
Plattform: linkedin | Zielgruppe: Head of Ops in mittelstaendischer Logistik
Markenstimme: sachlich, konkret, ohne Buzzwords
Briefing: Wir starten ein Tool, das Lieferverzoegerungen 48h im Voraus meldet.\
"""

_FEW_SHOT_EXAMPLE_OUTPUT = {
    "brief_summary": (
        "Launch-Post fuer ein Fruehwarn-Tool bei Lieferverzoegerungen, gerichtet an "
        "Operations-Verantwortliche im Mittelstand, sachlicher Ton ohne Buzzwords."
    ),
    "variants": [
        {
            "variant": "A",
            "angle": "Kosten der Ueberraschung",
            "headline": "Eine Verspaetung kostet nicht 2 Tage. Sie kostet 2 Tage zu spaet.",
            "body": (
                "Die meisten Ops-Teams erfahren von einer Verzoegerung, wenn der Kunde "
                "anruft. Unser neues Fruehwarnsystem meldet sie 48 Stunden vorher - genug "
                "Zeit, um umzuplanen statt zu entschuldigen."
            ),
            "call_to_action": "Kurzdemo ansehen (8 Minuten).",
            "hashtags": ["#Logistik", "#SupplyChain", "#Operations"],
        },
        {
            "variant": "B",
            "angle": "Konkreter Kundenfall",
            "headline": "48 Stunden Vorwarnung, 31 Prozent weniger Eskalationen.",
            "body": (
                "Ein Kunde mit 40 taeglichen Touren hat drei Monate mit uns getestet. "
                "Ergebnis: weniger Eskalationen, gleiche Flotte, keine zusaetzliche "
                "Schicht in der Disposition."
            ),
            "call_to_action": "Fallstudie lesen.",
            "hashtags": ["#SupplyChain", "#CaseStudy"],
        },
    ],
    "analysis": {
        "reasoning_steps": [
            "Zielgruppe sind Ops-Leads unter Zeitdruck: Zahlen schlagen Wortspiele.",
            "Variante A hat den staerkeren Einstieg, bleibt aber eine Behauptung.",
            "Variante B belegt den Nutzen, startet dafuer nuechterner.",
            "Auf LinkedIn ueberwiegt Belegbarkeit knapp gegenueber Pointierung.",
        ],
        "criteria": [
            {
                "criterion": "hook_strength",
                "score_a": 5,
                "score_b": 4,
                "rationale": "A hat die pointiertere erste Zeile, B den harten Zahlenanker.",
            },
            {
                "criterion": "brand_voice_fit",
                "score_a": 3,
                "score_b": 5,
                "rationale": "Sachlich ohne Buzzwords - B trifft das, A ist werblicher.",
            },
            {
                "criterion": "platform_fit",
                "score_a": 4,
                "score_b": 5,
                "rationale": "Belegte Ergebnisposts performen im LinkedIn-Feed stabiler.",
            },
            {
                "criterion": "cta_clarity",
                "score_a": 5,
                "score_b": 4,
                "rationale": "A nennt die Dauer und senkt damit die Huerde spuerbar.",
            },
            {
                "criterion": "audience_relevance",
                "score_a": 4,
                "score_b": 5,
                "rationale": "Der Flottenbezug in B spiegelt den Alltag der Zielgruppe.",
            },
        ],
        "winner": "B",
        "winner_rationale": (
            "B gewinnt 23:21, weil Markenstimme und Plattformpassung schwerer wiegen als "
            "der etwas staerkere Hook von A."
        ),
        "recommended_edit": (
            "Uebernimm den zeitlich konkreten CTA aus A in B: 'Fallstudie lesen (4 Minuten).'"
        ),
    },
}


def few_shot_block() -> str:
    """Technik 1: Few-Shot Prompting - ein vollstaendiges Loesungsbeispiel."""

    example = json.dumps(_FEW_SHOT_EXAMPLE_OUTPUT, ensure_ascii=False, indent=2)
    return (
        "### Beispiel (Few-Shot)\n"
        "So sieht eine gute Loesung aus. Uebernimm Format und Detailtiefe, "
        "nicht den Inhalt.\n\n"
        f"BRIEFING:\n{_FEW_SHOT_EXAMPLE_BRIEF}\n\n"
        f"ANTWORT:\n{example}\n"
    )


def chain_of_thought_block() -> str:
    """Technik 2: Chain-of-Thought - Abwaegung vor Verdikt, im Schema verankert."""

    criteria = ", ".join(CRITERIA)
    return (
        "### Vorgehen (Schritt fuer Schritt)\n"
        "Denke die Analyse in dieser Reihenfolge durch und schreibe die Zwischenschritte "
        "als kurze Saetze nach `analysis.reasoning_steps`:\n"
        "1. Fasse zusammen, worauf es bei diesem Briefing und dieser Zielgruppe ankommt.\n"
        f"2. Pruefe beide Varianten nacheinander gegen jedes Kriterium ({criteria}).\n"
        "3. Benenne den entscheidenden Unterschied zwischen A und B.\n"
        "4. Erst danach: vergib die Punkte, bestimme `winner` und begruende ihn.\n"
        "Die Punktwerte muessen aus den Zwischenschritten folgen - lege dich nicht "
        "vorher fest.\n"
    )


def normalize_techniques(raw: object) -> list[str]:
    """Validiert die pro Request gewaehlten Techniken."""

    if raw is None:
        return list(DEFAULT_TECHNIQUES)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError("techniques muss eine Liste von Strings sein")
    chosen = [item.strip().lower() for item in raw if item.strip()]
    unknown = [item for item in chosen if item not in SUPPORTED_TECHNIQUES]
    if unknown:
        raise ValueError(
            f"Unbekannte Technik(en): {unknown}. Erlaubt: {list(SUPPORTED_TECHNIQUES)}"
        )
    # Reihenfolge stabil halten, Duplikate entfernen.
    return [item for item in SUPPORTED_TECHNIQUES if item in chosen]


def render_history(turns: Sequence[dict]) -> str:
    """Rendert die Konversationshistorie fuer den Prompt.

    `turns` sind Zeilen aus der `messages`-Tabelle (aelteste zuerst). Nur die
    letzten `HISTORY_TURN_LIMIT` Eintraege gehen in den Prompt; Assistant-Turns
    werden auf Kernaussagen verdichtet, damit die Historie nicht bei jedem
    Folgeturn das komplette JSON mitschleppt.
    """

    recent = list(turns)[-HISTORY_TURN_LIMIT:]
    if not recent:
        return ""
    lines = ["### Bisheriger Gespraechsverlauf (aelteste zuerst)"]
    for turn in recent:
        role = turn["role"]
        if role == "user":
            lines.append(f"- NUTZER: {turn['content']}")
        else:
            lines.append(f"- DU (Zusammenfassung): {turn['content']}")
    lines.append(
        "Beziehe dich auf diesen Verlauf: greife bereits akzeptierte Entscheidungen auf "
        "und wiederhole verworfene Winkel nicht."
    )
    return "\n".join(lines) + "\n"


def build_instructions(techniques: Iterable[str]) -> str:
    """System-/Instruktionsteil, abhaengig von den gewaehlten Techniken."""

    chosen = list(techniques)
    parts = [BASE_INSTRUCTIONS]
    if CHAIN_OF_THOUGHT in chosen:
        parts.append(chain_of_thought_block())
    if FEW_SHOT in chosen:
        parts.append(few_shot_block())
    return "\n".join(parts)


def build_prompt(
    *,
    brief: str,
    platform: str,
    audience: str,
    brand_voice: str,
    history: Sequence[dict],
) -> str:
    """Der eigentliche Task-Prompt inklusive Historie und Ausgabeschema."""

    schema = json.dumps(result_json_schema(), ensure_ascii=False, indent=2)
    history_block = render_history(history)
    return (
        f"{history_block}"
        "### Aktuelles Briefing\n"
        f"Plattform: {platform}\n"
        f"Zielgruppe: {audience}\n"
        f"Markenstimme: {brand_voice}\n"
        f"Briefing: {brief}\n\n"
        "### Ausgabeschema (JSON Schema, verbindlich)\n"
        f"{schema}\n\n"
        "Gib jetzt nur das JSON-Objekt aus."
    )
