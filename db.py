"""
db.py — every SQLite read and write in Jarvis goes through this module.

No other module opens the database directly.

Schema changes are applied by versioned migration functions in MIGRATIONS.
Tables are never dropped and recreated: to change the schema, append a new
migration. The current version is recorded in the schema_meta table.

All timestamps are stored as ISO-8601 UTC strings ("2026-09-19T08:30:00+00:00")
so they sort lexicographically and carry no local-timezone ambiguity.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import config

# Serialises writers within this process. SQLite handles cross-process locking
# itself; this just keeps the background sync thread and Flask request threads
# from tripping over each other on long write batches.
_WRITE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Time helpers (shared by every module — nothing else should build its own)
# ---------------------------------------------------------------------------

def utcnow():
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_iso(dt):
    """Serialise a datetime to an ISO-8601 UTC string. None passes through."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def from_iso(value):
    """Parse an ISO-8601 string back to a timezone-aware UTC datetime."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

def _connect(db_path=None):
    path = db_path or config.DB_PATH
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def connection(db_path=None):
    """Read-oriented connection. Closed automatically."""
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(db_path=None):
    """Write connection wrapped in a transaction, serialised in-process."""
    with _WRITE_LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Migrations
#
# Each entry is (version, description, callable(conn)). Append only — never
# edit a migration that has already shipped.
# ---------------------------------------------------------------------------

# DDL is applied statement by statement rather than with executescript(),
# because executescript() issues an implicit COMMIT that would end the
# migration transaction opened by transaction().
_MIGRATION_001_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # Feature 4: "No reply needed" — permanent dismissal of a thread.
    """
    CREATE TABLE IF NOT EXISTS dismissals (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        category        TEXT NOT NULL,
        entry_id        TEXT,
        subject         TEXT,
        created_at      TEXT NOT NULL,
        UNIQUE (conversation_id, category)
    )
    """,
    # Feature 4: "Snooze 3 days" — hidden until snooze_until passes.
    """
    CREATE TABLE IF NOT EXISTS snoozes (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        category        TEXT NOT NULL,
        entry_id        TEXT,
        subject         TEXT,
        snooze_until    TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        UNIQUE (conversation_id, category)
    )
    """,
    # Feature 6: saved PrivateGPT responses, raw plus parsed fields.
    """
    CREATE TABLE IF NOT EXISTS ai_responses (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id        TEXT NOT NULL,
        conversation_id TEXT,
        prompt_type     TEXT NOT NULL DEFAULT 'followup',
        prompt_version  INTEGER,
        raw_response    TEXT NOT NULL,
        situation       TEXT,
        action          TEXT,
        draft_reply     TEXT,
        urgency         TEXT,
        urgency_reason  TEXT,
        created_at      TEXT NOT NULL
    )
    """,
    # Cached sync results so the dashboard loads instantly.
    # payload is JSON: new data sources add fields without a migration.
    """
    CREATE TABLE IF NOT EXISTS cached_items (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        category        TEXT NOT NULL,
        entry_id        TEXT NOT NULL,
        conversation_id TEXT,
        payload         TEXT NOT NULL,
        synced_at       TEXT NOT NULL,
        UNIQUE (category, entry_id)
    )
    """,
    # One row per sync run, mirroring sync.log.
    """
    CREATE TABLE IF NOT EXISTS sync_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at   TEXT NOT NULL,
        finished_at  TEXT,
        status       TEXT NOT NULL,
        counts       TEXT,
        error        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_dismissals_conv ON dismissals (conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_snoozes_conv ON snoozes (conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_snoozes_until ON snoozes (snooze_until)",
    "CREATE INDEX IF NOT EXISTS idx_ai_entry ON ai_responses (entry_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_cached_category ON cached_items (category)",
    "CREATE INDEX IF NOT EXISTS idx_sync_started ON sync_runs (started_at DESC)",
]


def _migration_001_initial(conn):
    for statement in _MIGRATION_001_STATEMENTS:
        conn.execute(statement)


MIGRATIONS = [
    (1, "initial schema", _migration_001_initial),
]

SCHEMA_VERSION = max(version for version, _, _ in MIGRATIONS)


def _read_version(conn):
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0  # schema_meta does not exist yet -> fresh database
    return int(row["value"]) if row else 0


def init_db(db_path=None):
    """Create or migrate the database. Safe to call on every start."""
    with transaction(db_path) as conn:
        current = _read_version(conn)
        applied = []
        for version, description, migrate in sorted(MIGRATIONS):
            if version > current:
                migrate(conn)
                conn.execute(
                    "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(version),),
                )
                applied.append((version, description))
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('initialised_at', ?) "
            "ON CONFLICT(key) DO NOTHING",
            (to_iso(utcnow()),),
        )
    return applied


