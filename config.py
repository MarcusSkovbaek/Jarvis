"""
config.py — single source of truth for every configurable value in Jarvis.

No other module may hardcode thresholds, paths, folder names, domains or
prefixes. If a value could conceivably change, it lives here.
"""

import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# BASE_DIR resolves correctly both when running from source and when frozen
# by PyInstaller (sys.frozen). Kept as a function-free constant for simplicity;
# main.py adjusts sys.path, not this value.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# When frozen, write data next to the .exe rather than inside the temp bundle.
import sys  # noqa: E402  (deliberate: needed only for the frozen check below)

if getattr(sys, "frozen", False):
    DATA_DIR = os.path.dirname(sys.executable)
else:
    DATA_DIR = BASE_DIR

DB_PATH = os.path.join(DATA_DIR, "jarvis.db")
LOG_PATH = os.path.join(DATA_DIR, "sync.log")

# A short id for this run, logged in the startup banner and exposed at
# /healthz. It makes a log file traceable to a specific run, and lets tooling
# confirm which instance is answering when several have been started. Note
# that a PyInstaller one-file .exe runs the app in a CHILD of the launched
# process, so the OS process id is not a reliable identifier from outside.
import uuid  # noqa: E402

INSTANCE_ID = os.environ.get("JARVIS_INSTANCE_ID") or uuid.uuid4().hex[:12]

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

# Every "sent by me" / "replied by me" test matches exclusively against this.
USER_EMAIL = "mskovbaek@cedra.dk"

# Matching is case-insensitive everywhere; helper kept here so no module
# re-implements the comparison.
def is_user(address):
    """True if the given SMTP address is the Jarvis user."""
    if not address:
        return False
    return address.strip().lower() == USER_EMAIL.strip().lower()


# ---------------------------------------------------------------------------
# Outlook backend
# ---------------------------------------------------------------------------

# "mock" -> mock_outlook.py (Phase 1, no mailbox needed)
# "com"  -> live win32com connection (Phase 2, work PC only)
# Overridable at runtime without editing this file:
#     $env:JARVIS_OUTLOOK_BACKEND = "com"
OUTLOOK_BACKEND = os.environ.get("JARVIS_OUTLOOK_BACKEND", "mock").strip().lower()

# Outlook default-folder constants (olFolderInbox / olFolderSentMail /
# olFolderCalendar). Named here so outlook_reader.py contains no magic numbers.
OL_FOLDER_INBOX = 6
OL_FOLDER_SENT = 5
OL_FOLDER_CALENDAR = 9

# Recipient types (olTo / olCC / olBCC).
OL_TO = 1
OL_CC = 2
OL_BCC = 3

# Meeting response status (olResponseStatus).
OL_RESPONSE_NONE = 0
OL_RESPONSE_ORGANIZED = 1
OL_RESPONSE_TENTATIVE = 2
OL_RESPONSE_ACCEPTED = 3
OL_RESPONSE_DECLINED = 4
OL_RESPONSE_NOT_RESPONDED = 5

RESPONSE_STATUS_LABELS = {
    OL_RESPONSE_NONE: "No response required",
    OL_RESPONSE_ORGANIZED: "Organiser",
    OL_RESPONSE_TENTATIVE: "Tentative",
    OL_RESPONSE_ACCEPTED: "Accepted",
    OL_RESPONSE_DECLINED: "Declined",
    OL_RESPONSE_NOT_RESPONDED: "Not responded",
}

# Statuses that count as "you have not yet accepted or declined".
PENDING_RESPONSE_STATUSES = [OL_RESPONSE_NOT_RESPONDED, OL_RESPONSE_TENTATIVE]

# MAPI property tags read via PropertyAccessor.
PR_TRANSPORT_MESSAGE_HEADERS = "http://schemas.microsoft.com/mapi/proptag/0x007D001F"
PR_SMTP_ADDRESS = "http://schemas.microsoft.com/mapi/proptag/0x39FE001F"
PR_SENDER_SMTP_ADDRESS = "http://schemas.microsoft.com/mapi/proptag/0x5D01001F"

# Main Inbox only — no subfolder recursion (decision #6).
INCLUDE_INBOX_SUBFOLDERS = False

# How far back to read each folder. Bounds the COM query so Outlook is never
# asked to walk an entire multi-year mailbox.
SENT_LOOKBACK_DAYS = 120
INBOX_LOOKBACK_DAYS = 90

# ---------------------------------------------------------------------------
# Thresholds (all in CALENDAR days — weekends included, decision #2)
# ---------------------------------------------------------------------------

# Feature 1 — a sent thread is flagged after this many days with no reply.
SENT_AWAITING_REPLY_DAYS = 7

