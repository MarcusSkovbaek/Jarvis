"""
email_processor.py — Feature 1 (sent, awaiting reply) and Feature 2 (inbound,
overdue) detection logic.

This module is pure: it takes normalised records from outlook_reader and
returns JSON-safe result dicts. It never touches Outlook and never touches
SQLite. Snooze and dismissal filtering happens in the view layer, so an
expired snooze resurfaces immediately rather than waiting for the next sync.

Interpretation notes, where the spec left room:

* Days waiting is measured in calendar days between local dates, so a thread
  sent nine days ago reads as 9 whatever time of day the sync runs.
* A forward from the user neither resolves a thread (decision #3) nor resets
  the clock: the anchor message stays the last real send to the original
  recipients.
* A reply from a CC'd recipient resolves the thread, per the Feature 1 wording
  ("...from any address the email was sent To or CC'd to"). Flip
  config.CC_REPLY_RESOLVES_THREAD to require a reply from a To recipient.
"""

import logging

import config
from outlook_reader import domain_of, local_part_of

log = logging.getLogger("jarvis.email")


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def calendar_days_since(then, now):
    """Whole calendar days between two instants, compared as local dates.

    Weekends included (decision #2). Comparing dates rather than a raw
    timedelta keeps the count stable regardless of the time of day.
    """
    if then is None or now is None:
        return 0
    return (now.astimezone().date() - then.astimezone().date()).days


def timestamp_of(record):
    """The instant a message entered the thread, from the right field."""
    if record.get("is_from_user"):
        return record.get("sent_on") or record.get("received_time")
    return record.get("received_time") or record.get("sent_on")


def age_band(days):
    """Map a day count onto a config.AGE_BANDS label."""
    for label, low, high in config.AGE_BANDS:
        if days >= low and (high is None or days < high):
            return label
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def is_auto_reply(record):
    """True for out-of-office and other machine-generated replies."""
    message_class = (record.get("message_class") or "").lower()
    for known in config.AUTO_REPLY_MESSAGE_CLASSES:
        if known.lower() in message_class:
            return True

    subject = (record.get("subject") or "").strip().lower()
    for prefix in config.AUTO_REPLY_SUBJECT_PREFIXES:
        if subject.startswith(prefix.lower()):
            return True

    headers = record.get("headers") or {}
    for name in config.AUTO_REPLY_HEADERS:
        if name not in headers:
            continue
        value = (headers.get(name) or "").strip().lower()
        if name == "auto-submitted" and value in config.AUTO_SUBMITTED_HUMAN_VALUES:
            continue
        return True
    return False


def is_mailing_list(record, address=None):
    """True for newsletters and list traffic."""
    headers = record.get("headers") or {}
    for name in config.MAILING_LIST_HEADERS:
        if name not in headers:
            continue
        if name == "precedence":
            value = (headers.get(name) or "").strip().lower()
            if value in config.MAILING_LIST_PRECEDENCE_VALUES:
                return True
            continue
        return True

    candidate = (address or record.get("sender_address") or "").lower()
    return any(pattern in candidate
               for pattern in config.MAILING_LIST_SENDER_PATTERNS)


def is_mailing_list_address(address):
    return any(pattern in (address or "").lower()
               for pattern in config.MAILING_LIST_SENDER_PATTERNS)


def is_excluded_address(address):
    """True for addresses Jarvis should never chase or be chased by."""
    address = (address or "").strip().lower()
    if not address:
        return True
    if address in {a.lower() for a in config.EXCLUDED_DOMAIN_EXCEPTIONS}:
        return False
    if local_part_of(address) in config.EXCLUDED_LOCAL_PARTS:
        return True
    domain = domain_of(address)
    for excluded in config.EXCLUDED_DOMAINS:
        excluded = excluded.strip().lower()
        if domain == excluded or domain.endswith("." + excluded):
            return True
    return False


def _has_prefix(subject, prefixes):
    subject = (subject or "").strip().lower()
    return any(subject.startswith(prefix) for prefix in prefixes)


def is_forward(record):
    return _has_prefix(record.get("subject"), config.FORWARD_SUBJECT_PREFIXES)


def is_reply(record):
    return _has_prefix(record.get("subject"), config.REPLY_SUBJECT_PREFIXES)


