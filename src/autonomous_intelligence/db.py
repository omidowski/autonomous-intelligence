"""SQLite persistence for auth, multi-tenancy, scheduling, review status and
publish logs. Stdlib `sqlite3`, no ORM - the app's existing style favours
hand-rolled persistence (see `daily_content_orchestrator.py`'s manifest.json
writes) over pulling in SQLAlchemy/Alembic for a handful of small tables.

Schema changes for this pass are idempotent `CREATE TABLE IF NOT EXISTS`
statements run at startup - no versioned migrations (see the plan's "out of
scope" section for the upgrade path once this needs to be a real product).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


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
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                daily_run_enabled INTEGER NOT NULL DEFAULT 0,
                daily_run_hour_utc INTEGER,
                daily_run_minute_utc INTEGER,
                last_scheduled_run_date TEXT,
                brand_voice TEXT,
                content_model TEXT,
                content_language TEXT,
                target_duration_seconds INTEGER,
                image_size TEXT,
                image_quality TEXT,
                subscription_plan TEXT NOT NULL DEFAULT 'free',
                subscription_status TEXT NOT NULL DEFAULT 'active',
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                company_id INTEGER NOT NULL REFERENCES companies(id),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS research_reports (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                query TEXT NOT NULL,
                report_json TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS content_bundle_status (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                date TEXT NOT NULL,
                trend_index INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                reviewer_email TEXT,
                reviewed_at TEXT,
                notes TEXT,
                UNIQUE(company_id, date, trend_index)
            );

            CREATE TABLE IF NOT EXISTS publish_log (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                date TEXT NOT NULL,
                trend_index INTEGER NOT NULL,
                platform TEXT NOT NULL,
                success INTEGER NOT NULL,
                external_id TEXT,
                detail TEXT,
                published_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                kind TEXT NOT NULL,
                units INTEGER NOT NULL DEFAULT 1,
                estimated_cost_usd REAL NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS brand_assets (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                is_logo INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS post_variant_selection (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                date TEXT NOT NULL,
                trend_index INTEGER NOT NULL,
                platform TEXT NOT NULL,
                selected_variant TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(company_id, date, trend_index, platform)
            );

            CREATE TABLE IF NOT EXISTS stripe_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                received_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                name TEXT NOT NULL,
                prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                date TEXT NOT NULL,
                trend_index INTEGER NOT NULL,
                platform TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                external_id TEXT,
                detail TEXT,
                created_at TEXT NOT NULL,
                published_at TEXT,
                UNIQUE(company_id, date, trend_index, platform, scheduled_for)
            );

            CREATE TABLE IF NOT EXISTS company_webhooks (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                url TEXT NOT NULL,
                secret TEXT NOT NULL,
                event_types TEXT NOT NULL DEFAULT 'all',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            """
        )
        # `brand_voice` was added after the initial `companies` table shipped -
        # `CREATE TABLE IF NOT EXISTS` above is a no-op against a pre-existing
        # dev DB, so add the column explicitly for upgrades (idempotent - a
        # duplicate-column error means it's already there).
        for column_ddl in (
            "ALTER TABLE companies ADD COLUMN brand_voice TEXT",
            "ALTER TABLE companies ADD COLUMN content_model TEXT",
            "ALTER TABLE companies ADD COLUMN content_language TEXT",
            "ALTER TABLE companies ADD COLUMN target_duration_seconds INTEGER",
            "ALTER TABLE companies ADD COLUMN image_size TEXT",
            "ALTER TABLE companies ADD COLUMN image_quality TEXT",
            "ALTER TABLE companies ADD COLUMN subscription_plan TEXT NOT NULL DEFAULT 'free'",
            "ALTER TABLE companies ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'active'",
            "ALTER TABLE companies ADD COLUMN stripe_customer_id TEXT",
            "ALTER TABLE companies ADD COLUMN stripe_subscription_id TEXT",
        ):
            try:
                conn.execute(column_ddl)
            except sqlite3.OperationalError:
                pass
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- companies ---------------------------------------------------------


