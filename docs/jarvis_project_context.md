# Jarvis Project — Full Context Document
*Load this into a new Claude Code session to continue the project with full context.*

---

## What this is

This document captures the full context of a planning conversation for building a local personal assistant dashboard called "Jarvis." It is designed to be pasted into a new Claude chat so the conversation can continue without losing any context or decisions already made.

---

## The person

- Works at Cedra, email: mskovbaek@cedra.dk
- Uses a Windows work PC
- Cannot install third-party apps on the work PC (IT policy) — this includes Claude Code
- Can run on the work PC: Python scripts, PowerShell scripts, self-compiled .exe files
- Has access to claude.ai in the browser
- Uses PrivateGPT (a company-approved browser-based AI tool) — browser UI only, no API access
- Microsoft Outlook is installed and running on the work PC
- May have Microsoft Teams with Excel-based task tracking in SharePoint/OneDrive
- Has a personal MacBook and can run a Windows VM on it. Claude Code CAN run
  inside that VM. The VM has no connection to the Cedra mailbox — it cannot
  sign in as mskovbaek@cedra.dk (Conditional Access / unmanaged device), so
  it cannot be used to test against real Outlook data. It is a genuine
  execution environment for everything else: running scripts, iterating,
  and self-verifying logic against realistic mock data.

---

## What we are building

A locally hosted personal assistant dashboard that:
- Runs entirely on the Windows work PC
- Connects to Outlook via win32com (Windows COM interface)
- Serves a web dashboard at http://localhost:5000 via Flask
- Stores all state in a local SQLite database
- Never sends data anywhere automatically
- Has one manual bridge to PrivateGPT: a "Copy prompt" button that
  copies a structured prompt to clipboard for the user to paste manually

---

## Key constraints

- Everything is local. No external API calls. No cloud. No telemetry.
- No third-party app installs. Python packages via pip are fine (no admin rights needed).
- PrivateGPT is the only AI system involved, and only via manual copy-paste.
- The app is read-only in Outlook — it never sends, deletes, or modifies anything.
- The only action it takes in Outlook is opening items (via EntryID) and
  pre-filling draft replies for the user to review and send manually.
- Will be distributed as a .exe compiled with PyInstaller so it runs
  without Python installed separately.

---

## Build and iteration workflow

Claude Code cannot be installed on the work PC (IT policy), but it CAN run
inside a Windows VM on the person's personal MacBook. That VM has no access
to the real Cedra mailbox. This splits the build into two phases:

**Phase 1 — VM (Claude Code, full execution, mock Outlook data)**
Claude Code runs directly in the VM with real execution access — no manual
copy-paste required. It builds a mock Outlook layer that simulates
realistic win32com objects (see "Mock Outlook layer" below) and uses it to
build, run, and self-verify every module that doesn't strictly require a
live mailbox connection: db.py, config.py, email_processor.py logic,
calendar_reader.py logic, prompt_builder.py, response_renderer.py, sync.py
mechanics, the Flask app and dashboard UI, snooze/dismiss/purge behavior,
and the PyInstaller build itself. Claude Code iterates against mock data
until the logic is provably correct, with no human round-trip needed for
this phase.

**Phase 2 — Work PC (manual, real Outlook data)**
Once Phase 1 is logic-complete, only the steps that genuinely require a
live win32com connection to the real mailbox remain: pulling real Sent
Items/Inbox/Calendar data, and opening real items in Outlook via EntryID.
For these, the person runs the compiled/scripted app on the actual work PC
and reports back real output — but by this point it should mostly be
confirming wiring against the real mailbox, not debugging logic, since
the logic was already proven against mocks in Phase 1.

Python virtual environment setup (no admin rights needed, same on the VM
or the work PC):
```powershell
python -m venv jarvis-env
jarvis-env\Scripts\activate
pip install flask pywin32
```

### Mock Outlook layer

Claude Code should build a `mock_outlook.py` (or a `fixtures/` module) early
in Phase 1 that stands in for win32com's Outlook objects closely enough
that `outlook_reader.py` and everything downstream can be exercised without
a real mailbox. It should cover realistic edge cases so the logic in
Features 1–3 actually gets tested, not just the happy path:
- Sent items with no reply, with a reply, with a reply only after
  forwarding (should still flag), with only a CC'd reply (should still
  flag), with a pure-BCC send (should be excluded)