def has_priority_marker(record):
    """Feature 2: a question mark in the subject or the top of the body."""
    subject = record.get("subject") or ""
    body = (record.get("body") or "")[: config.PRIORITY_BODY_SCAN_CHARS]
    haystack = f"{subject}\n{body}"
    return any(marker in haystack for marker in config.PRIORITY_MARKERS)


# ---------------------------------------------------------------------------
# Threading
# ---------------------------------------------------------------------------

def thread_key(record):
    """Group by ConversationID, falling back to the normalised topic."""
    return (record.get("conversation_id")
            or f"topic::{(record.get('conversation_topic') or '').strip().lower()}")


def build_threads(sent, inbox):
    """Merge both folders into {thread_key: [messages sorted oldest first]}."""
    threads = {}
    for record in list(sent) + list(inbox):
        threads.setdefault(thread_key(record), []).append(record)
    for messages in threads.values():
        messages.sort(key=lambda r: (timestamp_of(r) is None, timestamp_of(r)))
    return threads


def _recipients(record, include_cc=True):
    """Non-user To (and optionally CC) addresses on a message."""
    people = list(record.get("to") or [])
    if include_cc:
        people += list(record.get("cc") or [])
    return [p for p in people if p.get("address")
            and not config.is_user(p["address"])]


def _format_people(people):
    out = []
    for person in people:
        name = (person.get("name") or "").strip()
        address = person.get("address") or ""
        out.append(f"{name} <{address}>" if name and name.lower() != address
                   else address)
    return out


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _display_date(dt):
    return dt.astimezone().strftime("%d %b %Y, %H:%M") if dt is not None else ""


# ---------------------------------------------------------------------------
# Feature 1 — sent emails with no response
# ---------------------------------------------------------------------------

def find_awaiting_reply(sent, inbox, now, threads=None):
    """Threads where the user's last real send has gone unanswered."""
    threads = threads if threads is not None else build_threads(sent, inbox)
    results = []

    for key, messages in threads.items():
        # Candidate anchors: the user's own messages that actually went To
        # someone. Forwards are skipped — they neither resolve the thread nor
        # restart the clock. Pure-BCC sends have no To recipient, so they never
        # produce an anchor and are excluded automatically.
        anchors = [
            m for m in messages
            if m.get("is_from_user")
            and not is_forward(m)
            and _recipients(m, include_cc=False)
        ]
        if not anchors:
            continue
        anchor = anchors[-1]
        anchor_time = timestamp_of(anchor)
        if anchor_time is None:
            continue

        to_people = _recipients(anchor, include_cc=False)
        # Excluded only when every To recipient is excluded; a thread with one
        # real recipient and one noreply address still deserves chasing.
        live_to = [p for p in to_people
                   if not is_excluded_address(p["address"])
                   and not is_mailing_list_address(p["address"])]
        if not live_to:
            continue

        watched = {p["address"] for p in live_to}
        if config.CC_REPLY_RESOLVES_THREAD:
            watched |= {p["address"] for p in _recipients(anchor)
                        if not is_excluded_address(p["address"])}

        # A reply is any later message in the thread from a watched address
        # that is not machine-generated.
        replies = [
            m for m in messages
            if not m.get("is_from_user")
            and not is_auto_reply(m)
            and m.get("sender_address") in watched
            and (timestamp_of(m) or anchor_time) > anchor_time
        ]
        if replies:
            continue

        days = calendar_days_since(anchor_time, now)
        if days < config.SENT_AWAITING_REPLY_DAYS:
            continue
        band = age_band(days)
        if band is None:
            continue

        # Their last message before the anchor gives the prompt builder context.
        earlier_inbound = [m for m in messages
                           if not m.get("is_from_user") and not is_auto_reply(m)]
        their_last = earlier_inbound[-1] if earlier_inbound else None
        forwarded = any(m.get("is_from_user") and is_forward(m) for m in messages)

        results.append({
            "category": config.CATEGORY_AWAITING_REPLY,
            "entry_id": anchor["entry_id"],
            "reply_entry_id": messages[-1]["entry_id"],
            "conversation_id": key,
            "subject": anchor.get("subject") or "(no subject)",
            "counterparties": to_people,
            "counterparty_display": ", ".join(
                p["name"] or p["address"] for p in to_people),
            "participants": _format_people(_recipients(anchor)),
            "date": _iso(anchor_time),
            "date_display": _display_date(anchor_time),
            "days_waiting": days,
            "age_band": band,
            "attachments": anchor.get("attachments") or [],
            "has_attachments": bool(anchor.get("attachments")),
            "forwarded": forwarded,
            "priority": "normal",
            "my_last_message": anchor.get("body") or "",
            "their_last_message": (their_last or {}).get("body") or "",
            "thread_length": len(messages),
        })

    results.sort(key=lambda r: (-r["days_waiting"], r["subject"].lower()))
    return results


