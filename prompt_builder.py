"""
prompt_builder.py — generates the PrivateGPT prompt.

This is the only place a prompt template lives. The template is versioned:
config.PROMPT_VERSION must be incremented on ANY change to the text below, and
the change recorded in PROMPT_CHANGELOG.

The prompt is fully self-contained plain text — no references to files, no
links, nothing the user would have to supply separately. It is copied to the
clipboard by the browser and pasted into PrivateGPT by hand. Nothing in this
module ever contacts PrivateGPT or any other service.

Future prompt types ("meeting-prep", "task-delegation", ...) register
themselves in PROMPT_TYPES with their own builder and their own version, and
are rendered by the same response_renderer.
"""

import config

# Every template change gets a line here. Newest last.
PROMPT_CHANGELOG = [
    (1, "2026-09-19", "followup", "Initial template as specified in the build brief."),
    (1, "2026-09-23", "dashboard",
     "Initial template: whole-overview analysis returned as a self-contained, "
     "network-locked HTML page."),
]

PROMPT_TYPE_FOLLOWUP = "followup"
PROMPT_TYPE_DASHBOARD = "dashboard"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def truncate_words(text, limit=None):
    """Cut a body down to `limit` words, marking that it was truncated."""
    limit = config.PROMPT_BODY_TRUNCATE_WORDS if limit is None else limit
    words = (text or "").split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]) + f" [... truncated at {limit} words]"


def _clean(text):
    """Collapse the blank-line runs Outlook bodies are full of."""
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").split("\n")]
    out, blank = [], False
    for line in lines:
        if not line.strip():
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(line)
    return "\n".join(out).strip()


def format_participants(item):
    """'Name <address>' for everyone on the thread, the user included.

    The user's own address comes from config, unless the row carries a
    "user_email" of its own. Only the redacted probe sets that, so a real
    prompt is unaffected; without it the probe would print the one address
    redaction is most obliged to hide.
    """
    people = list(item.get("participants") or [])
    if not people:
        people = [
            f"{p.get('name') or p.get('address')} <{p.get('address')}>"
            for p in item.get("counterparties") or []
        ]
    people.append(f"Me <{item.get('user_email') or config.USER_EMAIL}>")
    seen, unique = set(), []
    for person in people:
        key = person.lower()
        if key not in seen:
            seen.add(key)
            unique.append(person)
    return ", ".join(unique) if unique else "(unknown)"


def format_attachments(item):
    names = item.get("attachments") or []
    return ", ".join(names) if names else "None"


# ---------------------------------------------------------------------------
# Template v1 — followup
#
# This text is reproduced character for character from the build brief, and
# tests/verify_prompt_conformance.py asserts that byte-for-byte.
#
# The two dashes are written as – (en dash, "3-5 sentences") and —
# (em dash, on the URGENCY line) rather than as literal characters, so the
# template cannot be silently corrupted by a source-encoding mismatch when the
# .exe is built on another machine.
#
# WARNING: changing any line below requires incrementing config.PROMPT_VERSION
# and adding an entry to PROMPT_CHANGELOG.
# ---------------------------------------------------------------------------

_FOLLOWUP_TEMPLATE = """=== PRIVATEGPT PROMPT (v{version}) ===

You are a professional assistant helping to manage email
follow-ups. Below is a thread context. Based only on the
information provided, suggest a concise follow-up action.
Format your response exactly as specified at the end of
this prompt.

--- THREAD CONTEXT ---
Subject: {subject}
Participants: {participants}
Original sent date: {sent_date}
Days without reply: {days}
Attachments: {attachments}
My last message: {my_last_message}
Their last message (if any): {their_last_message}

--- YOUR TASK ---
1. Summarise the situation in 2 sentences
2. Suggest a follow-up action: reply, call, escalate,
   or close
3. If a reply is suggested, draft it (3–5 sentences,
   professional tone, no fluff)
4. Rate urgency: Low / Medium / High with one-line reason

--- REQUIRED RESPONSE FORMAT ---
Respond using exactly this structure with these exact
headings. Do not add any text outside this structure:

SITUATION: [2 sentence summary]
ACTION: [reply / call / escalate / close]
DRAFT REPLY: [draft text, or "N/A" if action is not reply]
URGENCY: [Low / Medium / High] — [one line reason]

=== END OF PROMPT ==="""

NO_REPLY_PLACEHOLDER = "No reply received"


def build_followup_prompt(item):
    """Build the v1 follow-up prompt for a dashboard row."""
    their_message = _clean(item.get("their_last_message") or "")
    return _FOLLOWUP_TEMPLATE.format(
        version=config.PROMPT_VERSION,
        subject=item.get("subject") or "(no subject)",
        participants=format_participants(item),
        sent_date=item.get("date_display") or item.get("date") or "(unknown)",
        days=item.get("days_waiting", 0),
        attachments=format_attachments(item),
        my_last_message=truncate_words(_clean(item.get("my_last_message") or ""))
        or "(no message body available)",
        their_last_message=truncate_words(their_message) if their_message
        else NO_REPLY_PLACEHOLDER,
    )