- Auto-reply / out-of-office messages, detected via the same rules as real
  ones (message class, subject prefix, headers)
- Newsletters / mailing-list senders (List-Unsubscribe header)
- Inbound emails with and without a question mark, within and past the
  overdue threshold
- Emails with and without attachments, including multiple attachments
- Calendar items: accepted, declined, pending, and a meeting within 24
  hours with no associated thread
- Threads spanning the 7–14 / 14–30 / 30+ day age bands

The real `outlook_reader.py` should be structured so the mock can be
swapped in behind the same interface (e.g. dependency injection or a
config flag), so Phase 2 requires no code changes — only pointing it at
the real win32com connection.

---

## All decisions made

| # | Decision | Choice |
|---|----------|--------|
| 1 | Outlook identity | Single address: mskovbaek@cedra.dk |
| 2 | Day thresholds | Calendar days (includes weekends) |
| 3 | Forwarding a thread | Does NOT resolve it — stays flagged |
| 4 | Auto-replies | Do NOT count as replies (ignored) |
| 5 | Which threads to flag | Only where YOUR message is most recent with no response after it |
| 6 | Inbox scope | Main Inbox folder only (no subfolders) |
| 7 | PrivateGPT response input | Collapsible panel inline below the thread row |
| 8 | Draft reply in Outlook | Reply All |
| 9 | SQLite record cleanup | Auto-purge dismissed/snoozed items older than 30 days |
| 10 | Attachment handling | Show paperclip indicator on rows, include attachment names in PrivateGPT prompt |

---

## Module structure

```
main.py                 — entrypoint, starts Flask and sync loop
config.py               — all configuration values, never hardcoded elsewhere
outlook_reader.py       — Outlook COM connection and raw data pull
mock_outlook.py         — mock win32com layer for VM testing (no real mailbox needed)
email_processor.py      — overdue and awaiting-reply detection logic
calendar_reader.py      — calendar event reading and processing
prompt_builder.py       — PrivateGPT prompt generation and versioning
response_renderer.py    — parsing and rendering AI responses
db.py                   — all SQLite read/write operations
sync.py                 — background sync loop
templates/
  dashboard.html        — main dashboard UI
  response_card.html    — fixed AI response card layout (never modified by other modules)
static/                 — local CSS and JS, no CDN dependencies
sync.log                — sync run log
jarvis.db               — SQLite database
```

---

## The full build prompt

Paste this into Claude to start building:

---

