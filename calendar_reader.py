"""
calendar_reader.py — Feature 3: upcoming meetings for today and the next
7 days, with the two flags the dashboard cares about.

Meetings are strictly read-only. Nothing here creates, edits, accepts or
declines anything; clicking a row opens the meeting in Outlook and that is the
full extent of the interaction.

Two flags are raised:
  * pending  — you have neither accepted nor declined
  * unprepared — the meeting starts within MEETING_PREP_WARNING_HOURS and has
    neither notes in its body nor an email thread that looks related

"Looks related" is a heuristic: the meeting subject shares at least
config.MEETING_PREP_MIN_SHARED_WORDS significant words with a known mail
conversation topic. It is deliberately conservative — a missed match costs a
false "unprepared" flag, not a missed meeting.
"""

import logging
import re

import config

log = logging.getLogger("jarvis.calendar")

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def significant_words(text):
    """Lowercased content words, stopwords and very short tokens removed."""
    words = {w.lower() for w in _WORD_RE.findall(text or "")}
    return {w for w in words
            if len(w) >= 3 and w not in config.MEETING_PREP_STOPWORDS}


def related_topic(subject, topics):
    """The first conversation topic that shares enough words with `subject`."""
    meeting_words = significant_words(subject)
    if not meeting_words:
        return None
    for topic in topics:
        shared = meeting_words & significant_words(topic)
        if len(shared) >= config.MEETING_PREP_MIN_SHARED_WORDS:
            return {"topic": topic, "shared_words": sorted(shared)}
    return None


def has_notes(body):
    return len((body or "").strip()) >= config.MEETING_PREP_MIN_NOTE_CHARS


def hours_until(start, now):
    return (start - now).total_seconds() / 3600.0


def process_calendar(meetings, now, topics=None):
    """Turn normalised appointments into JSON-safe dashboard rows."""
    topics = topics or set()
    results = []

    for meeting in meetings:
        start = meeting.get("start")
        end = meeting.get("end") or start
        if start is None:
            continue

        status = meeting.get("response_status", config.OL_RESPONSE_NONE)
        pending = status in config.PENDING_RESPONSE_STATUSES

        until = hours_until(start, now)
        # Negative means it has already begun but has not finished; that still
        # counts as "imminent" for the preparation check.
        imminent = until <= config.MEETING_PREP_WARNING_HOURS

        notes = has_notes(meeting.get("body"))
        match = related_topic(meeting.get("subject"), topics)
        unprepared = bool(imminent and not notes and match is None)

        flags = []
        if pending:
            flags.append("pending")
        if unprepared:
            flags.append("unprepared")

        results.append({
            "category": config.CATEGORY_MEETING,
            "entry_id": meeting.get("entry_id") or "",
            "conversation_id": meeting.get("entry_id") or "",
            "subject": meeting.get("subject") or "(no subject)",
            "date": start.isoformat(),
            "date_display": start.astimezone().strftime("%a %d %b, %H:%M"),
            "end": end.isoformat() if end else None,
            "duration_minutes": meeting.get("duration_minutes") or 0,
            "duration_display": _duration_display(meeting.get("duration_minutes")),
            "organizer": meeting.get("organizer") or "",
            "organizer_address": meeting.get("organizer_address") or "",
            "response_status": status,
            "response_label": meeting.get("response_label")
            or config.RESPONSE_STATUS_LABELS.get(status, "Unknown"),
            "attendee_count": meeting.get("attendee_count") or 0,
            "location": meeting.get("location") or "",
            "all_day": bool(meeting.get("all_day")),
            "recurring": bool(meeting.get("recurring")),
            "hours_until": round(until, 1),
            "starts_within_warning": bool(imminent),
            "has_notes": notes,
            "related_thread": match["topic"] if match else None,
            "pending_response": pending,
            "unprepared": unprepared,
            "flags": flags,
        })

    results.sort(key=lambda r: r["date"])
    return results


def _duration_display(minutes):
    minutes = int(minutes or 0)
    if minutes <= 0:
        return ""
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def summarise(meetings):
    """Counts used by sync logging and the dashboard header."""
    return {
        "total": len(meetings),
        "pending": sum(1 for m in meetings if m["pending_response"]),
        "unprepared": sum(1 for m in meetings if m["unprepared"]),
    }