def insert_company(db_path: str | Path, *, slug: str, name: str) -> int:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO companies (slug, name, created_at) VALUES (?, ?, ?)",
            (slug, name, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_company_by_slug(db_path: str | Path, slug: str) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT * FROM companies WHERE slug = ?", (slug,)).fetchone()
    finally:
        conn.close()


def get_company_by_id(db_path: str | Path, company_id: int) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    finally:
        conn.close()


def update_company_schedule(
    db_path: str | Path,
    company_id: int,
    *,
    enabled: bool,
    hour_utc: int | None,
    minute_utc: int | None,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE companies SET daily_run_enabled = ?, daily_run_hour_utc = ?, "
            "daily_run_minute_utc = ? WHERE id = ?",
            (1 if enabled else 0, hour_utc, minute_utc, company_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_company_brand_voice(db_path: str | Path, company_id: int, brand_voice: str | None) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE companies SET brand_voice = ? WHERE id = ?", (brand_voice, company_id))
        conn.commit()
    finally:
        conn.close()


def update_company_content_model(db_path: str | Path, company_id: int, content_model: str | None) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE companies SET content_model = ? WHERE id = ?", (content_model, company_id)
        )
        conn.commit()
    finally:
        conn.close()


def update_company_content_settings(
    db_path: str | Path,
    company_id: int,
    *,
    content_language: str | None,
    target_duration_seconds: int | None,
    image_size: str | None,
    image_quality: str | None,
) -> None:
    """Single consolidated update for the "more content settings" group
    (language/duration/image size/quality) - unlike `brand_voice`/
    `content_model`, which shipped earlier as their own cards/endpoints,
    these four are saved together from one frontend card via one PUT."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE companies SET content_language = ?, target_duration_seconds = ?, "
            "image_size = ?, image_quality = ? WHERE id = ?",
            (content_language, target_duration_seconds, image_size, image_quality, company_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_company_by_stripe_customer_id(db_path: str | Path, stripe_customer_id: str) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM companies WHERE stripe_customer_id = ?", (stripe_customer_id,)
        ).fetchone()
    finally:
        conn.close()


def set_stripe_customer_id(db_path: str | Path, company_id: int, stripe_customer_id: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE companies SET stripe_customer_id = ? WHERE id = ?",
            (stripe_customer_id, company_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_company_subscription(
    db_path: str | Path,
    company_id: int,
    *,
    plan: str,
    status: str,
    stripe_subscription_id: str | None = None,
) -> None:
    """Called from the Stripe webhook handler (`billing_api.py`) - the only
    writer of `subscription_plan`/`subscription_status` post-signup, since
    those reflect what Stripe says is true, not what the app decided.
    `stripe_subscription_id` is left unchanged (pass `None`) on updates
    that don't carry one, e.g. a plan reverting to "free" after
    cancellation keeps the historical subscription id for reference."""
    conn = get_connection(db_path)
    try:
        if stripe_subscription_id is not None:
            conn.execute(
                "UPDATE companies SET subscription_plan = ?, subscription_status = ?, "
                "stripe_subscription_id = ? WHERE id = ?",
                (plan, status, stripe_subscription_id, company_id),
            )
        else:
            conn.execute(
                "UPDATE companies SET subscription_plan = ?, subscription_status = ? WHERE id = ?",
                (plan, status, company_id),
            )
        conn.commit()
    finally:
        conn.close()


def record_stripe_event(db_path: str | Path, event_id: str, event_type: str) -> bool:
    """Records a Stripe webhook `event_id` and returns `True` if it was new.
    `False` means this exact event was already handled - Stripe retries
    delivery (up to 3 days) on any non-2xx or timeout, so the handler in
    `billing_api.py` calls this first and no-ops on a repeat. The row is
    written before the event is processed: every current handler is an
    idempotent UPSERT so a re-delivery after a mid-processing crash is
    harmless to skip; revisit if a non-idempotent handler is added."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO stripe_events (event_id, event_type, received_at) "
            "VALUES (?, ?, ?)",
            (event_id, event_type, _now()),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def schedule_post(
    db_path: str | Path,
    *,
    company_id: int,
    date: str,
    trend_index: int,
    platform: str,
    scheduled_for: str,
) -> int | None:
    """Queues one platform post for a future time. Returns the new row id,
    or `None` if this exact slot is already queued - the UNIQUE constraint
    makes double-booking a no-op rather than an error, so a retried request
    can't create duplicate posts."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO scheduled_posts "
            "(company_id, date, trend_index, platform, scheduled_for, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (company_id, date, trend_index, platform, scheduled_for, _now()),
        )
        conn.commit()
        return int(cur.lastrowid) if cur.rowcount == 1 else None
    finally:
        conn.close()


def list_scheduled_posts(
    db_path: str | Path, company_id: int, *, status: str | None = None
) -> list[sqlite3.Row]:
    conn = get_connection(db_path)
    try:
        sql = "SELECT * FROM scheduled_posts WHERE company_id = ?"
        params: list[object] = [company_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        return conn.execute(sql + " ORDER BY scheduled_for", params).fetchall()
    finally:
        conn.close()


def list_due_scheduled_posts(db_path: str | Path, *, now_iso: str) -> list[sqlite3.Row]:
    """Every pending post across all companies whose time has come - the
    scheduler's work queue."""
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM scheduled_posts WHERE status = 'pending' AND scheduled_for <= ? "
            "ORDER BY scheduled_for",
            (now_iso,),
        ).fetchall()
    finally:
        conn.close()


def mark_scheduled_post(
    db_path: str | Path,
    post_id: int,
    *,
    status: str,
    external_id: str | None = None,
    detail: str | None = None,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE scheduled_posts SET status = ?, external_id = ?, detail = ?, "
            "published_at = ? WHERE id = ?",
            (status, external_id, detail, _now(), post_id),
        )
        conn.commit()
    finally:
        conn.close()


def cancel_scheduled_post(db_path: str | Path, company_id: int, post_id: int) -> bool:
    """Only a still-pending post can be canceled - one already published
    cannot be un-published from here."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "UPDATE scheduled_posts SET status = 'canceled' "
            "WHERE id = ? AND company_id = ? AND status = 'pending'",
            (post_id, company_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def create_api_key(
    db_path: str | Path, *, company_id: int, name: str, prefix: str, key_hash: str
) -> int:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO api_keys (company_id, name, prefix, key_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (company_id, name, prefix, key_hash, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_api_keys(db_path: str | Path, company_id: int) -> list[sqlite3.Row]:
    """Every key for the company, revoked ones included - the UI shows them
    struck through rather than hiding that they ever existed."""
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM api_keys WHERE company_id = ? ORDER BY id DESC", (company_id,)
        ).fetchall()
    finally:
        conn.close()


def get_api_key_by_hash(db_path: str | Path, key_hash: str) -> sqlite3.Row | None:
    """Looks a presented key up by its hash. Revoked keys are not returned,
    so revocation takes effect on the very next request."""
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0", (key_hash,)
        ).fetchone()
    finally:
        conn.close()


def touch_api_key(db_path: str | Path, key_id: int) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), key_id))
        conn.commit()
    finally:
        conn.close()


def revoke_api_key(db_path: str | Path, company_id: int, key_id: int) -> bool:
    """Soft delete - the row stays so `last_used_at` remains auditable after
    a key is turned off."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "UPDATE api_keys SET revoked = 1 WHERE id = ? AND company_id = ? AND revoked = 0",
            (key_id, company_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def create_company_webhook(
    db_path: str | Path,
    *,
    company_id: int,
    url: str,
    secret: str,
    event_types: str = "all",
) -> int:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO company_webhooks (company_id, url, secret, event_types, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (company_id, url, secret, event_types, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_company_webhooks(
    db_path: str | Path, company_id: int, *, active_only: bool = False
) -> list[sqlite3.Row]:
    conn = get_connection(db_path)
    try:
        sql = "SELECT * FROM company_webhooks WHERE company_id = ?"
        if active_only:
            sql += " AND active = 1"
        return conn.execute(sql + " ORDER BY id", (company_id,)).fetchall()
    finally:
        conn.close()


def delete_company_webhook(db_path: str | Path, company_id: int, webhook_id: int) -> bool:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM company_webhooks WHERE id = ? AND company_id = ?",
            (webhook_id, company_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_companies_with_schedule_enabled(db_path: str | Path) -> list[sqlite3.Row]:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM companies WHERE daily_run_enabled = 1 "
            "AND daily_run_hour_utc IS NOT NULL AND daily_run_minute_utc IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()


def mark_company_run(db_path: str | Path, slug: str, date_str: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE companies SET last_scheduled_run_date = ? WHERE slug = ?", (date_str, slug)
        )
        conn.commit()
    finally:
        conn.close()


# --- users ---------------------------------------------------------------


def insert_user(
    db_path: str | Path,
    *,
    company_id: int,
    email: str,
    password_hash: str,
    password_salt: str,
    role: str = "member",
) -> int:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO users (company_id, email, password_hash, password_salt, role, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (company_id, email, password_hash, password_salt, role, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_user_by_email(db_path: str | Path, email: str) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()


def get_user_by_id(db_path: str | Path, user_id: int) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()


def list_users_for_company(db_path: str | Path, company_id: int) -> list[sqlite3.Row]:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT id, email, role, created_at FROM users WHERE company_id = ? ORDER BY id",
            (company_id,),
        ).fetchall()
    finally:
        conn.close()


# --- sessions --------------------------------------------------------------


def insert_session(
    db_path: str | Path, *, token: str, user_id: int, company_id: int, expires_at: str
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id, company_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, user_id, company_id, _now(), expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_session(db_path: str | Path, token: str) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    finally:
        conn.close()


def delete_session(db_path: str | Path, token: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


# --- research reports --------------------------------------------------


def insert_research_report(
    db_path: str | Path, *, company_id: int, query: str, report_json: str, mode: str
) -> int:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO research_reports (company_id, query, report_json, mode, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (company_id, query, report_json, mode, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_research_reports(db_path: str | Path, company_id: int) -> list[sqlite3.Row]:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT id, query, mode, created_at FROM research_reports WHERE company_id = ? "
            "ORDER BY id DESC",
            (company_id,),
        ).fetchall()
    finally:
        conn.close()


def get_research_report(db_path: str | Path, company_id: int, report_id: int) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM research_reports WHERE id = ? AND company_id = ?",
            (report_id, company_id),
        ).fetchone()
    finally:
        conn.close()


# --- content bundle status --------------------------------------------


def get_bundle_status_row(
    db_path: str | Path, company_id: int, date: str, trend_index: int
) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM content_bundle_status WHERE company_id = ? AND date = ? "
            "AND trend_index = ?",
            (company_id, date, trend_index),
        ).fetchone()
    finally:
        conn.close()


def upsert_bundle_status(
    db_path: str | Path,
    *,
    company_id: int,
    date: str,
    trend_index: int,
    status: str,
    reviewer_email: str | None,
    notes: str | None,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO content_bundle_status
                (company_id, date, trend_index, status, reviewer_email, reviewed_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, date, trend_index) DO UPDATE SET
                status = excluded.status,
                reviewer_email = excluded.reviewer_email,
                reviewed_at = excluded.reviewed_at,
                notes = excluded.notes
            """,
            (company_id, date, trend_index, status, reviewer_email, _now(), notes),
        )
        conn.commit()
    finally:
        conn.close()


# --- post variant selection --------------------------------------------


def get_selected_variants(
    db_path: str | Path, company_id: int, date: str, trend_index: int
) -> dict[str, str]:
    """Maps platform -> selected variant ("A"/"B") for this bundle. A
    platform with no row here defaults to "A" (see `review_api.py`'s
    publish endpoint and `api.py`'s bundle rendering) - most bundles have
    no explicit selection, and "A" is always the first variant generated."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT platform, selected_variant FROM post_variant_selection "
            "WHERE company_id = ? AND date = ? AND trend_index = ?",
            (company_id, date, trend_index),
        ).fetchall()
        return {row["platform"]: row["selected_variant"] for row in rows}
    finally:
        conn.close()


def set_selected_variant(
    db_path: str | Path,
    *,
    company_id: int,
    date: str,
    trend_index: int,
    platform: str,
    variant: str,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO post_variant_selection
                (company_id, date, trend_index, platform, selected_variant, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, date, trend_index, platform) DO UPDATE SET
                selected_variant = excluded.selected_variant,
                updated_at = excluded.updated_at
            """,
            (company_id, date, trend_index, platform, variant, _now()),
        )
        conn.commit()
    finally:
        conn.close()


# --- publish log -----------------------------------------------------------


def insert_publish_log(
    db_path: str | Path,
    *,
    company_id: int,
    date: str,
    trend_index: int,
    platform: str,
    success: bool,
    external_id: str | None,
    detail: str,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO publish_log (company_id, date, trend_index, platform, success, "
            "external_id, detail, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (company_id, date, trend_index, platform, 1 if success else 0, external_id, detail, _now()),
        )
        conn.commit()
    finally:
        conn.close()


# --- usage log ---------------------------------------------------------


def insert_usage_log(
    db_path: str | Path, *, company_id: int, kind: str, units: int, estimated_cost_usd: float
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO usage_log (company_id, kind, units, estimated_cost_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (company_id, kind, units, estimated_cost_usd, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def get_usage_summary(db_path: str | Path, company_id: int, *, since: str) -> list[sqlite3.Row]:
    """Returns one row per `kind` (`{"kind", "calls", "units", "estimated_cost_usd"}`) for
    usage_log entries with `created_at >= since` (an ISO-8601 date/datetime prefix, e.g. a
    "YYYY-MM" month prefix works since `created_at` is ISO-8601)."""
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT kind, COUNT(*) AS calls, SUM(units) AS units, "
            "SUM(estimated_cost_usd) AS estimated_cost_usd FROM usage_log "
            "WHERE company_id = ? AND created_at >= ? GROUP BY kind",
            (company_id, since),
        ).fetchall()
    finally:
        conn.close()


# --- brand assets -----------------------------------------------------


def insert_brand_asset(
    db_path: str | Path,
    *,
    company_id: int,
    filename: str,
    content_type: str,
    is_logo: bool,
) -> int:
    conn = get_connection(db_path)
    try:
        if is_logo:
            # Only one logo at a time - demote any previous logo so the
            # watermark step (`brand_assets.get_logo_asset`) has a single
            # unambiguous choice.
            conn.execute(
                "UPDATE brand_assets SET is_logo = 0 WHERE company_id = ?", (company_id,)
            )
        cur = conn.execute(
            "INSERT INTO brand_assets (company_id, filename, content_type, is_logo, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (company_id, filename, content_type, 1 if is_logo else 0, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_brand_assets(db_path: str | Path, company_id: int) -> list[sqlite3.Row]:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM brand_assets WHERE company_id = ? ORDER BY id DESC", (company_id,)
        ).fetchall()
    finally:
        conn.close()


def get_brand_asset(db_path: str | Path, company_id: int, asset_id: int) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM brand_assets WHERE id = ? AND company_id = ?", (asset_id, company_id)
        ).fetchone()
    finally:
        conn.close()


def get_logo_asset(db_path: str | Path, company_id: int) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM brand_assets WHERE company_id = ? AND is_logo = 1", (company_id,)
        ).fetchone()
    finally:
        conn.close()


def delete_brand_asset(db_path: str | Path, company_id: int, asset_id: int) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "DELETE FROM brand_assets WHERE id = ? AND company_id = ?", (asset_id, company_id)
        )
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "cancel_scheduled_post",
    "create_api_key",
    "create_company_webhook",
    "delete_brand_asset",
    "delete_company_webhook",
    "delete_session",
    "get_api_key_by_hash",
    "get_brand_asset",
    "get_bundle_status_row",
    "get_company_by_id",
    "get_company_by_slug",
    "get_company_by_stripe_customer_id",
    "get_connection",
    "get_logo_asset",
    "get_research_report",
    "get_selected_variants",
    "get_session",
    "get_usage_summary",
    "get_user_by_email",
    "get_user_by_id",
    "init_db",
    "insert_brand_asset",
    "insert_company",
    "insert_publish_log",
    "insert_research_report",
    "insert_session",
    "insert_usage_log",
    "insert_user",
    "list_api_keys",
    "list_brand_assets",
    "list_companies_with_schedule_enabled",
    "list_company_webhooks",
    "list_due_scheduled_posts",
    "list_research_reports",
    "list_scheduled_posts",
    "list_users_for_company",
    "mark_company_run",
    "mark_scheduled_post",
    "record_stripe_event",
    "revoke_api_key",
    "schedule_post",
    "set_selected_variant",
    "set_stripe_customer_id",
    "touch_api_key",
    "update_company_brand_voice",
    "update_company_content_model",
    "update_company_content_settings",
    "update_company_schedule",
    "update_company_subscription",
    "upsert_bundle_status",
]