# Feature 2 — an inbound email is flagged after this many days unanswered.
INBOUND_OVERDUE_DAYS = 2

# Feature 1 age bands, as (label, min_days_inclusive, max_days_exclusive).
# None as the upper bound means "no upper limit".
AGE_BANDS = [
    ("7-14 days", 7, 14),
    ("14-30 days", 14, 30),
    ("30+ days", 30, None),
]

# Feature 3 — calendar window and preparation warning.
CALENDAR_LOOKAHEAD_DAYS = 7
MEETING_PREP_WARNING_HOURS = 24

# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------

# Threads to/from these domains are never flagged. Compared case-insensitively
# against the part after "@". Subdomains are matched by suffix, so "example.com"
# also excludes "mail.example.com".
EXCLUDED_DOMAINS = [
    "cedra.dk",          # internal noreply/system senders; see note below
    "noreply.com",
    "notifications.com",
]

# Addresses that override EXCLUDED_DOMAINS — colleagues on an excluded domain
# whose threads you DO want tracked. Internal mail to cedra.dk is excluded by
# default above; move addresses here (or remove "cedra.dk" from the list) once
# you decide how you want internal threads treated.
EXCLUDED_DOMAIN_EXCEPTIONS = []

# Local-parts that are never worth chasing, on any domain.
EXCLUDED_LOCAL_PARTS = [
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "postmaster",
    "bounce",
    "notifications",
    "automated",
]

# ---------------------------------------------------------------------------
# Auto-reply detection (decision #4 — auto-replies never count as replies)
# ---------------------------------------------------------------------------

# Matched as a case-insensitive substring of PR_MESSAGE_CLASS.
AUTO_REPLY_MESSAGE_CLASSES = [
    "IPM.Note.Rules",
    "IPM.Note.Rules.OofTemplate",
    "IPM.Note.Rules.ReplyTemplate",
]

# Matched case-insensitively against the START of the subject.
AUTO_REPLY_SUBJECT_PREFIXES = [
    "automatic reply:",
    "autosvar:",
    "auto:",
    "out of office:",
    "out of office autoreply:",
    "ooo:",
    "automatisk svar:",
    "abwesenheitsnotiz:",
    "réponse automatique:",
    "undelivered mail returned to sender",
    "delivery status notification",
]

# Presence of any of these headers marks the message as automated.
AUTO_REPLY_HEADERS = [
    "x-auto-response-suppress",
    "auto-submitted",
    "x-autoreply",
    "x-autorespond",
]

# Values of Auto-Submitted that do NOT indicate an auto-reply.
AUTO_SUBMITTED_HUMAN_VALUES = ["no"]

# ---------------------------------------------------------------------------
# Mailing list / newsletter detection
# ---------------------------------------------------------------------------

MAILING_LIST_HEADERS = [
    "list-unsubscribe",
    "list-id",
    "list-post",
    "precedence",  # value checked against MAILING_LIST_PRECEDENCE_VALUES
]

MAILING_LIST_PRECEDENCE_VALUES = ["bulk", "list", "junk"]

# Case-insensitive substring match against the sender's SMTP address.
MAILING_LIST_SENDER_PATTERNS = [
    "newsletter",
    "news@",
    "marketing@",
    "info@",
    "updates@",
    "digest@",
    "campaign",
    "mailchimp",
    "sendgrid",
    "hubspot",
]

# ---------------------------------------------------------------------------
# Subject prefixes — used to classify a message within its thread
# ---------------------------------------------------------------------------

# A message from the user carrying one of these is a FORWARD. Decision #3:
# forwarding does not resolve a thread. A forward also does not reset the
# clock — the days-waiting count still runs from the last real send to the
# original recipients.
FORWARD_SUBJECT_PREFIXES = ["fw:", "fwd:", "vs:", "wg:", "tr:", "rv:"]

REPLY_SUBJECT_PREFIXES = ["re:", "sv:", "aw:", "ref:", "antw:"]

# ---------------------------------------------------------------------------
# Reply resolution
# ---------------------------------------------------------------------------

# Feature 1 states a thread is flagged only when no message exists after the
# sent date "from any address the email was sent To or CC'd to" — so a reply
# from a CC'd recipient resolves the thread. Set to False to require a reply
# from a To recipient specifically, leaving CC-only replies flagged.
CC_REPLY_RESOLVES_THREAD = True

# ---------------------------------------------------------------------------
# Meeting preparation heuristic (Feature 3)
# ---------------------------------------------------------------------------

