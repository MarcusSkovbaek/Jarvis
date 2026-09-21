"""
response_renderer.py — parses PrivateGPT responses and owns the response card.

The card layout is defined here and in templates/response_card.html. No other
module may modify either file. Future AI response features extend this
renderer rather than replacing it.

Parsing is strict by design. The prompt tells PrivateGPT exactly which four
headings to use; if they are missing or malformed the user is told so and
nothing is guessed or reformatted.
"""

import re
from datetime import datetime, timezone

PARSE_ERROR_MESSAGE = (
    "Response format does not match the expected structure. "
    "Please check the PrivateGPT output and try again."
)

# The four headings, in the order the prompt specifies them.
HEADINGS = ["SITUATION", "ACTION", "DRAFT REPLY", "URGENCY"]

FIELD_NAMES = {
    "SITUATION": "situation",
    "ACTION": "action",
    "DRAFT REPLY": "draft_reply",
    "URGENCY": "urgency",
}

VALID_ACTIONS = ["reply", "call", "escalate", "close"]
VALID_URGENCIES = ["low", "medium", "high"]

NOT_APPLICABLE = {"n/a", "na", "none", "-", ""}

# A heading at the start of a line. Models routinely bold the heading, and the
# markers can fall either side of the colon ("**ACTION:**" or "**ACTION**:"),
# so both are consumed and never leak into the value.
_HEADING_RE = re.compile(
    r"^[ \t]*[*_#>\-]*[ \t]*(" + "|".join(re.escape(h) for h in HEADINGS) +
    r")[ \t]*[*_]*[ \t]*:[ \t]*[*_]*",
    re.IGNORECASE | re.MULTILINE,
)

# "High - because X" / "High — because X" / "High: because X"
_URGENCY_RE = re.compile(
    r"^\s*[*_]*\s*(low|medium|high)\s*[*_]*\s*(?:[-–—:]\s*(.*))?$",
    re.IGNORECASE | re.DOTALL,
)


def _clean_value(text):
    """Strip markdown emphasis and the template's own [ ] placeholder brackets."""
    text = (text or "").strip().strip("*_").strip()
    if len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        text = text[1:-1].strip()
    return text


def parse(raw_response):
    """Parse a PrivateGPT response.

    Returns {"ok": True, "fields": {...}, "raw": ...} on success, or
    {"ok": False, "error": PARSE_ERROR_MESSAGE, "missing": [...], "raw": ...}.
    """
    raw = (raw_response or "").replace("\r\n", "\n").strip()
    if not raw:
        return _failure(raw, HEADINGS, "The response was empty.")

    matches = list(_HEADING_RE.finditer(raw))
    found = {}
    for index, match in enumerate(matches):
        heading = match.group(1).upper()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        # Later duplicates are ignored; the first occurrence wins.
        found.setdefault(heading, raw[start:end].strip())

    missing = [heading for heading in HEADINGS if heading not in found]
    if missing:
        return _failure(raw, missing,
                        "Missing heading(s): " + ", ".join(missing))

    situation = _clean_value(found["SITUATION"])
    action_raw = _clean_value(found["ACTION"])
    draft_raw = _clean_value(found["DRAFT REPLY"])
    urgency_raw = _clean_value(found["URGENCY"])

    if not situation:
        return _failure(raw, ["SITUATION"], "SITUATION was empty.")

    action = _normalise_action(action_raw)
    if action is None:
        return _failure(raw, ["ACTION"],
                        f"ACTION must be one of {', '.join(VALID_ACTIONS)} "
                        f"(got {action_raw!r}).")

    urgency_match = _URGENCY_RE.match(urgency_raw)
    if not urgency_match:
        return _failure(raw, ["URGENCY"],
                        f"URGENCY must be Low, Medium or High with a one-line "
                        f"reason (got {urgency_raw!r}).")
    urgency = urgency_match.group(1).capitalize()
    urgency_reason = (urgency_match.group(2) or "").strip()

    draft_reply = "" if draft_raw.strip().lower() in NOT_APPLICABLE else draft_raw

    return {
        "ok": True,
        "raw": raw,
        "fields": {
            "situation": situation,
            "action": action,
            "action_label": action.capitalize(),
            "draft_reply": draft_reply,
            "has_draft": bool(draft_reply),
            "urgency": urgency,
            "urgency_reason": urgency_reason,
        },
    }