# ---------------------------------------------------------------------------
# Template v1 — dashboard
#
# One prompt covering everything on screen. PrivateGPT is asked to do the
# work that genuinely needs a language model — summarising threads, grouping
# them into themes, suggesting actions and drafting replies — and to return
# the result as a single HTML file the user saves and opens locally.
#
# The Content-Security-Policy line is the safety mechanism, not a nicety.
# With default-src 'none' the browser itself refuses every network request
# the page might make: no web fonts, no CDN scripts, no images by URL, no
# fetch(). So even if the model ignores the "no external resources" rule, the
# file it produced cannot contact anything. CSP_LINE is exported so the tests
# and the UI can check for exactly this string.
#
# WARNING: changing any line below requires incrementing
# config.DASHBOARD_PROMPT_VERSION and adding an entry to PROMPT_CHANGELOG.
# ---------------------------------------------------------------------------

CSP_LINE = ('<meta http-equiv="Content-Security-Policy" '
            'content="default-src \'none\'; style-src \'unsafe-inline\'; '
            'script-src \'unsafe-inline\'; img-src data:; '
            'base-uri \'none\'; form-action \'none\'">')

_DASHBOARD_TEMPLATE = """=== PRIVATEGPT DASHBOARD PROMPT (v{version}) ===

You are a professional assistant helping me manage my email
follow-ups and meetings. Below is everything currently on my
dashboard. Analyse it, then build me a single self-contained
HTML page I can open on my computer and work in today.

Base everything only on the data provided. Do not invent
people, dates, commitments or facts. Keep every subject line,
name and "days" figure exactly as given. If something cannot
be determined from the data, say so rather than guessing.

--- WHAT TO WORK OUT ---
1. A briefing of 3-5 sentences: what needs my attention most
   today and why.
2. My top 3 focus items for today, each with a one-line reason.
3. For every email item: a one-sentence summary of the
   thread, a suggested action (reply / call / escalate /
   close / wait), and an urgency (Low / Medium / High) with a
   one-line reason. Where the action is reply, a draft reply
   of 3-5 sentences in a professional tone, no fluff.
4. Group the email items into themes or projects, based on
   their subjects and content.
5. For every meeting flagged "not yet accepted or declined"
   or "no preparation found": a short preparation checklist
   of 2-4 points.

--- HOW TO BUILD THE PAGE ---
Reply with ONE code block containing the complete HTML
document, from <!DOCTYPE html> to </html>, and nothing before
or after it.

The very first element inside <head> must be exactly this
line, character for character:
{csp_line}

Hard rules - the page must work fully offline:
- No external resources of any kind: no links to fonts,
  stylesheets, scripts, images or frameworks. No CDN.
- All CSS in one <style> element, all JavaScript in one
  <script> element, plain JavaScript only.
- No fetch, XMLHttpRequest, WebSocket, forms that submit,
  or links that leave the page.
- Use the font stack "Segoe UI", system-ui, sans-serif.

Layout and style - a dark, calm dashboard:
- Background #0b0d17, cards #141827 with a 1px #232a40
  border and 16px rounded corners, body text #e6e8f2, muted
  text #8b90a8, accents #7c6cf6 (violet) and #4c8dff (blue).
  High urgency #f0647a, medium #f5b454, low #4cc38a.
- A header with a greeting, today's date and the briefing.
- A "Today's focus" card with the top 3 items and a circular
  progress ring showing the percentage of items marked done.
- One card per theme, listing its email items. Each item
  shows its ID (such as A1), subject, the other person,
  days waiting, summary, suggested action and an urgency
  badge. Drafts sit in a collapsible section with a
  "Copy draft" button.
- A meetings card in time order, with the preparation
  checklists where they apply.

Make it something I can work in:
- A checkbox on every item to mark it done; done items are
  dimmed with a strike-through and the progress ring updates.
- Filter buttons: All / Needs my reply / Waiting on others /
  Meetings, plus a search box that filters by any text.
- "Copy draft" copies the draft to the clipboard using
  navigator.clipboard, falling back to selecting the text.
- Save the checkbox state in localStorage under the key
  "jarvis-{date_key}", so it survives a reload today.

--- DASHBOARD DATA ---
Generated: {generated}
Me: {me}

[A] AWAITING MY REPLY - sent to me, unanswered ({count_a})
{section_a}

[N] NO RESPONSE RECEIVED - I wrote last, no reply yet ({count_n})
{section_n}

[M] UPCOMING MEETINGS ({count_m})
{section_m}

=== END OF PROMPT ==="""


def _person(item):
    people = item.get("counterparties") or []
    if people:
        first = people[0]
        name = first.get("name") or first.get("address") or "(unknown)"
        address = first.get("address")
        label = f"{name} <{address}>" if address and address != name else name
        if len(people) > 1:
            label += f" (+{len(people) - 1} more)"
        return label
    return item.get("counterparty_display") or "(unknown)"


def _snippet(text):
    cleaned = _clean(text or "")
    if not cleaned:
        return "(none)"
    return truncate_words(cleaned, config.DASHBOARD_PROMPT_SNIPPET_WORDS)