# A meeting within MEETING_PREP_WARNING_HOURS is flagged as unprepared unless
# it has notes in its body, or its subject shares at least this many
# significant words with a known mail conversation topic.
MEETING_PREP_MIN_SHARED_WORDS = 2
MEETING_PREP_MIN_NOTE_CHARS = 40

MEETING_PREP_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "your", "our", "you",
    "are", "was", "will", "have", "has", "not", "but", "all", "can", "about",
    "into", "over", "re", "fw", "fwd", "sv", "vs", "meeting", "call", "sync",
    "update", "updates", "weekly", "monthly", "quick", "new", "på", "og",
    "til", "for", "med", "den", "det", "der", "som", "har", "kan", "vedr",
}

# ---------------------------------------------------------------------------
# Priority hints
# ---------------------------------------------------------------------------

# Feature 2 — inbound mail containing any of these is raised in priority.
PRIORITY_MARKERS = ["?"]

# Only the first N characters of a body are scanned for priority markers, so a
# long quoted history does not make every mail "a question".
PRIORITY_BODY_SCAN_CHARS = 2000

# ---------------------------------------------------------------------------
# Dashboard actions and retention
# ---------------------------------------------------------------------------

SNOOZE_DAYS = 3
RETENTION_DAYS = 30          # auto-purge dismissed/snoozed records older than this
KEEP_AI_RESPONSES = True     # AI responses kept indefinitely unless cleared manually

# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

SYNC_INTERVAL_MINUTES = 15
SYNC_ON_STARTUP = True
SYNC_LOG_MAX_BYTES = 2 * 1024 * 1024
SYNC_LOG_BACKUP_COUNT = 3

# ---------------------------------------------------------------------------
# PrivateGPT prompt
# ---------------------------------------------------------------------------

# Increment on ANY change to a prompt template in prompt_builder.py.
# Changelog lives in prompt_builder.PROMPT_CHANGELOG.
PROMPT_VERSION = 1

# Message bodies are truncated to this many words inside the prompt.
PROMPT_BODY_TRUNCATE_WORDS = 500

# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------

FLASK_HOST = "127.0.0.1"   # loopback only — never exposed to the network
FLASK_PORT = 5000
FLASK_DEBUG = False

# Browser auto-refresh interval, mirrors the sync cycle.
DASHBOARD_REFRESH_SECONDS = SYNC_INTERVAL_MINUTES * 60

API_PREFIX = "/api/v1"

# ---------------------------------------------------------------------------
# Categories — used as keys in SQLite and in the API. Adding a future category
# (tasks, excel projects) means adding it here, not changing db.py.
# ---------------------------------------------------------------------------

CATEGORY_AWAITING_REPLY = "awaiting_reply"     # Feature 1: sent, no response
CATEGORY_OVERDUE_INBOUND = "overdue_inbound"   # Feature 2: inbound, unanswered
CATEGORY_MEETING = "meeting"                   # Feature 3: calendar

CATEGORIES = [
    CATEGORY_OVERDUE_INBOUND,
    CATEGORY_AWAITING_REPLY,
    CATEGORY_MEETING,
]

# Human-readable section titles for the dashboard.
CATEGORY_TITLES = {
    CATEGORY_OVERDUE_INBOUND: "Awaiting your reply",
    CATEGORY_AWAITING_REPLY: "No response received",
    CATEGORY_MEETING: "Upcoming meetings",
}


def public_settings():
    """Config values safe to display in the dashboard settings panel."""
    return {
        "USER_EMAIL": USER_EMAIL,
        "OUTLOOK_BACKEND": OUTLOOK_BACKEND,
        "SENT_AWAITING_REPLY_DAYS": SENT_AWAITING_REPLY_DAYS,
        "INBOUND_OVERDUE_DAYS": INBOUND_OVERDUE_DAYS,
        "AGE_BANDS": [band[0] for band in AGE_BANDS],
        "CALENDAR_LOOKAHEAD_DAYS": CALENDAR_LOOKAHEAD_DAYS,
        "MEETING_PREP_WARNING_HOURS": MEETING_PREP_WARNING_HOURS,
        "SNOOZE_DAYS": SNOOZE_DAYS,
        "RETENTION_DAYS": RETENTION_DAYS,
        "SYNC_INTERVAL_MINUTES": SYNC_INTERVAL_MINUTES,
        "PROMPT_VERSION": PROMPT_VERSION,
        "EXCLUDED_DOMAINS": EXCLUDED_DOMAINS,
        "INCLUDE_INBOX_SUBFOLDERS": INCLUDE_INBOX_SUBFOLDERS,
        "DB_PATH": DB_PATH,
        "LOG_PATH": LOG_PATH,
    }
