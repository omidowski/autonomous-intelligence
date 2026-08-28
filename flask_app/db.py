"""SQLite-Persistenz der Flask-App: genau zwei Tabellen.

- `conversations` - eine Beratungs-Session (Plattform, Zielgruppe,
  Markenstimme). Haelt den geteilten Kontext, gegen den jeder Turn laeuft.
- `messages`      - jeder Turn dieser Session, Nutzer wie Assistant, in
  Reihenfolge (`seq`). Assistant-Turns tragen zusaetzlich das vollstaendige,
  validierte Structured-Output-JSON in `payload_json`.

Damit ist die Konversationshistorie kein Prozessspeicher, sondern Zustand in
der DB: ein Neustart des Servers verliert nichts, und mehrere Worker sehen
dieselbe Historie. Stdlib `sqlite3`, kein ORM - konsistent mit
`src/autonomous_intelligence/db.py`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = "data/flask_app.db"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                platform TEXT NOT NULL,
                audience TEXT NOT NULL,
                brand_voice TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                payload_json TEXT,
                techniques TEXT,
                model_mode TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (conversation_id, seq)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages (conversation_id, seq);
            """
        )
        conn.commit()
    finally:
        conn.close()


# --- conversations: schreiben & lesen ------------------------------------


def create_conversation(
    conn: sqlite3.Connection,
    *,
    title: str,
    platform: str,
    audience: str,
    brand_voice: str,
) -> dict[str, Any]:
    now = utc_now()
    cursor = conn.execute(
        """
        INSERT INTO conversations (title, platform, audience, brand_voice, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (title, platform, audience, brand_voice, now, now),
    )
    conn.commit()
    conversation = get_conversation(conn, int(cursor.lastrowid))
    assert conversation is not None
    return conversation


def get_conversation(conn: sqlite3.Connection, conversation_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    return dict(row) if row else None


def list_conversations(
    conn: sqlite3.Connection, *, limit: int = 50, platform: str | None = None
) -> list[dict[str, Any]]:
    """Listet Sessions inkl. abgeleiteter Kennzahlen (Turn-Zahl, letzte Aktivitaet)."""

    sql = """
        SELECT c.*,
               COUNT(m.id) AS message_count,
               SUM(CASE WHEN m.role = 'assistant' THEN 1 ELSE 0 END) AS generation_count
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
    """
    params: list[Any] = []
    if platform:
        sql += " WHERE c.platform = ?"
        params.append(platform)
    sql += " GROUP BY c.id ORDER BY c.updated_at DESC, c.id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def touch_conversation(conn: sqlite3.Connection, conversation_id: int) -> None:
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (utc_now(), conversation_id),
    )
    conn.commit()


# --- messages: schreiben & lesen -----------------------------------------


def next_seq(conn: sqlite3.Connection, conversation_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM messages WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return int(row["max_seq"]) + 1


def add_message(
    conn: sqlite3.Connection,
    *,
    conversation_id: int,
    role: str,
    content: str,
    payload: dict[str, Any] | None = None,
    techniques: list[str] | None = None,
    model_mode: str | None = None,
) -> dict[str, Any]:
    seq = next_seq(conn, conversation_id)
    cursor = conn.execute(
        """
        INSERT INTO messages (
            conversation_id, seq, role, content, payload_json, techniques, model_mode, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            seq,
            role,
            content,
            json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            ",".join(techniques) if techniques else None,
            model_mode,
            utc_now(),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def list_messages(conn: sqlite3.Connection, conversation_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq ASC",
        (conversation_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def serialize_message(row: dict[str, Any]) -> dict[str, Any]:
    """DB-Zeile -> API-Repraesentation (payload_json wird aufgeloest)."""

    payload = json.loads(row["payload_json"]) if row.get("payload_json") else None
    return {
        "id": row["id"],
        "seq": row["seq"],
        "role": row["role"],
        "content": row["content"],
        "techniques": row["techniques"].split(",") if row.get("techniques") else [],
        "model_mode": row.get("model_mode"),
        "created_at": row["created_at"],
        "result": payload,
    }