def schema_version(db_path=None):
    with connection(db_path) as conn:
        return _read_version(conn)


def describe_schema(db_path=None):
    """Return the CREATE statements for every table and index."""
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL ORDER BY type DESC, name"
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Dismissals — "No reply needed"
# ---------------------------------------------------------------------------

def dismiss(conversation_id, category, entry_id=None, subject=None, db_path=None):
    with transaction(db_path) as conn:
        conn.execute(
            "INSERT INTO dismissals (conversation_id, category, entry_id, subject, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(conversation_id, category) DO UPDATE SET "
            "  entry_id = excluded.entry_id, "
            "  subject = excluded.subject, "
            "  created_at = excluded.created_at",
            (conversation_id, category, entry_id, subject, to_iso(utcnow())),
        )


def undismiss(conversation_id, category, db_path=None):
    with transaction(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM dismissals WHERE conversation_id = ? AND category = ?",
            (conversation_id, category),
        )
        return cur.rowcount


def dismissed_ids(category=None, db_path=None):
    """Set of conversation_ids permanently dismissed for a category."""
    sql = "SELECT conversation_id FROM dismissals"
    params = ()
    if category:
        sql += " WHERE category = ?"
        params = (category,)
    with connection(db_path) as conn:
        return {row["conversation_id"] for row in conn.execute(sql, params)}


# ---------------------------------------------------------------------------
# Snoozes
# ---------------------------------------------------------------------------

def snooze(conversation_id, category, days=None, entry_id=None, subject=None,
           seconds=None, db_path=None):
    """Hide a thread until the snooze expires.

    `days` defaults to config.SNOOZE_DAYS. `seconds` overrides it and exists so
    expiry behaviour can be exercised without waiting three days.
    """
    now = utcnow()
    if seconds is not None:
        until = now + timedelta(seconds=seconds)
    else:
        until = now + timedelta(days=config.SNOOZE_DAYS if days is None else days)
    with transaction(db_path) as conn:
        conn.execute(
            "INSERT INTO snoozes (conversation_id, category, entry_id, subject, "
            "                     snooze_until, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(conversation_id, category) DO UPDATE SET "
            "  entry_id = excluded.entry_id, "
            "  subject = excluded.subject, "
            "  snooze_until = excluded.snooze_until, "
            "  created_at = excluded.created_at",
            (conversation_id, category, entry_id, subject, to_iso(until), to_iso(now)),
        )
    return until


def unsnooze(conversation_id, category, db_path=None):
    with transaction(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM snoozes WHERE conversation_id = ? AND category = ?",
            (conversation_id, category),
        )
        return cur.rowcount


def snoozed_ids(category=None, now=None, db_path=None):
    """Set of conversation_ids currently hidden by an unexpired snooze."""
    now_iso = to_iso(now or utcnow())
    sql = "SELECT conversation_id FROM snoozes WHERE snooze_until > ?"
    params = [now_iso]
    if category:
        sql += " AND category = ?"
        params.append(category)
    with connection(db_path) as conn:
        return {row["conversation_id"] for row in conn.execute(sql, params)}


def snooze_expiry_map(category=None, db_path=None):
    """conversation_id -> snooze_until datetime, including expired rows."""
    sql = "SELECT conversation_id, snooze_until FROM snoozes"
    params = ()
    if category:
        sql += " WHERE category = ?"
        params = (category,)
    with connection(db_path) as conn:
        return {
            row["conversation_id"]: from_iso(row["snooze_until"])
            for row in conn.execute(sql, params)
        }


def hidden_ids(category, now=None, db_path=None):
    """Every conversation_id hidden from a category, dismissed or snoozed."""
    return dismissed_ids(category, db_path=db_path) | snoozed_ids(
        category, now=now, db_path=db_path
    )


# ---------------------------------------------------------------------------
# Cached sync results
# ---------------------------------------------------------------------------

def replace_cached_items(category, items, synced_at=None, db_path=None):
    """Atomically swap the cached rows for one category.

    `items` is a list of dicts, each with at least entry_id; the whole dict is
    stored as JSON so new fields need no migration.
    """
    stamp = to_iso(synced_at or utcnow())
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM cached_items WHERE category = ?", (category,))
        conn.executemany(
            "INSERT INTO cached_items (category, entry_id, conversation_id, payload, synced_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    category,
                    item.get("entry_id") or "",
                    item.get("conversation_id"),
                    json.dumps(item, ensure_ascii=False, default=str),
                    stamp,
                )
                for item in items
            ],
        )
    return len(items)


