"""
verify_db.py — Phase 1 validation item 1: db.py runs without error, creates
every table, and each read/write path behaves as specified.

Run:  python tests\verify_db.py
Uses a throwaway database in the system temp directory; never touches jarvis.db.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import db  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def main():
    tmpdir = tempfile.mkdtemp(prefix="jarvis_verify_")
    path = os.path.join(tmpdir, "verify.db")
    print(f"Temporary database: {path}\n")

    # -- 1. creation and migration -----------------------------------------
    print("1. init_db / migrations")
    applied = db.init_db(path)
    check("migrations applied on fresh db", applied == [(1, "initial schema")], str(applied))
    check("schema_version == 1", db.schema_version(path) == db.SCHEMA_VERSION)
    again = db.init_db(path)
    check("re-running init_db applies nothing", again == [], str(again))

    print("\n2. schema")
    schema = db.describe_schema(path)
    # sqlite_sequence is SQLite's own AUTOINCREMENT bookkeeping table.
    tables = sorted(
        o["name"] for o in schema
        if o["type"] == "table" and not o["name"].startswith("sqlite_")
    )
    indexes = sorted(o["name"] for o in schema if o["type"] == "index")
    expected_tables = sorted(
        ["schema_meta", "dismissals", "snoozes", "ai_responses",
         "cached_items", "sync_runs"]
    )
    check("all expected tables exist", tables == expected_tables, str(tables))
    print(f"       indexes: {indexes}")
    print(f"       tables:  {tables}")

    # -- 3. dismissals ------------------------------------------------------
    print("\n3. dismissals")
    db.dismiss("CONV-A", config.CATEGORY_AWAITING_REPLY, "ENTRY-A", "Subject A", db_path=path)
    db.dismiss("CONV-A", config.CATEGORY_AWAITING_REPLY, "ENTRY-A", "Subject A", db_path=path)
    check("dismissal is idempotent (upsert)",
          db.stats(path)["dismissals"] == 1, str(db.stats(path)["dismissals"]))
    check("dismissed_ids returns it",
          db.dismissed_ids(config.CATEGORY_AWAITING_REPLY, db_path=path) == {"CONV-A"})
    check("dismissal scoped to its category",
          db.dismissed_ids(config.CATEGORY_OVERDUE_INBOUND, db_path=path) == set())
    check("undismiss removes it",
          db.undismiss("CONV-A", config.CATEGORY_AWAITING_REPLY, db_path=path) == 1)
    db.dismiss("CONV-A", config.CATEGORY_AWAITING_REPLY, db_path=path)

    # -- 4. snoozes ---------------------------------------------------------
    print("\n4. snoozes")
    until = db.snooze("CONV-B", config.CATEGORY_OVERDUE_INBOUND, db_path=path)
    delta_days = (until - db.utcnow()).total_seconds() / 86400
    check(f"default snooze is {config.SNOOZE_DAYS} days",
          abs(delta_days - config.SNOOZE_DAYS) < 0.01, f"{delta_days:.4f} days")
    check("snoozed_ids hides it",
          db.snoozed_ids(config.CATEGORY_OVERDUE_INBOUND, db_path=path) == {"CONV-B"})

    db.snooze("CONV-C", config.CATEGORY_OVERDUE_INBOUND, seconds=1, db_path=path)
    check("short snooze hides item immediately",
          "CONV-C" in db.snoozed_ids(config.CATEGORY_OVERDUE_INBOUND, db_path=path))
    time.sleep(1.2)
    check("item resurfaces after expiry",
          "CONV-C" not in db.snoozed_ids(config.CATEGORY_OVERDUE_INBOUND, db_path=path))
    check("expired snooze row is still on record (purged later, not instantly)",
          "CONV-C" in db.snooze_expiry_map(db_path=path))
    check("hidden_ids unions dismissed + snoozed",
          db.hidden_ids(config.CATEGORY_OVERDUE_INBOUND, db_path=path) == {"CONV-B"})

    # -- 5. cached items ----------------------------------------------------
    print("\n5. cached items")
    items = [
        {"entry_id": "E1", "conversation_id": "C1", "subject": "Hello", "days": 9,
         "attachments": ["spec.pdf"]},
        {"entry_id": "E2", "conversation_id": "C2", "subject": "Ping", "days": 31,
         "attachments": []},
    ]
    db.replace_cached_items(config.CATEGORY_AWAITING_REPLY, items, db_path=path)
    loaded = db.get_cached_items(config.CATEGORY_AWAITING_REPLY, db_path=path)
    check("round-trips payload exactly", loaded == items, str(loaded))
    db.replace_cached_items(config.CATEGORY_AWAITING_REPLY, items[:1], db_path=path)
    check("replace swaps the whole category",
          len(db.get_cached_items(config.CATEGORY_AWAITING_REPLY, db_path=path)) == 1)
    check("counts per category",
          db.cached_counts(path) == {config.CATEGORY_AWAITING_REPLY: 1},
          str(db.cached_counts(path)))

    # -- 6. AI responses ----------------------------------------------------
    print("\n6. PrivateGPT responses")
    parsed = {"situation": "Waiting 9 days.", "action": "reply",
              "draft_reply": "Following up.", "urgency": "Medium",
              "urgency_reason": "Blocking delivery"}
    db.save_ai_response("E1", "RAW#1", parsed, conversation_id="C1", db_path=path)
    time.sleep(0.01)
    db.save_ai_response("E1", "RAW#2", parsed, conversation_id="C1", db_path=path)
    history = db.get_ai_responses("E1", db_path=path)
    check("history kept per thread", len(history) == 2, f"{len(history)} rows")
    check("newest first", history[0]["raw_response"] == "RAW#2",
          history[0]["raw_response"])
    check("parsed fields persisted", history[0]["urgency"] == "Medium")
    check("prompt_version stamped",
          history[0]["prompt_version"] == config.PROMPT_VERSION)
    check("latest_ai_response_map keyed by entry_id",
          db.latest_ai_response_map(path)["E1"]["raw_response"] == "RAW#2")

    # -- 7. sync runs -------------------------------------------------------
    print("\n7. sync runs")
    run_id = db.start_sync_run(path)
    check("run starts in 'running'", db.last_sync(path)["status"] == "running")
    db.finish_sync_run(run_id, "ok", {"awaiting_reply": 3, "overdue_inbound": 5,
                                      "meeting": 2}, db_path=path)
    last = db.last_sync(path)
    check("counts stored as dict", last["counts"]["overdue_inbound"] == 5, str(last["counts"]))
    failed = db.start_sync_run(path)
    db.finish_sync_run(failed, "error", error="Outlook not running", db_path=path)
    check("last_sync sees the failure", db.last_sync(path)["status"] == "error")
    check("last_successful_sync skips it",
          db.last_successful_sync(path)["id"] == run_id)

    # -- 8. retention -------------------------------------------------------
    print("\n8. retention purge")
    old = db.to_iso(db.utcnow() - db.timedelta(days=config.RETENTION_DAYS + 1))
    with db.transaction(path) as conn:
        conn.execute("UPDATE dismissals SET created_at = ?", (old,))
        conn.execute("UPDATE snoozes SET created_at = ? WHERE conversation_id = 'CONV-C'",
                     (old,))
    purged = db.purge_old_records(db_path=path)
    check("old dismissal purged", purged["dismissals"] == 1, str(purged))
    check("old snooze purged", purged["snoozes"] == 1, str(purged))
    check("recent snooze survives",
          db.snoozed_ids(config.CATEGORY_OVERDUE_INBOUND, db_path=path) == {"CONV-B"})
    check("AI responses survive the purge (kept indefinitely)",
          db.stats(path)["ai_responses"] == 2)
    check("manual clear removes them", db.clear_ai_responses(db_path=path) == 2)

    print("\nFinal table counts:", db.stats(path))

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("db.py — ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
