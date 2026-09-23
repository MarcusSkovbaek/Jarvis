"""
api.py — the versioned JSON API, mounted at config.API_PREFIX (/api/v1).

Every piece of processed data is reachable here as well as in the rendered
dashboard, so a future frontend can be built against these routes alone
without touching the current UI.

The only routes with side effects outside SQLite are /open and /reply, which
map onto the two user-triggered Outlook actions. Nothing here ever sends mail.
"""

import logging

from flask import Blueprint, current_app, jsonify, render_template, request

import config
import dashboard
import page_check
import db
import prompt_builder
import response_renderer
from outlook_reader import OutlookReader, OutlookUnavailable

log = logging.getLogger("jarvis.api")

bp = Blueprint("api", __name__, url_prefix=config.API_PREFIX)


def _payload():
    return request.get_json(silent=True) or {}


def _bad_request(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def _outlook():
    """Reader used by the two action routes. Injected in tests via app config."""
    factory = current_app.config.get("OUTLOOK_READER_FACTORY") or OutlookReader
    return factory()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@bp.get("/status")
def status():
    view = dashboard.build_view()
    return jsonify({
        "ok": True,
        "counts": view["counts"],
        "last_sync": view["last_sync"],
        "last_successful_sync": view["last_successful_sync"],
        "prompt_version": config.PROMPT_VERSION,
        "backend": config.OUTLOOK_BACKEND,
        "user_email": config.USER_EMAIL,
    })


@bp.get("/items")
def all_items():
    view = dashboard.build_view()
    return jsonify({
        "ok": True,
        config.CATEGORY_OVERDUE_INBOUND: view["overdue_inbound"],
        config.CATEGORY_AWAITING_REPLY: view["awaiting_reply"],
        "awaiting_reply_bands": view["awaiting_reply_bands"],
        config.CATEGORY_MEETING: view["meetings"],
        "counts": view["counts"],
        "last_sync": view["last_sync"],
    })


@bp.get("/items/<category>")
def items_by_category(category):
    if category not in config.CATEGORIES:
        return _bad_request(f"Unknown category: {category}", 404)
    items = dashboard.visible_items(category)
    body = {"ok": True, "category": category, "count": len(items), "items": items}
    if category == config.CATEGORY_AWAITING_REPLY:
        import email_processor
        body["bands"] = [
            {"label": label, "items": band_items}
            for label, band_items in email_processor.group_by_age_band(items).items()
        ]
    return jsonify(body)


@bp.get("/settings")
def settings():
    return jsonify({
        "ok": True,
        "settings": config.public_settings(),
        "stats": db.stats(),
        "prompt_types": prompt_builder.available_types(),
        "prompt_changelog": [
            {"version": v, "date": d, "type": t, "note": n}
            for v, d, t, n in prompt_builder.PROMPT_CHANGELOG
        ],
    })


@bp.get("/sync-log")
def sync_log():
    return jsonify({"ok": True, "runs": db.recent_sync_runs(limit=20)})


# ---------------------------------------------------------------------------
# Row actions
# ---------------------------------------------------------------------------

@bp.post("/actions/snooze")
def snooze():
    data = _payload()
    conversation_id = data.get("conversation_id")
    category = data.get("category")
    if not conversation_id or category not in config.CATEGORIES:
        return _bad_request("category and conversation_id are required")
    until = db.snooze(
        conversation_id, category,
        days=data.get("days"), seconds=data.get("seconds"),
        entry_id=data.get("entry_id"), subject=data.get("subject"),
    )
    log.info("Snoozed %s in %s until %s", conversation_id, category, until)
    return jsonify({"ok": True, "snoozed_until": until.isoformat(),
                    "days": config.SNOOZE_DAYS if data.get("days") is None
                    else data.get("days")})


@bp.post("/actions/dismiss")
def dismiss():
    data = _payload()
    conversation_id = data.get("conversation_id")
    category = data.get("category")
    if not conversation_id or category not in config.CATEGORIES:
        return _bad_request("category and conversation_id are required")
    db.dismiss(conversation_id, category, entry_id=data.get("entry_id"),
               subject=data.get("subject"))
    log.info("Dismissed %s in %s (no reply needed)", conversation_id, category)
    return jsonify({"ok": True})


@bp.post("/actions/restore")
def restore():
    """Undo a snooze or a dismissal."""
    data = _payload()
    conversation_id = data.get("conversation_id")
    category = data.get("category")
    if not conversation_id or category not in config.CATEGORIES:
        return _bad_request("category and conversation_id are required")
    removed = db.unsnooze(conversation_id, category) + \
        db.undismiss(conversation_id, category)
    return jsonify({"ok": True, "removed": removed})


# ---------------------------------------------------------------------------
# PrivateGPT bridge — manual, user-initiated, one direction at a time
# ---------------------------------------------------------------------------

@bp.get("/prompt")
def prompt():
    category = request.args.get("category")
    conversation_id = request.args.get("conversation_id")
    entry_id = request.args.get("entry_id")
    prompt_type = request.args.get("type", prompt_builder.PROMPT_TYPE_FOLLOWUP)

    if category and category in config.CATEGORIES:
        item = dashboard.find_item(category, conversation_id, entry_id)
    else:
        category, item = dashboard.find_item_anywhere(conversation_id, entry_id)
    if item is None:
        return _bad_request("No cached item matches that id", 404)

    try:
        text = prompt_builder.build(item, prompt_type)
    except ValueError as exc:
        return _bad_request(str(exc))
    # Logged so there is a record of every prompt handed to the clipboard —
    # the only point at which any data leaves the app.
    log.info("Built %s prompt v%s for %s (%s chars, %s attachment(s)) - "
             "subject %r", prompt_type, prompt_builder.prompt_version(prompt_type),
             item.get("entry_id"), len(text), len(item.get("attachments") or []),
             (item.get("subject") or "")[:60])
    return jsonify({"ok": True, "prompt": text,
                    "prompt_version": prompt_builder.prompt_version(prompt_type),
                    "entry_id": item.get("entry_id"),
                    "category": category})


@bp.get("/prompt/dashboard")
def dashboard_prompt():
    """The whole-dashboard prompt, for PrivateGPT to turn into an HTML page."""
    view = dashboard.build_view()
    text = prompt_builder.build_dashboard_prompt(view)
    counts = view["counts"]
    # Logged by size and counts only: like the follow-up prompt, this is the
    # moment data is handed to the clipboard, so there is a record of it.
    log.info("Built dashboard prompt v%s (%s chars: %s awaiting your reply, "
             "%s no response, %s meetings)", config.DASHBOARD_PROMPT_VERSION,
             len(text), counts[config.CATEGORY_OVERDUE_INBOUND],
             counts[config.CATEGORY_AWAITING_REPLY],
             counts[config.CATEGORY_MEETING])
    return jsonify({"ok": True, "prompt": text,
                    "prompt_version": config.DASHBOARD_PROMPT_VERSION,
                    "csp_line": prompt_builder.CSP_LINE,
                    "chars": len(text), "words": len(text.split())})


# A generated page is a few tens of KB; anything past this is not one.
MAX_PAGE_CHECK_CHARS = 5_000_000


@bp.post("/check-page")
def check_page():
    """Check a PrivateGPT-generated page before the user opens it.

    The page's source arrives from the dashboard's file picker, read by the
    browser from the user's own disk and posted to this local server; it is
    checked in memory and not stored. Only the verdict is logged.
    """
    payload = request.get_json(silent=True) or {}
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        return _bad_request("No page content received")
    if len(source) > MAX_PAGE_CHECK_CHARS:
        return _bad_request("That file is too large to be a generated page")
    result = page_check.check_page(source)
    log.info("Checked a generated page (%s chars): %s, %s blocking, "
             "%s advisory", result["size"], result["verdict"],
             len(result["blocking"]), len(result["advisory"]))
    return jsonify({"ok": True, **result})


@bp.post("/responses")
def save_response():
    """Parse a pasted PrivateGPT response and render the fixed card."""
    data = _payload()
    raw = data.get("raw") or ""
    entry_id = data.get("entry_id")
    conversation_id = data.get("conversation_id")
    category = data.get("category")

    if not entry_id and not conversation_id:
        return _bad_request("entry_id or conversation_id is required")

    if category in config.CATEGORIES:
        item = dashboard.find_item(category, conversation_id, entry_id)
    else:
        category, item = dashboard.find_item_anywhere(conversation_id, entry_id)
    if item is None:
        return _bad_request("No cached item matches that id", 404)

    parsed = response_renderer.parse(raw)
    if not parsed["ok"]:
        # Nothing is saved and nothing is guessed — the user fixes the paste.
        log.info("Rejected malformed PrivateGPT response for %s: %s",
                 item.get("entry_id"), parsed.get("detail"))
        return jsonify({"ok": False, "error": parsed["error"],
                        "detail": parsed.get("detail"),
                        "missing": parsed.get("missing", [])}), 200

    db.save_ai_response(
        item["entry_id"], parsed["raw"], parsed["fields"],
        conversation_id=item.get("conversation_id"),
        prompt_type=data.get("type", prompt_builder.PROMPT_TYPE_FOLLOWUP),
    )
    saved = db.latest_ai_response(item["entry_id"])
    card = response_renderer.build_card(item, parsed,
                                        saved_at=saved["created_at"])
    return jsonify({
        "ok": True,
        "card": card,
        "html": render_template("response_card.html", card=card),
    })


@bp.get("/responses/<path:entry_id>")
def response_history(entry_id):
    return jsonify({"ok": True, "responses": db.get_ai_responses(entry_id)})


@bp.delete("/responses")
def clear_responses():
    removed = db.clear_ai_responses(_payload().get("entry_id"))
    log.info("Cleared %s saved PrivateGPT response(s)", removed)
    return jsonify({"ok": True, "removed": removed})


# ---------------------------------------------------------------------------
# Outlook actions — the only writes outside SQLite
# ---------------------------------------------------------------------------

@bp.post("/outlook/open")
def outlook_open():
    entry_id = _payload().get("entry_id")
    if not entry_id:
        return _bad_request("entry_id is required")
    reader = _outlook()
    try:
        reader.open_item(entry_id)
    except OutlookUnavailable as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    finally:
        reader.close()
    return jsonify({"ok": True, "entry_id": entry_id})


@bp.post("/outlook/reply")
def outlook_reply():
    """Open a Reply All draft pre-filled with the drafted text."""
    data = _payload()
    entry_id = data.get("entry_id")
    if not entry_id:
        return _bad_request("entry_id is required")
    reader = _outlook()
    try:
        reader.create_reply_all_draft(entry_id, data.get("body") or "")
    except OutlookUnavailable as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    finally:
        reader.close()
    return jsonify({"ok": True, "entry_id": entry_id})


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

@bp.post("/sync")
def trigger_sync():
    loop = current_app.config.get("SYNC_LOOP")
    if loop is None:
        return _bad_request("No sync loop is attached to this app", 503)
    result = loop.sync_now()
    return jsonify({"ok": result["status"] == "ok", "result": {
        "status": result["status"],
        "counts": result.get("counts", {}),
        "error": result.get("error"),
    }})


@bp.post("/purge")
def purge():
    """Feature 4 settings panel: manually run the 30-day purge."""
    removed = db.purge_old_records()
    log.info("Manual purge removed %s", removed)
    return jsonify({"ok": True, "removed": removed,
                    "retention_days": config.RETENTION_DAYS})
