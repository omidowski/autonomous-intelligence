"""Schlanke Flask-API fuer die Rubrik-Anforderungen.

Bewusst eigenstaendig gehalten: die bestehende FastAPI-Plattform unter
`src/autonomous_intelligence/` bleibt unveraendert, hier wird nur die
LLM-Schicht (`autonomous_intelligence.llm.LLMProvider`) wiederverwendet, damit
dieselben Provider (codex / openai / openrouter / demo) greifen.

Use case: Content-Marketing-Briefing -> zwei Copy-Varianten (A/B) plus eine
use-case-spezifische vergleichende Analyse, ueber mehrere Turns hinweg mit
Konversationshistorie.
"""

from .app import create_app

__all__ = ["create_app"]