def group_by_age_band(items):
    """Ordered {band label: [items]} for the dashboard, empty bands included."""
    grouped = {label: [] for label, _, _ in config.AGE_BANDS}
    for item in items:
        grouped.setdefault(item["age_band"], []).append(item)
    return grouped


# ---------------------------------------------------------------------------
# Feature 2 — inbound emails the user has not answered
# ---------------------------------------------------------------------------

def find_overdue_inbound(inbox, sent, now, threads=None):
    """Inbound mail addressed to the user, unanswered past the threshold."""
    threads = threads if threads is not None else build_threads(sent, inbox)
    best_per_thread = {}

    for record in inbox:
        if record.get("is_from_user"):
            continue
        if is_auto_reply(record):
            continue
        if is_mailing_list(record):
            continue

        sender = record.get("sender_address") or ""
        if is_excluded_address(sender):
            continue

        # The user must be a To recipient — CC only does not count.
        to_addresses = {(p.get("address") or "").lower()
                        for p in (record.get("to") or [])}
        if not any(config.is_user(address) for address in to_addresses):
            continue

        received = timestamp_of(record)
        if received is None:
            continue
        days = calendar_days_since(received, now)
        if days < config.INBOUND_OVERDUE_DAYS:
            continue

        # Has the user replied into this thread since?
        key = thread_key(record)
        answered = any(
            m.get("is_from_user")
            and (timestamp_of(m) or received) > received
            for m in threads.get(key, [])
        )
        if answered:
            continue

        # One row per thread: keep the most recent qualifying message.
        existing = best_per_thread.get(key)
        if existing is None or received > timestamp_of(existing):
            best_per_thread[key] = record

    results = []
    for key, record in best_per_thread.items():
        received = timestamp_of(record)
        days = calendar_days_since(received, now)
        messages = threads.get(key, [record])
        mine = [m for m in messages if m.get("is_from_user")]

        results.append({
            "category": config.CATEGORY_OVERDUE_INBOUND,
            "entry_id": record["entry_id"],
            "reply_entry_id": record["entry_id"],
            "conversation_id": key,
            "subject": record.get("subject") or "(no subject)",
            "counterparties": [{"name": record.get("sender_name") or "",
                                "address": record.get("sender_address") or ""}],
            "counterparty_display": (record.get("sender_name")
                                     or record.get("sender_address") or ""),
            "participants": _format_people(
                [{"name": record.get("sender_name"),
                  "address": record.get("sender_address")}]
                + _recipients(record)),
            "date": _iso(received),
            "date_display": _display_date(received),
            "days_waiting": days,
            "age_band": age_band(days),
            "attachments": record.get("attachments") or [],
            "has_attachments": bool(record.get("attachments")),
            "priority": "high" if has_priority_marker(record) else "normal",
            "unread": bool(record.get("unread")),
            "my_last_message": (mine[-1].get("body") if mine else "") or "",
            "their_last_message": record.get("body") or "",
            "thread_length": len(messages),
        })

    # Highest priority first, then longest waiting.
    results.sort(key=lambda r: (r["priority"] != "high", -r["days_waiting"],
                                r["subject"].lower()))
    return results


# ---------------------------------------------------------------------------
# Shared output
# ---------------------------------------------------------------------------

def conversation_topics(sent, inbox):
    """Normalised topics of every known thread — used by calendar_reader."""
    topics = set()
    for record in list(sent) + list(inbox):
        topic = (record.get("conversation_topic") or record.get("subject") or "")
        if topic.strip():
            topics.add(topic.strip())
    return topics


def process(sent, inbox, now):
    """Run both features in one pass, sharing the thread index."""
    threads = build_threads(sent, inbox)
    return {
        config.CATEGORY_AWAITING_REPLY: find_awaiting_reply(
            sent, inbox, now, threads=threads),
        config.CATEGORY_OVERDUE_INBOUND: find_overdue_inbound(
            inbox, sent, now, threads=threads),
    }
