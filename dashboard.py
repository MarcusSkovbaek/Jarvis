"""
dashboard.py — assembles the view model from the SQLite cache.

Both the HTML dashboard and the /api/v1 routes read from here, so they can
never disagree about what is on screen. Nothing in this module talks to
Outlook; it only reads what the last sync wrote.

Snooze and dismissal filtering happens here rather than during sync, so an
expired snooze resurfaces on the next page load instead of waiting up to
15 minutes for the next sync cycle.
"""

from datetime import datetime, timezone

import config
import db
import email_processor
import response_renderer


def _attach_saved_responses(items, responses):
    for item in items:
        record = responses.get(item.get("entry_id"))
        item["saved_response"] = (
            response_renderer.card_from_record(item, record) if record else None
        )
    return items


def visible_items(category, now=None, db_path=None, responses=None):
    """Cached rows for a category, minus anything dismissed or still snoozed."""
    hidden = db.hidden_ids(category, now=now, db_path=db_path)
    items = [item for item in db.get_cached_items(category, db_path=db_path)
             if item.get("conversation_id") not in hidden]
    if responses is None:
        responses = db.latest_ai_response_map(db_path=db_path)
    return _attach_saved_responses(items, responses)


def build_view(now=None, db_path=None):
    """Everything the dashboard needs, in one dict."""
    now = now or datetime.now(timezone.utc)
    responses = db.latest_ai_response_map(db_path=db_path)

    overdue = visible_items(config.CATEGORY_OVERDUE_INBOUND, now, db_path, responses)
    awaiting = visible_items(config.CATEGORY_AWAITING_REPLY, now, db_path, responses)
    meetings = visible_items(config.CATEGORY_MEETING, now, db_path, responses)

    bands = email_processor.group_by_age_band(awaiting)
    last = db.last_sync(db_path=db_path)
    last_ok = db.last_successful_sync(db_path=db_path)

    return {
        "now": now,
        "overdue_inbound": overdue,
        "awaiting_reply": awaiting,
        "awaiting_reply_bands": [
            {"label": label, "items": items} for label, items in bands.items()
        ],
        "meetings": meetings,
        "counts": {
            config.CATEGORY_OVERDUE_INBOUND: len(overdue),
            config.CATEGORY_AWAITING_REPLY: len(awaiting),
            config.CATEGORY_MEETING: len(meetings),
            "meetings_pending": sum(1 for m in meetings if m.get("pending_response")),
            "meetings_unprepared": sum(1 for m in meetings if m.get("unprepared")),
            "high_priority": sum(1 for i in overdue if i.get("priority") == "high"),
        },
        "last_sync": _sync_summary(last),
        "last_successful_sync": _sync_summary(last_ok),
        "titles": config.CATEGORY_TITLES,
        "settings": config.public_settings(),
        "stats": db.stats(db_path=db_path),
    }


def _sync_summary(record):
    if not record:
        return None
    finished = db.from_iso(record.get("finished_at") or record.get("started_at"))
    return {
        "status": record.get("status"),
        "at": finished.isoformat() if finished else None,
        "at_display": finished.astimezone().strftime("%d %b %Y, %H:%M:%S")
        if finished else "never",
        "counts": record.get("counts") or {},
        "error": record.get("error"),
    }


def find_item(category, conversation_id=None, entry_id=None, db_path=None):
    """Look one cached row back up — used by the prompt and action routes."""
    for item in db.get_cached_items(category, db_path=db_path):
        if conversation_id and item.get("conversation_id") == conversation_id:
            return item
        if entry_id and item.get("entry_id") == entry_id:
            return item
    return None


def find_item_anywhere(conversation_id=None, entry_id=None, db_path=None):
    for category in config.CATEGORIES:
        item = find_item(category, conversation_id, entry_id, db_path=db_path)
        if item:
            return category, item
    return None, None