```
You are building a local personal assistant dashboard for Windows.
It connects to Microsoft Outlook via win32com, reads emails, calendar
events, and tasks, and displays them in a locally hosted web dashboard.

ABSOLUTE CONSTRAINTS — these apply to every part of the build:
- Everything runs locally on this machine. No data leaves the machine.
- No external API calls of any kind — no AI APIs, no cloud services,
  no telemetry, no CDN dependencies. The app must work fully offline.
- The only external system that ever receives data is PrivateGPT,
  and only when the user explicitly clicks a "Copy prompt" button.
  This is always a manual, deliberate action — never automatic.
- PrivateGPT is treated as a read-only paste target. The app never
  connects to it programmatically. The user copies, pastes, gets a
  response, and the app displays that response in a fixed template.
- The app never sends, deletes, or modifies any email, calendar
  event, or task in Outlook. It is strictly read-only, except for
  opening items and pre-filling draft replies for the user to
  review and send manually.

---

IDENTITY

- The user's email address is mskovbaek@cedra.dk
- All logic that determines "sent by me" or "replied by me" must
  match exclusively against this address
- Store this in config.py as USER_EMAIL and never hardcode it
  elsewhere in the codebase

---

ARCHITECTURE REQUIREMENTS

- Python backend using win32com to interface with Outlook
- A lightweight local web server (Flask) serving a dashboard UI
- SQLite database for local memory: snooze states, dismissed items,
  manual overrides, sync logs, saved PrivateGPT responses
- All config (thresholds, whitelist domains, folder names, day
  thresholds, retention periods) in a single config.py —
  never hardcoded inline
- Modular structure: each feature must be its own module:
    - outlook_reader.py — Outlook COM connection and raw data pull
    - email_processor.py — overdue and awaiting-reply detection logic
    - calendar_reader.py — calendar event reading and processing
    - prompt_builder.py — PrivateGPT prompt generation and versioning
    - response_renderer.py — parsing and rendering AI responses
    - db.py — all SQLite read/write operations
    - sync.py — background sync loop
    - main.py — entrypoint, starts Flask and sync loop
  No monolithic files. Future features must be addable as new
  modules without modifying existing ones.
- All UI is served from local HTML/CSS/JS files — no external
  frameworks, no CDN links, no internet required to render anything
- Flask routes must be versioned (/api/v1/...) so a future UI
  can be swapped in without breaking the backend

---

FEATURE 1 — AWAITING REPLY (SENT EMAILS WITH NO RESPONSE)

- Read the Outlook Sent Items folder
- For each sent email sent from mskovbaek@cedra.dk, check whether
  any reply exists in the same conversation thread (matched by
  ConversationID) from any of the original recipients, received
  after the sent date
- Day threshold: 7 calendar days (including weekends)
- A thread is flagged as awaiting reply only when ALL of these
  are true:
    - The most recent message from mskovbaek@cedra.dk in the
      thread has received no response from the recipient(s)
    - No message exists in the thread after the sent date from
      any address the email was sent To or CC'd to
    - The email was sent directly To at least one recipient
      (pure BCC sends are excluded)
- Forwarding a thread does NOT resolve it — it stays flagged
- Auto-replies do NOT count as replies. Detect auto-replies by:
    - PR_MESSAGE_CLASS property containing "IPM.Note.Rules"
    - Subject starting with "Automatic reply:", "Out of office:",
      "Auto:", or similar prefixes (store prefix list in config.py)
    - X-Auto-Response-Suppress or Auto-Submitted headers
- Exclude from flagging:
    - Emails to domains listed in config.py EXCLUDED_DOMAINS
    - Threads manually marked "no reply needed" (stored in SQLite)
    - Newsletters and mailing lists (detected by List-Unsubscribe
      header or configurable sender patterns in config.py)
- Group flagged results by age:
    - 7–14 days
    - 14–30 days
    - 30+ days
- Each flagged item displays: recipient(s), subject, sent date,
  days waiting, attachment indicator if applicable

---

FEATURE 2 — OVERDUE INBOUND EMAILS

- Read the main Outlook Inbox folder only (no subfolders)
- Flag emails where ALL of these are true:
    - mskovbaek@cedra.dk is in the To field (not CC only)
    - No reply has been sent from mskovbaek@cedra.dk into
      that conversation thread after the received date
    - The email is older than 2 calendar days (configurable
      in config.py as INBOUND_OVERDUE_DAYS)
- Mark as higher priority if the subject or body contains
  a question mark
- Exclude:
    - Auto-replies (same detection as Feature 1)
    - Senders in config.py EXCLUDED_DOMAINS
    - Threads manually dismissed (stored in SQLite)
    - Mailing lists (same detection as Feature 1)

---

FEATURE 3 — CALENDAR OVERVIEW

- Read the Outlook calendar for today and the next 7 days
- Display a third dashboard section: "Upcoming meetings"
- For each meeting show: title, date, time, duration,
  organiser, acceptance status, number of attendees
- Flag meetings where:
    - You have not yet accepted or declined (status pending)
    - The meeting is within 24 hours and has no associated
      email thread or notes (possible lack of preparation)
- Meetings are read-only. No creating, editing, or declining
  from the dashboard. Clicking a meeting opens it in Outlook.
- Calendar data refreshes on the same 15-minute sync cycle
  as email data

---

FEATURE 4 — DASHBOARD UI

- Local web app served at http://localhost:5000
- Three clearly separated sections:
    1. "Awaiting your reply" (inbound overdue)
    2. "No response received" (sent awaiting reply)
    3. "Upcoming meetings" (calendar)
- Each email row shows: sender/recipient, subject, date,
  days waiting, attachment indicator (paperclip icon) if
  the email has attachments
- Each email row is clickable — clicking opens that exact
  email in Outlook using EntryID via a Flask route that
  calls outlook.Session.GetItemFromID(entry_id).Display()
- Each email row has the following action buttons:
    - "Snooze 3 days" — hides item, stores snooze expiry
      in SQLite, auto-resurfaces when expired
    - "No reply needed" — permanently dismisses from list,
      stored in SQLite
    - "Copy PrivateGPT prompt" — generates and copies
      the structured prompt to clipboard, and reveals
      the collapsible response panel below the row
- Each calendar row has:
    - Click to open in Outlook
    - A pending acceptance indicator where applicable
- Dashboard auto-refreshes every 15 minutes
- Last sync timestamp shown at the top of the dashboard
- A settings panel accessible from the dashboard with:
    - Display of current config values
    - A "Clear old records" button that manually triggers
      the 30-day purge

---

FEATURE 5 — PRIVATEGPT PROMPT BUILDER

This is the only bridge between the local app and any AI system.
It is always a manual, user-initiated action — never automatic.

The "Copy PrivateGPT prompt" button on each email row must:
- Generate a fully self-contained plain text prompt containing
  all necessary context (no references to external files)
- Copy it to the clipboard instantly on click
- Reveal the collapsible response panel directly below the
  thread row (see Feature 6)
- The prompt always follows this exact versioned template,
  stored in prompt_builder.py. The version is stored in
  config.py as PROMPT_VERSION. Any future change to the
  template must increment PROMPT_VERSION and be logged:

  === PRIVATEGPT PROMPT (v[PROMPT_VERSION]) ===

  You are a professional assistant helping to manage email
  follow-ups. Below is a thread context. Based only on the
  information provided, suggest a concise follow-up action.
  Format your response exactly as specified at the end of
  this prompt.

  --- THREAD CONTEXT ---
  Subject: [subject]
  Participants: [names and email addresses]
  Original sent date: [date]
  Days without reply: [n]
  Attachments: [comma-separated list of attachment names,
  or "None"]
  My last message: [body of last sent message, truncated
  to 500 words]
  Their last message (if any): [body, truncated to 500
  words, or "No reply received"]

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

  === END OF PROMPT ===

---

FEATURE 6 — PRIVATEGPT RESPONSE RENDERER

After copying the prompt and pasting it into PrivateGPT, the
user pastes the response back into the app via a collapsible
panel that appears directly below the relevant thread row
when "Copy PrivateGPT prompt" is clicked.

The collapsible panel contains:
- A clearly labelled text area: "Paste PrivateGPT response here"
- A "Save and render" button

On clicking "Save and render":
- Parse the response using the exact headings: SITUATION,
  ACTION, DRAFT REPLY, URGENCY
- If parsing succeeds, render the fixed response card
  directly below the text area:

  ┌──────────────────────────────────────────────┐
  │ SUBJECT: [subject]                           │
  │ DAYS WAITING: [n]                            │
  ├──────────────────────────────────────────────┤
  │ SITUATION                                    │
  │ [parsed text]                                │
  ├──────────────────────────────────────────────┤
  │ ACTION: [Reply/Call/Escalate/Close]          │
  │ URGENCY: [High/Medium/Low] — [reason]        │
  ├──────────────────────────────────────────────┤
  │ DRAFT REPLY                                  │
  │ [parsed text]         [Open reply in Outlook]│
  └──────────────────────────────────────────────┘

- "Open reply in Outlook" opens a Reply All to the thread
  in Outlook pre-filled with the DRAFT REPLY text. The user
  reviews and sends manually. The app never sends email.
- If parsing fails (missing or malformed headings), show a
  clear inline error: "Response format does not match the
  expected structure. Please check the PrivateGPT output
  and try again." Do not attempt to guess or reformat.
- Save the raw response and parsed fields to SQLite against
  the thread EntryID and a timestamp, so previous responses
  are retrievable per thread
- The rendered card layout is defined exclusively in
  response_renderer.py and response_card.html. No other
  module may modify these files. All future AI response
  features must extend this renderer, not replace it.

---

SYNC AND PERFORMANCE

- Outlook sync runs on a background thread every 15 minutes
- Results cached in SQLite between syncs so the dashboard
  loads instantly on every page load
- Sync must not block the UI or freeze Outlook
- Log every sync run to sync.log with: timestamp, number
  of items found per category, any errors encountered
- If Outlook is not running when a sync is attempted, log
  the failure gracefully and retry on the next cycle —
  do not crash

---

DATABASE AND RETENTION

- Auto-purge dismissed and snoozed records older than 30
  calendar days on each sync run
- PrivateGPT response records are kept indefinitely unless
  manually cleared via the settings panel
- All schema changes must be handled via versioned migration
  functions in db.py — never drop and recreate tables
- On first run, db.py must create all required tables if
  they do not exist

---

FUTURE-PROOFING REQUIREMENTS

The following features are NOT to be built now, but the
architecture must support adding them cleanly as new modules
without modifying any existing module:

- Task list integration (Outlook tasks)
- Morning brief generator combining email, calendar and tasks
- Excel file reader for Teams-based task tracking stored in
  local OneDrive sync folders (openpyxl), producing project
  status summaries per file
- Additional PrivateGPT prompt types (e.g. "meeting-prep",
  "task-delegation") — each with their own versioned template
  in prompt_builder.py, rendered by the same response_renderer
- A future frontend that consumes all data via /api/v1/ routes
  independently of the current UI

Nothing in the current build may assume it is the final UI or
the final set of data sources. All processed data must be
accessible via /api/v1/ routes in addition to being rendered
in the dashboard.

---

BUILD AND ITERATION WORKFLOW

You (Claude Code) are running inside a Windows VM on the user's personal
MacBook. You have real execution access in this VM — you can run Python,
PowerShell, and the app itself directly, and see the real output. This VM
CANNOT connect to the real Cedra Outlook mailbox (no sign-in access from
an unmanaged device), so it cannot be used to test against real mail or
calendar data. Do not attempt to connect to a real mailbox from here.

The workflow has two phases:

PHASE 1 — build and self-verify in the VM using mock data
Build a mock_outlook.py that simulates win32com Outlook objects closely
enough to exercise the real logic (see "Mock Outlook layer" requirements
below — cover auto-replies, forwarded threads, BCC-only sends, mailing
lists, attachments, all three age bands, and calendar acceptance states).
Structure outlook_reader.py so the mock can be swapped in behind the same
interface with no code changes needed later (dependency injection or a
config flag), so Phase 2 only changes which backend it points at.

Using the mock layer, build and self-verify every module yourself by
actually running it in this VM — do not just write code and assume it
works. Iterate until each module's real output (not projected output)
matches spec, in this order:
1. db.py — create and verify database and tables
2. config.py — all configuration values
3. mock_outlook.py — build the mock layer with all required edge cases
4. outlook_reader.py — verify it works correctly against the mock
5. email_processor.py — verify overdue and awaiting-reply logic against
   every mock edge case
6. calendar_reader.py — verify calendar data pull and flagging logic
7. sync.py — verify background sync loop and logging
8. prompt_builder.py — verify prompt generation with real mock data
9. response_renderer.py — verify response parsing, including malformed
   input
10. main.py + Flask routes — verify dashboard loads and renders mock data
    correctly
11. UI templates — verify dashboard renders correctly, including all
    action buttons and the collapsible PrivateGPT panel
12. PyInstaller .exe — verify the compiled app runs in the VM

Do not move to the next module until the current one is verified by
actual execution. Show your work, including any failures along the way.
If unsure what expected output should look like for any requirement,
say so rather than guessing.

PHASE 2 — verify against the real mailbox on the work PC
This phase happens on the user's actual Windows work PC, which you cannot
access. Once Phase 1 is complete, produce a short, explicit checklist of
only the steps that require a real win32com connection to the real
mailbox (see "Work-PC-only validation" below) and give the user exact
PowerShell commands to run. The user will paste the real output back.
Because the logic was already proven against mocks in Phase 1, this phase
should mostly confirm wiring against real data, not debug logic — but
iterate on any real discrepancies the same way.

Make sure no build decisions constrain future iterations of the app,
including planned future features.

---

VALIDATION — PHASE 1 (self-verify in the VM against mock data)

Demonstrate all of these yourself with real output from actually running
the code in the VM:

1.  db.py runs without error and creates all tables — show the SQLite
    schema
2.  mock_outlook.py provides realistic Sent Items, Inbox, and Calendar
    data covering all required edge cases — show sample items
3.  outlook_reader.py pulls raw data correctly from the mock — show item
    counts and a sample item from each source
4.  email_processor.py flags overdue inbound emails correctly against
    mock data — show the list with mock subjects, senders, and dates
5.  email_processor.py flags sent emails awaiting reply, grouped by age
    band, correctly against every mock edge case (forwarded, BCC-only,
    CC-only reply, auto-reply, mailing list) — show results per case
6.  calendar_reader.py pulls upcoming meetings including acceptance
    status and the 24-hour-no-prep flag — show mock data
7.  sync.log shows a complete sync run with item counts per category
    and timestamp
8.  Flask server starts and dashboard loads at localhost:5000 with no
    internet connection active, rendering mock data
9.  "Copy PrivateGPT prompt" generates the full prompt with mock data
    including attachment names where present — show the full prompt text
10. Collapsible response panel appears below the row after clicking
    "Copy PrivateGPT prompt"
11. Pasting a correctly formatted mock PrivateGPT response renders the
    fixed response card with all four fields correctly parsed
12. Pasting a malformed response shows the error message and nothing
    breaks
13. Snooze: item disappears after snoozing, reappears after expiry
    (test with a short expiry, restore to 3 days after)
14. "No reply needed": item disappears and does not return after a sync
15. Simulated Outlook-unavailable condition during sync: error is
    logged gracefully, no crash, sync resumes on next cycle
16. PyInstaller .exe builds and runs in the VM without error

Do not consider Phase 1 complete until all of these are passed with real
output from actual execution in the VM.

---

VALIDATION — PHASE 2 (work PC only, real mailbox, manual)

These cannot be tested in the VM and require the user to run the app on
the real work PC and report back real output:

1. outlook_reader.py connects to the real Outlook and pulls raw data
   from Sent Items, Inbox, and Calendar — show item counts and a sample
   item from each
2. email_processor.py flags overdue inbound emails using real mailbox
   data — show real subjects, senders, and dates
3. email_processor.py flags sent emails awaiting reply, grouped by age
   band, using real mailbox data
4. calendar_reader.py pulls real upcoming meetings including acceptance
   status
5. Clicking a thread row opens that exact email in Outlook — log the
   EntryID used
6. Clicking a calendar row opens that exact meeting in Outlook
7. "Copy PrivateGPT prompt" generates the full prompt with real data
   including real attachment names
8. "Open reply in Outlook" opens a Reply All draft pre-filled with the
   draft text — confirm this works
9. Outlook closed during a real sync: error is logged gracefully, no
   crash, sync resumes on next cycle

Do not consider the build fully complete until all Phase 2 items are
confirmed with real output from the work PC.
```

---

## Planned future features (not in v1)

- Outlook task list integration
- Morning brief generator (email + calendar + tasks combined)
- Excel reader for Teams task tracking files in OneDrive
  (openpyxl, reading local synced files — no API needed)
- Additional PrivateGPT prompt types: "meeting-prep",
  "task-delegation"
- Upgrade to a richer frontend consuming the /api/v1/ routes

---

## How to continue in a new Claude Code session

Run Claude Code inside the Windows VM on the MacBook, paste this entire
document into the session, and say:

"This is the full context of a project I have been planning. You are
running inside a Windows VM with no access to my real Outlook mailbox.
I want to start building it. Please confirm you have understood the
full context — including the two-phase workflow and the fact that you
cannot reach my real mailbox from here — summarise the key constraints
and decisions, and then begin with module 1: db.py, followed by the
mock Outlook layer."
