# Jarvis

A local Outlook assistant dashboard. Everything runs on one machine: no cloud,
no external API calls, no CDN, no telemetry. It works with the network cable
unplugged.

The only bridge to any AI system is a **Copy PrivateGPT prompt** button, which
puts text on your clipboard for you to paste by hand. Jarvis never contacts
PrivateGPT, or anything else, on its own.

Outlook access is read-only, with two exceptions, both triggered by you
clicking: opening an item (`Display()`), and opening a pre-filled **Reply All**
draft (`ReplyAll()`) that you review and send yourself. Jarvis never calls
`Send()`.

---

## Running it

```powershell
python -m venv jarvis-env
```

```powershell
jarvis-env\Scripts\activate
```

```powershell
pip install flask pywin32
```

Against mock data (no mailbox needed — this is how it runs on any machine):

```powershell
python main.py --backend mock
```

Against the real Outlook mailbox (Windows, Outlook running):

```powershell
python main.py --backend com
```

The dashboard is at http://localhost:5000, bound to loopback only.

| Flag | Effect |
|------|--------|
| `--backend mock\|com` | which Outlook backend to use |
| `--port N` | serve on a different port |
| `--no-browser` | do not open a browser window |
| `--no-sync` | serve cached data without starting the sync loop |
| `--sync-once` | run one sync, print the result, exit |
| `--log-level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `--debug` | verbose logging |
| `--version` | print version, paths and backend, then exit |

`JARVIS_OUTLOOK_BACKEND` works as an environment variable equivalent of
`--backend`.

---

## What it shows

**1. Awaiting your reply** — mail sent directly to you (To, not CC), unanswered
for more than `INBOUND_OVERDUE_DAYS` (2). Anything containing a question mark is
raised to high priority.

**2. No response received** — threads where your most recent message has had no
reply for more than `SENT_AWAITING_REPLY_DAYS` (7), grouped into 7–14 / 14–30 /
30+ calendar days.

**3. Upcoming meetings** — today and the next 7 days, flagged when you have not
accepted or declined, or when a meeting starts within 24 hours with no notes and
no related email thread. Read-only.

Every row: click to open in Outlook, **Snooze 3 days**, **No reply needed**,
**Copy PrivateGPT prompt**. Paperclip where there are attachments, with the
filenames shown and included in the prompt.

---

## Layout

```
main.py                 entrypoint: starts the sync loop, then Flask
config.py               every configurable value; nothing is hardcoded elsewhere
outlook_reader.py       the only module that talks to Outlook
mock_outlook.py         mock win32com layer, for running without a mailbox
email_processor.py      Features 1 and 2 detection logic (pure, no I/O)
calendar_reader.py      Feature 3 logic (pure, no I/O)
prompt_builder.py       versioned PrivateGPT prompt templates
response_renderer.py    response parsing + the fixed card (owns response_card.html)
dashboard.py            builds the view model from the cache
db.py                   every SQLite read and write
api.py                  the /api/v1 blueprint
app.py                  Flask application factory
sync.py                 background sync loop and logging
templates/              dashboard.html, row.html, response_card.html
static/                 style.css, app.js — local only, no external references
tests/                  the Phase 1 verification suite and the Phase 2 probe
jarvis.spec             PyInstaller build definition
```

`response_renderer.py` and `templates/response_card.html` own the response card.
Nothing else modifies them; future AI response features extend that renderer.

All processed data is available at `/api/v1/` as well as in the rendered page,
so a different frontend can be built against the API alone.

---

## Logging

Everything goes to `sync.log` next to the app (or next to the .exe), rotating
at 2 MB with three backups. One file holds the lot:

- a startup banner with the instance id, whether it is running frozen, the
  identity, backend, database and log paths, the prompt version and every
  threshold — so a log can always be matched to the run that produced it;
- every sync: raw item counts pulled from Outlook, flagged counts per
  category, the age-band breakdown, records purged, and elapsed time;
- every user action: prompt generated (with size and attachment count),
  response saved or rejected, snooze, dismissal, purge, item opened in
  Outlook, reply draft opened;
- every HTTP request;
- any uncaught exception, including from background threads.

The console shows the same thing minus the per-request lines. That is
deliberate: the request log is high volume, and if Jarvis is launched by a
parent process that pipes stdout without reading it, that volume fills the OS
pipe buffer and blocks the process mid-request. File-only keeps the complete
record with no such risk.

`/healthz` reports the instance id, process id, backend and data directory,
which is the quickest way to tell which instance is answering on a port.

---

## Verifying it

```powershell
python tests\run_all.py
```

Eight suites against mock data, including `verify_prompt_conformance.py`, which
asserts the generated PrivateGPT prompt matches the build brief **byte for
byte** — every word, blank line and dash.

Two of the eight exist to check the promises that a reader cannot verify by
reading the code:

- `verify_no_egress.py` installs a CPython audit hook — which fires for every
  socket connection, DNS lookup and URL opened anywhere in the process,
  including inside libraries Jarvis did not write — then drives a full sync,
  every API endpoint, the dashboard and the probe, and asserts that nothing
  connected to anything but loopback. It ends with a negative control: it
  makes one deliberate outbound connection and confirms the hook catches it,
  so the test cannot pass by failing to look.
- `verify_probe_redaction.py` takes every address, name, subject, filename and
  body out of the mock mailbox, runs the probe in its shareable mode, and
  asserts none of them survive — whole or in fragments — while the things that
  make the report useful (reply prefixes, file extensions, counts, id shapes)
  do.

Separately, a black-box acceptance test that launches the compiled .exe in a
clean directory and drives it over HTTP exactly as the browser does:

```powershell
python tests\verify_exe.py
```

111 checks covering the prompt text, response parsing, the card, snooze and
dismissal, purge, restart persistence, the log contents and the port-conflict
message. It never imports the application — it only talks to the binary.

Five scripts, all running against mock data, covering every Phase 1 validation
item: schema creation and migrations, the mock mailbox edge cases, raw pulls and
normalisation, both email features against every edge case, calendar flags, a
full sync run and its log, graceful handling of Outlook being unavailable,
prompt generation, response parsing (valid and malformed), the rendered
dashboard, the API, snooze expiry and permanent dismissal.

Run one at a time for the detailed output:

```powershell
python tests\verify_processors.py
```

---

## Building the .exe

```powershell
pip install pyinstaller
```

```powershell
jarvis-env\Scripts\pyinstaller.exe --noconfirm jarvis.spec
```

`dist\Jarvis.exe` runs on a machine with no Python installed. `templates/` and
`static/` are bundled inside it; `jarvis.db` and `sync.log` are created next to
the .exe, so put it somewhere writable.

Two things worth knowing about the one-file build:

- The bootloader unpacks on first launch, so the first start takes a few
  seconds longer than later ones.
- It runs the application in a **child** process. Closing the console window or
  Ctrl+C stops both, but killing only the launched process can leave the child
  holding port 5000. If that happens the next launch does not throw a socket
  traceback — it reports that the port is in use, says whether the occupant is
  another Jarvis and where its data lives, and exits with code 2.

---

## Configuration

Everything lives in `config.py`. The values most likely to need changing:

| Setting | Default | Meaning |
|---------|---------|---------|
| `USER_EMAIL` | `mskovbaek@cedra.dk` | the only address treated as "me" |
| `OUTLOOK_BACKEND` | `mock` | `mock` or `com` |
| `SENT_AWAITING_REPLY_DAYS` | 7 | Feature 1 threshold |
| `INBOUND_OVERDUE_DAYS` | 2 | Feature 2 threshold |
| `AGE_BANDS` | 7–14 / 14–30 / 30+ | Feature 1 grouping |
| `EXCLUDED_DOMAINS` | includes `cedra.dk` | domains never flagged |
| `SNOOZE_DAYS` | 3 | snooze length |
| `RETENTION_DAYS` | 30 | auto-purge window for dismissals and snoozes |
| `SYNC_INTERVAL_MINUTES` | 15 | sync cycle |
| `PROMPT_VERSION` | 1 | increment on any prompt template change |

Saved PrivateGPT responses are kept indefinitely and are never touched by the
purge; clear them from the settings panel.

---

## Two judgement calls worth knowing about

The brief left two points genuinely ambiguous. Both are resolved by a single
config value so either behaviour is one edit away.

**A reply from a CC'd recipient resolves the thread**
(`CC_REPLY_RESOLVES_THREAD = True`). Feature 1 says a thread stays flagged only
while no message exists "from any address the email was sent To or CC'd to",
which makes a CC'd reply resolving. The mock-data notes separately suggested a
CC-only reply should still flag. The Feature 1 wording won, since it is the
operative spec. Set the flag to `False` to require a reply from a To recipient.

**A forward does not reset the clock.** Decision #3 says forwarding does not
resolve a thread, but not which date the count runs from afterwards. Jarvis
anchors on your last real send to the original recipients and ignores forwards
entirely, so a thread sent 20 days ago and forwarded 6 days ago still reads as
20 days waiting — which is the number that reflects how long they have been
sitting on it.

Both are exercised explicitly in `tests\verify_processors.py`.

---

## Status

Phase 1 (build and self-verify against mock data) is complete: every module
verified by actual execution, all six suites passing, and the compiled .exe
passing 111 black-box checks — including that the prompt it serves matches the
brief byte for byte.

Phase 2 (real mailbox on the work PC) is not started — see
[PHASE2_CHECKLIST.md](PHASE2_CHECKLIST.md).

Planned but deliberately not built: Outlook task integration, a morning brief
generator, an Excel reader for Teams task files, extra prompt types
("meeting-prep", "task-delegation"), and a richer frontend on `/api/v1/`. Each
can be added as a new module without modifying an existing one.