def _email_section(prefix, items):
    limit = config.DASHBOARD_PROMPT_MAX_ITEMS
    if not items:
        return "(none)"
    blocks = []
    for index, item in enumerate(items[:limit], start=1):
        lines = [
            f"{prefix}{index}. {item.get('subject') or '(no subject)'}",
            f"   With: {_person(item)}",
            f"   Days waiting: {item.get('days_waiting', 0)}"
            f" (since {item.get('date_display') or item.get('date') or 'unknown'})",
        ]
        if item.get("priority") == "high":
            lines.append("   Flag: contains a direct question")
        if item.get("forwarded"):
            lines.append("   Note: I forwarded this thread; the original "
                         "recipients have still not replied")
        lines.append(f"   Attachments: {format_attachments(item)}")
        lines.append(f"   Their last message: {_snippet(item.get('their_last_message'))}")
        lines.append(f"   My last message: {_snippet(item.get('my_last_message'))}")
        blocks.append("\n".join(lines))
    omitted = len(items) - limit
    if omitted > 0:
        blocks.append(f"({omitted} older item(s) omitted to keep this prompt "
                      f"a manageable length)")
    return "\n\n".join(blocks)


_MEETING_FLAGS = {
    "pending": "not yet accepted or declined",
    "unprepared": "no preparation found (no notes, no related email)",
}


def _meeting_section(meetings):
    limit = config.DASHBOARD_PROMPT_MAX_ITEMS
    if not meetings:
        return "(none)"
    blocks = []
    for index, meeting in enumerate(meetings[:limit], start=1):
        lines = [
            f"M{index}. {meeting.get('subject') or '(no subject)'}",
            f"   When: {meeting.get('date_display') or meeting.get('date')}"
            f" ({meeting.get('duration_display') or '?'})",
            f"   Organiser: {meeting.get('organizer') or '(unknown)'}",
            f"   Attendees: {meeting.get('attendee_count', 0)}",
            f"   My response: {meeting.get('response_label') or '(unknown)'}",
        ]
        if meeting.get("location"):
            lines.append(f"   Location: {meeting['location']}")
        flags = [_MEETING_FLAGS.get(f, f) for f in meeting.get("flags") or []]
        if flags:
            lines.append(f"   Flags: {'; '.join(flags)}")
        blocks.append("\n".join(lines))
    omitted = len(meetings) - limit
    if omitted > 0:
        blocks.append(f"({omitted} later meeting(s) omitted)")
    return "\n\n".join(blocks)


def build_dashboard_prompt(view, now=None):
    """Build the whole-dashboard prompt from dashboard.build_view() output.

    Takes the view rather than a row, because it covers every section at
    once. Rows appear in the order the dashboard shows them.
    """
    from datetime import datetime

    now = now or view.get("now") or datetime.now()
    local = now.astimezone() if getattr(now, "tzinfo", None) else now
    overdue = list(view.get("overdue_inbound") or [])
    awaiting = list(view.get("awaiting_reply") or [])
    meetings = list(view.get("meetings") or [])
    return _DASHBOARD_TEMPLATE.format(
        version=config.DASHBOARD_PROMPT_VERSION,
        csp_line=CSP_LINE,
        date_key=local.strftime("%Y-%m-%d"),
        generated=local.strftime("%A %d %B %Y, %H:%M"),
        me=config.USER_EMAIL,
        count_a=len(overdue),
        section_a=_email_section("A", overdue),
        count_n=len(awaiting),
        section_n=_email_section("N", awaiting),
        count_m=len(meetings),
        section_m=_meeting_section(meetings),
    )


# ---------------------------------------------------------------------------
# Registry — future prompt types are added here, not by editing the above.
#
# "scope" says what a builder is given: "row" types take one dashboard row,
# "view" types take the whole dashboard view.
# ---------------------------------------------------------------------------

PROMPT_TYPES = {
    PROMPT_TYPE_FOLLOWUP: {
        "label": "Follow-up",
        "version": config.PROMPT_VERSION,
        "scope": "row",
        "builder": build_followup_prompt,
    },
    PROMPT_TYPE_DASHBOARD: {
        "label": "Dashboard",
        "version": config.DASHBOARD_PROMPT_VERSION,
        "scope": "view",
        "builder": build_dashboard_prompt,
    },
}


def build(item, prompt_type=PROMPT_TYPE_FOLLOWUP):
    """Build a prompt of the requested type for a dashboard row."""
    spec = PROMPT_TYPES.get(prompt_type)
    if spec is None:
        raise ValueError(f"Unknown prompt type: {prompt_type!r}")
    if spec["scope"] != "row":
        raise ValueError(f"The {prompt_type!r} prompt covers the whole "
                         f"dashboard, not one row")
    return spec["builder"](item)


def prompt_version(prompt_type=PROMPT_TYPE_FOLLOWUP):
    return PROMPT_TYPES[prompt_type]["version"]


def available_types():
    return [{"type": key, "label": spec["label"], "version": spec["version"],
             "scope": spec["scope"]}
            for key, spec in PROMPT_TYPES.items()]
