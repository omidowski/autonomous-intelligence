"""Flask-API.

Endpoints (Rubrik-Zuordnung):

  POST /api/conversations                    -> POST 1: Session anlegen
  POST /api/conversations/<id>/generate      -> POST 2 + Text-Generation:
                                                erzeugt Varianten + Analyse und
                                                schreibt beide Turns in die DB
  GET  /api/conversations                    -> GET 1: Sessions auflisten
  GET  /api/conversations/<id>               -> GET 2: Session mit voller
                                                Konversationshistorie lesen
  GET  /api/health                           -> Hilfsendpoint (Status/Modus)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, get_args

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException, NotFound

from autonomous_intelligence.llm import LLMProvider

from . import db as store
from .analysis import compare
from .generation import generate, get_provider, summarize_for_history
from .models import CRITERIA, Platform
from .prompts import DEFAULT_TECHNIQUES, SUPPORTED_TECHNIQUES, normalize_techniques

SUPPORTED_PLATFORMS: tuple[str, ...] = get_args(Platform)
MAX_BRIEF_LENGTH = 4000


class BadRequest(ValueError):
    """400 mit sprechender Meldung."""


def create_app(
    *,
    db_path: str | None = None,
    testing: bool = False,
    provider_factory: Callable[[], LLMProvider | None] | None = None,
) -> Flask:
    """Application factory.

    `provider_factory` liefert den LLM-Provider pro Request. Default ist
    `generation.get_provider` (gecacht); unter `testing=True` ist der Default
    bewusst "kein Provider", damit die Testsuite nie eine echte API oder CLI
    anfaesst und deterministisch gegen den Offline-Generator laeuft.
    """

    app = Flask(__name__)
    app.config["DATABASE_PATH"] = db_path or store.DEFAULT_DB_PATH
    app.config["TESTING"] = testing
    app.config["JSON_SORT_KEYS"] = False

    if provider_factory is None:
        provider_factory = (lambda: None) if testing else get_provider

    store.init_db(app.config["DATABASE_PATH"])

    # --- Connection-Lifecycle --------------------------------------------

    def connection() -> sqlite3.Connection:
        if "db" not in g:
            g.db = store.get_connection(app.config["DATABASE_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_connection(_exc: BaseException | None) -> None:
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    # --- Fehlerbehandlung -------------------------------------------------

    @app.errorhandler(BadRequest)
    def _handle_bad_request(exc: BadRequest):
        return jsonify({"error": "bad_request", "detail": str(exc)}), 400

    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException):
        return jsonify({"error": exc.name.lower().replace(" ", "_"), "detail": exc.description}), (
            exc.code or 500
        )

    # --- Helpers ----------------------------------------------------------

    def json_body() -> dict[str, Any]:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise BadRequest("Request-Body muss ein JSON-Objekt sein.")
        return body

    def required_str(body: dict[str, Any], field: str, *, max_length: int = 500) -> str:
        value = body.get(field)
        if not isinstance(value, str) or not value.strip():
            raise BadRequest(f"Feld '{field}' fehlt oder ist leer.")
        value = value.strip()
        if len(value) > max_length:
            raise BadRequest(f"Feld '{field}' ist laenger als {max_length} Zeichen.")
        return value

    def required_platform(body: dict[str, Any]) -> str:
        platform = required_str(body, "platform", max_length=40).lower()
        if platform not in SUPPORTED_PLATFORMS:
            raise BadRequest(
                f"platform '{platform}' wird nicht unterstuetzt. "
                f"Erlaubt: {list(SUPPORTED_PLATFORMS)}"
            )
        return platform

    def load_conversation(conversation_id: int) -> dict[str, Any]:
        conversation = store.get_conversation(connection(), conversation_id)
        if conversation is None:
            raise NotFound(f"Konversation {conversation_id} existiert nicht.")
        return conversation

    # --- GET (Hilfsendpoint) ---------------------------------------------

    @app.get("/api/health")
    def health():
        provider = provider_factory()
        return jsonify(
            {
                "status": "ok",
                "model_mode": "offline" if provider is None or provider.is_demo else provider.mode,
                "database": app.config["DATABASE_PATH"],
                "platforms": list(SUPPORTED_PLATFORMS),
                "criteria": list(CRITERIA),
                "prompt_techniques": list(SUPPORTED_TECHNIQUES),
            }
        )

    # --- POST 1: Konversation anlegen ------------------------------------

    @app.post("/api/conversations")
    def create_conversation():
        """Legt eine Beratungs-Session an (Zeile in `conversations`)."""

        body = json_body()
        conversation = store.create_conversation(
            connection(),
            title=required_str(body, "title", max_length=200),
            platform=required_platform(body),
            audience=required_str(body, "audience"),
            brand_voice=required_str(body, "brand_voice"),
        )
        return jsonify({"conversation": conversation}), 201

    # --- POST 2 / Text-Generation ----------------------------------------

    @app.post("/api/conversations/<int:conversation_id>/generate")
    def generate_content(conversation_id: int):
        """Text-Generation-Endpoint.

        Nimmt ein Briefing, laedt die bisherige Historie aus SQLite, ruft das
        LLM mit den gewaehlten Prompt-Techniken auf, validiert die Antwort
        gegen das Schema, rechnet die vergleichende Analyse aus und schreibt
        Nutzer- wie Assistant-Turn in die `messages`-Tabelle.
        """

        conversation = load_conversation(conversation_id)
        body = json_body()
        brief = required_str(body, "brief", max_length=MAX_BRIEF_LENGTH)
        try:
            techniques = normalize_techniques(body.get("techniques"))
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        if not techniques:
            techniques = list(DEFAULT_TECHNIQUES)

        conn = connection()
        history = store.list_messages(conn, conversation_id)

        # Nutzer-Turn zuerst persistieren: der Verlauf bleibt auch dann
        # vollstaendig, wenn die Generierung anschliessend scheitert.
        user_message = store.add_message(
            conn,
            conversation_id=conversation_id,
            role="user",
            content=brief,
            techniques=techniques,
        )

        outcome = generate(
            brief=brief,
            platform=conversation["platform"],
            audience=conversation["audience"],
            brand_voice=conversation["brand_voice"],
            history=history,
            techniques=techniques,
            provider=provider_factory(),
        )
        comparison = compare(outcome.result, platform=conversation["platform"])
        payload = {
            "generation": outcome.result.model_dump(),
            "comparative_analysis": comparison,
        }

        assistant_message = store.add_message(
            conn,
            conversation_id=conversation_id,
            role="assistant",
            content=summarize_for_history(outcome.result),
            payload=payload,
            techniques=techniques,
            model_mode=outcome.model_mode,
        )
        store.touch_conversation(conn, conversation_id)

        return (
            jsonify(
                {
                    "conversation_id": conversation_id,
                    "turn": assistant_message["seq"],
                    "history_turns_used": len(history),
                    "prompt_techniques": techniques,
                    "model_mode": outcome.model_mode,
                    "attempts": outcome.attempts,
                    "repaired": outcome.repaired,
                    "generation": payload["generation"],
                    "comparative_analysis": comparison,
                    "messages": {
                        "user": store.serialize_message(user_message),
                        "assistant": store.serialize_message(assistant_message),
                    },
                }
            ),
            201,
        )

    # --- GET 1: Konversationen auflisten ---------------------------------

    @app.get("/api/conversations")
    def list_conversations():
        platform = request.args.get("platform")
        if platform is not None:
            platform = platform.strip().lower()
            if platform not in SUPPORTED_PLATFORMS:
                raise BadRequest(
                    f"platform '{platform}' wird nicht unterstuetzt. "
                    f"Erlaubt: {list(SUPPORTED_PLATFORMS)}"
                )
        try:
            limit = int(request.args.get("limit", 50))
        except ValueError as exc:
            raise BadRequest("limit muss eine Zahl sein.") from exc
        if not 1 <= limit <= 200:
            raise BadRequest("limit muss zwischen 1 und 200 liegen.")

        conversations = store.list_conversations(connection(), limit=limit, platform=platform)
        return jsonify({"count": len(conversations), "conversations": conversations})

    # --- GET 2: Konversation mit voller Historie -------------------------

    @app.get("/api/conversations/<int:conversation_id>")
    def get_conversation(conversation_id: int):
        conversation = load_conversation(conversation_id)
        messages = [
            store.serialize_message(row)
            for row in store.list_messages(connection(), conversation_id)
        ]
        latest = next(
            (message for message in reversed(messages) if message["role"] == "assistant"), None
        )
        return jsonify(
            {
                "conversation": conversation,
                "message_count": len(messages),
                "messages": messages,
                "latest_comparative_analysis": (
                    latest["result"]["comparative_analysis"] if latest and latest["result"] else None
                ),
            }
        )

    return app