def _normalise_action(text):
    candidate = text.strip().lower().rstrip(".")
    if candidate in VALID_ACTIONS:
        return candidate
    # Accept "Reply to Lars" / "escalate to the PM" — the leading verb decides.
    first = candidate.split()[0] if candidate.split() else ""
    return first if first in VALID_ACTIONS else None


def _failure(raw, missing, detail):
    return {
        "ok": False,
        "raw": raw,
        "error": PARSE_ERROR_MESSAGE,
        "detail": detail,
        "missing": list(missing),
        "fields": None,
    }


# ---------------------------------------------------------------------------
# Card data — the single source of truth for what the card displays.
# templates/response_card.html renders exactly these keys.
# ---------------------------------------------------------------------------

URGENCY_ORDER = {"High": 3, "Medium": 2, "Low": 1}


def _display_timestamp(value):
    """ISO timestamp -> local, human-readable. Unparseable values pass through."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return str(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%d %b %Y, %H:%M")


def build_card(item, parsed, saved_at=None):
    """Assemble the fixed response-card payload for one thread."""
    fields = parsed["fields"] if parsed.get("ok") else {}
    return {
        "subject": item.get("subject") or "(no subject)",
        "days_waiting": item.get("days_waiting", 0),
        "entry_id": item.get("entry_id") or "",
        "reply_entry_id": item.get("reply_entry_id") or item.get("entry_id") or "",
        "situation": fields.get("situation", ""),
        "action": fields.get("action", ""),
        "action_label": fields.get("action_label", ""),
        "draft_reply": fields.get("draft_reply", ""),
        "has_draft": bool(fields.get("draft_reply")),
        "urgency": fields.get("urgency", ""),
        "urgency_reason": fields.get("urgency_reason", ""),
        "urgency_rank": URGENCY_ORDER.get(fields.get("urgency", ""), 0),
        "saved_at": saved_at,
        "saved_at_display": _display_timestamp(saved_at),
    }


def card_from_record(item, record):
    """Rebuild a card from a saved ai_responses row (page reload)."""
    if not record:
        return None
    parsed = {
        "ok": True,
        "fields": {
            "situation": record.get("situation") or "",
            "action": record.get("action") or "",
            "action_label": (record.get("action") or "").capitalize(),
            "draft_reply": record.get("draft_reply") or "",
            "has_draft": bool(record.get("draft_reply")),
            "urgency": record.get("urgency") or "",
            "urgency_reason": record.get("urgency_reason") or "",
        },
    }
    return build_card(item, parsed, saved_at=record.get("created_at"))


def render_text_card(card):
    """Plain-text rendering of the card, used for logs and verification."""
    width = 62
    def row(text=""):
        return "| " + text[:width - 4].ljust(width - 4) + " |"

    lines = [
        "+" + "-" * (width - 2) + "+",
        row(f"SUBJECT: {card['subject']}"),
        row(f"DAYS WAITING: {card['days_waiting']}"),
        "+" + "-" * (width - 2) + "+",
        row("SITUATION"),
    ]
    for line in _wrap(card["situation"], width - 4):
        lines.append(row(line))
    lines.append("+" + "-" * (width - 2) + "+")
    lines.append(row(f"ACTION: {card['action_label']}"))
    lines.append(row(f"URGENCY: {card['urgency']} - {card['urgency_reason']}"))
    lines.append("+" + "-" * (width - 2) + "+")
    lines.append(row("DRAFT REPLY"))
    for line in _wrap(card["draft_reply"] or "N/A", width - 4):
        lines.append(row(line))
    lines.append("+" + "-" * (width - 2) + "+")
    return "\n".join(lines)


def _wrap(text, width):
    out = []
    for paragraph in (text or "").split("\n"):
        words, line = paragraph.split(), ""
        if not words:
            out.append("")
            continue
        for word in words:
            if len(line) + len(word) + 1 > width:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        out.append(line)
    return out