def get_cached_items(category, db_path=None):
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT payload FROM cached_items WHERE category = ? ORDER BY id",
            (category,),
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def cached_counts(db_path=None):
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM cached_items GROUP BY category"
        ).fetchall()
    return {row["category"]: row["n"] for row in rows}


def clear_cached_items(category=None, db_path=None):
    with transaction(db_path) as conn:
        if category:
            cur = conn.execute("DELETE FROM cached_items WHERE category = ?", (category,))
        else:
            cur = conn.execute("DELETE FROM cached_items")
        return cur.rowcount


# ---------------------------------------------------------------------------
# PrivateGPT responses
# ---------------------------------------------------------------------------

def save_ai_response(entry_id, raw_response, parsed=None, conversation_id=None,
                     prompt_type="followup", prompt_version=None, db_path=None):
    parsed = parsed or {}
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO ai_responses (entry_id, conversation_id, prompt_type, "
            "  prompt_version, raw_response, situation, action, draft_reply, "
            "  urgency, urgency_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                conversation_id,
                prompt_type,
                config.PROMPT_VERSION if prompt_version is None else prompt_version,
                raw_response,
                parsed.get("situation"),
                parsed.get("action"),
                parsed.get("draft_reply"),
                parsed.get("urgency"),
                parsed.get("urgency_reason"),
                to_iso(utcnow()),
            ),
        )
        return cur.lastrowid


def get_ai_responses(entry_id, db_path=None):
    """All saved responses for a thread, newest first."""
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM ai_responses WHERE entry_id = ? ORDER BY created_at DESC, id DESC",
            (entry_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def latest_ai_response(entry_id, db_path=None):
    responses = get_ai_responses(entry_id, db_path=db_path)
    return responses[0] if responses else None


def latest_ai_response_map(db_path=None):
    """entry_id -> newest response, for rendering saved cards on page load."""
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT a.* FROM ai_responses a "
            "JOIN (SELECT entry_id, MAX(id) AS max_id FROM ai_responses GROUP BY entry_id) m "
            "  ON a.id = m.max_id"
        ).fetchall()
    return {row["entry_id"]: dict(row) for row in rows}


def clear_ai_responses(entry_id=None, db_path=None):
    with transaction(db_path) as conn:
        if entry_id:
            cur = conn.execute("DELETE FROM ai_responses WHERE entry_id = ?", (entry_id,))
        else:
            cur = conn.execute("DELETE FROM ai_responses")
        return cur.rowcount


# ---------------------------------------------------------------------------
# Sync runs
# ---------------------------------------------------------------------------

def start_sync_run(db_path=None):
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO sync_runs (started_at, status) VALUES (?, 'running')",
            (to_iso(utcnow()),),
        )
        return cur.lastrowid


def finish_sync_run(run_id, status, counts=None, error=None, db_path=None):
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE sync_runs SET finished_at = ?, status = ?, counts = ?, error = ? "
            "WHERE id = ?",
            (
                to_iso(utcnow()),
                status,
                json.dumps(counts or {}, ensure_ascii=False),
                error,
                run_id,
            ),
        )


def last_sync(db_path=None):
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    record = dict(row)
    record["counts"] = json.loads(record["counts"]) if record["counts"] else {}
    return record


def last_successful_sync(db_path=None):
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM sync_runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    record = dict(row)
    record["counts"] = json.loads(record["counts"]) if record["counts"] else {}
    return record


def recent_sync_runs(limit=20, db_path=None):
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for row in rows:
        record = dict(row)
        record["counts"] = json.loads(record["counts"]) if record["counts"] else {}
        out.append(record)
    return out


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

def purge_old_records(retention_days=None, now=None, db_path=None):
    """Delete dismissed/snoozed rows older than the retention window.

    AI responses are never touched here — they are kept indefinitely and only
    removed via clear_ai_responses() from the settings panel.
    """
    days = config.RETENTION_DAYS if retention_days is None else retention_days
    cutoff = to_iso((now or utcnow()) - timedelta(days=days))
    with transaction(db_path) as conn:
        dismissed = conn.execute(
            "DELETE FROM dismissals WHERE created_at < ?", (cutoff,)
        ).rowcount
        snoozed = conn.execute(
            "DELETE FROM snoozes WHERE created_at < ?", (cutoff,)
        ).rowcount
        runs = conn.execute(
            "DELETE FROM sync_runs WHERE started_at < ?", (cutoff,)
        ).rowcount
    return {"dismissals": dismissed, "snoozes": snoozed, "sync_runs": runs}


def stats(db_path=None):
    """Row counts per table, for the settings panel."""
    tables = ["dismissals", "snoozes", "ai_responses", "cached_items", "sync_runs"]
    out = {}
    with connection(db_path) as conn:
        for table in tables:
            out[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    out["schema_version"] = schema_version(db_path=db_path)
    return out
