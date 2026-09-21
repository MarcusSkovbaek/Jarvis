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
]

PROMPT_TYPE_FOLLOWUP = "followup"


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
# Registry — future prompt types are added here, not by editing the above.
# ---------------------------------------------------------------------------

PROMPT_TYPES = {
    PROMPT_TYPE_FOLLOWUP: {
        "label": "Follow-up",
        "version": config.PROMPT_VERSION,
        "builder": build_followup_prompt,
    },
}


def build(item, prompt_type=PROMPT_TYPE_FOLLOWUP):
    """Build a prompt of the requested type for a dashboard row."""
    spec = PROMPT_TYPES.get(prompt_type)
    if spec is None:
        raise ValueError(f"Unknown prompt type: {prompt_type!r}")
    return spec["builder"](item)


def prompt_version(prompt_type=PROMPT_TYPE_FOLLOWUP):
    return PROMPT_TYPES[prompt_type]["version"]


def available_types():
    return [{"type": key, "label": spec["label"], "version": spec["version"]}
            for key, spec in PROMPT_TYPES.items()]
