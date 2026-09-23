"""
dashboard.py — assembles the view model from the SQLite cache.

Both the HTML dashboard and the /api/v1 routes read from here, so they can
never disagree about what is on screen. Nothing in this module talks to
Outlook; it only reads what the last sync wrote.

Snooze and dismissal filtering happens here rather than during sync, so an
expired snooze resurfaces on the next page load instead of waiting up to
15 minutes for the next sync cycle.
"""

import re
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
        "overview": build_overview(overdue, awaiting, meetings, now),
    }


# ---------------------------------------------------------------------------
# Overview — the home screen
#
# Everything here is derived from the same three lists the detail views show;
# nothing is scored or invented. Kept in plain functions, not the template,
# so the rules are testable.
# ---------------------------------------------------------------------------

# Avatar colours, picked by a stable hash of the person so the same person
# always gets the same colour.
AVATAR_TONES = 6

_WORD = re.compile(r"[A-Za-z\u00c0-\u024f]+")


def initials(name):
    """'Lars Petersen' -> 'LP'; 'lars.petersen@nordvind.dk' -> 'LP'."""
    text = (name or "").split("<")[0].split("@")[0].replace(".", " ")
    words = _WORD.findall(text)
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def avatar_tone(name):
    """0..AVATAR_TONES-1, stable across runs (unlike hash())."""
    return sum(ord(c) for c in (name or "").lower()) % AVATAR_TONES


def greeting(local_now):
    hour = local_now.hour
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 18:
        return "Good afternoon"
    return "Good evening"


def _plural(count, one, many):
    return f"{count} {one if count == 1 else many}"


def headline(overdue, awaiting, meetings):
    """One sentence saying what needs attention, or that nothing does."""
    parts = []
    if overdue:
        questions = sum(1 for i in overdue if i.get("priority") == "high")
        text = _plural(len(overdue), "email is", "emails are") + " waiting on you"
        if questions:
            text += f" ({_plural(questions, 'with a direct question', 'with direct questions')})"
        parts.append(text)
    if awaiting:
        parts.append(_plural(len(awaiting), "thread is", "threads are")
                     + " waiting on others")
    pending = sum(1 for m in meetings if m.get("pending_response"))
    if pending:
        parts.append(_plural(pending, "meeting needs", "meetings need")
                     + " an answer")
    if not parts:
        return "Nothing is waiting on you. Your inbox and calendar are clear."
    if len(parts) == 1:
        return parts[0][0].upper() + parts[0][1:] + "."
    sentence = ", ".join(parts[:-1]) + " and " + parts[-1] + "."
    return sentence[0].upper() + sentence[1:]


def focus_items(overdue, awaiting, meetings, limit=3):
    """The few things most worth doing first, each with its reason.

    Order of precedence, most pressing first:
      1. an unanswered email that asks you a direct question
      2. a meeting within the prep-warning window that you have not
         answered, or have no preparation for
      3. the longest-waiting email sent to you
      4. the thread you have been waiting on longest
    Each rule contributes at most one item before the next rule gets a turn,
    so the list shows a spread rather than three of the same kind.
    """
    window = config.MEETING_PREP_WARNING_HOURS
    questions = sorted((i for i in overdue if i.get("priority") == "high"),
                       key=lambda i: -i.get("days_waiting", 0))
    soon = [m for m in meetings
            if (m.get("hours_until") or 0) <= window
            and (m.get("pending_response") or m.get("unprepared"))]
    oldest_inbound = sorted(overdue, key=lambda i: -i.get("days_waiting", 0))
    oldest_sent = sorted(awaiting, key=lambda i: -i.get("days_waiting", 0))

    def email(item, reason, kind):
        return {"kind": kind, "entry_id": item.get("entry_id"),
                "title": item.get("subject") or "(no subject)",
                "reason": reason, "who": item.get("counterparty_display")}

    def meeting(item):
        reason = ("Starts " + item.get("date_display", "") + " - "
                  + ("not yet answered" if item.get("pending_response")
                     else "no preparation found"))
        return {"kind": "meeting", "entry_id": item.get("entry_id"),
                "title": item.get("subject") or "(no subject)",
                "reason": reason, "who": item.get("organizer")}

    candidates = [
        [email(i, f"Asks you a question - {i.get('days_waiting', 0)} days unanswered",
               "question") for i in questions],
        [meeting(m) for m in soon],
        [email(i, f"Waiting on you for {i.get('days_waiting', 0)} days", "reply")
         for i in oldest_inbound],
        [email(i, f"No reply for {i.get('days_waiting', 0)} days - chase it?",
               "chase") for i in oldest_sent],
    ]
    picked, seen = [], set()
    while len(picked) < limit and any(candidates):
        for queue in candidates:
            while queue:
                item = queue.pop(0)
                if item["entry_id"] not in seen:
                    seen.add(item["entry_id"])
                    picked.append(item)
                    break
            if len(picked) >= limit:
                break
    return picked


def response_ring(meetings):
    """Share of upcoming meetings you have answered. None if there are none."""
    total = len(meetings)
    if not total:
        return None
    answered = sum(1 for m in meetings if not m.get("pending_response"))
    percent = round(100 * answered / total)
    circumference = 2 * 3.14159265 * 42          # r=42 in the template's SVG
    return {"answered": answered, "total": total, "percent": percent,
            "dash": round(circumference * percent / 100, 2),
            "gap": round(circumference, 2)}


def calendar_day(meetings, local_now):
    """A timeline for the next day that has meetings, today if any remain.

    Returns the day label, an hour axis and positioned events. Overlapping
    meetings are placed side by side in lanes.
    """
    events_by_day = {}
    for m in meetings:
        if m.get("all_day"):
            continue
        start = db.from_iso(m.get("date"))
        end = db.from_iso(m.get("end")) or start
        if start is None:
            continue
        start, end = start.astimezone(), end.astimezone()
        if end <= local_now:
            continue
        events_by_day.setdefault(start.date(), []).append((start, end, m))
    if not events_by_day:
        return None

    day = min(events_by_day)
    events = sorted(events_by_day[day], key=lambda e: e[0])
    first = min(e[0].hour for e in events)
    last = max(e[1].hour + (1 if e[1].minute else 0) for e in events)
    axis_start = min(first, 8)
    axis_end = max(last, axis_start + 8, 17)
    axis_end = min(axis_end, 24)
    span = (axis_end - axis_start) * 60

    lanes_end, placed = [], []
    for start, end, m in events:
        lane = next((i for i, lane_end in enumerate(lanes_end)
                     if lane_end <= start), None)
        if lane is None:
            lane = len(lanes_end)
            lanes_end.append(end)
        else:
            lanes_end[lane] = end
        top = ((start.hour - axis_start) * 60 + start.minute) / span * 100
        minutes = max((end - start).total_seconds() / 60, 20)
        placed.append({
            "entry_id": m.get("entry_id"),
            "subject": m.get("subject"),
            "time": start.strftime("%H:%M") + "-" + end.strftime("%H:%M"),
            "location": m.get("location"),
            "top": round(max(top, 0), 2),
            "height": round(min(minutes / span * 100, 100 - max(top, 0)), 2),
            "lane": lane,
            # Too short for two lines: title and time go on one line. Judged
            # as a share of the axis, not in minutes, because the same hour
            # is taller on a short day than on a long one. 12% of the ~300px
            # track is the ~36px two lines need.
            "compact": minutes / span < 0.12,
            "tone": ("pending" if m.get("pending_response")
                     else "unprepared" if m.get("unprepared") else "ok"),
        })
    lanes = max(len(lanes_end), 1)
    for event in placed:
        event["width"] = round(100 / lanes, 2)
        event["left"] = round(event["lane"] * 100 / lanes, 2)

    if day == local_now.date():
        label = "Today"
    elif (day - local_now.date()).days == 1:
        label = "Tomorrow"
    else:
        label = day.strftime("%A %d %b")
    return {
        "label": label,
        "date": day.strftime("%a %d %b"),
        "hours": [f"{h:02d}:00" for h in range(axis_start, axis_end + 1)],
        "hour_count": axis_end - axis_start,
        "events": placed,
        "later": sum(len(v) for d, v in events_by_day.items() if d != day),
    }


def build_overview(overdue, awaiting, meetings, now):
    local_now = now.astimezone()
    for item in list(overdue) + list(awaiting):
        who = item.get("counterparty_display") or ""
        item["initials"] = initials(who)
        item["tone"] = avatar_tone(who)
    return {
        "greeting": greeting(local_now),
        "date": local_now.strftime("%A %d %B"),
        "headline": headline(overdue, awaiting, meetings),
        "focus": focus_items(overdue, awaiting, meetings),
        "ring": response_ring(meetings),
        "day": calendar_day(meetings, local_now),
        "top_overdue": sorted(overdue, key=lambda i: (
            i.get("priority") != "high", -i.get("days_waiting", 0)))[:4],
        "top_awaiting": sorted(awaiting,
                               key=lambda i: -i.get("days_waiting", 0))[:5],
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
