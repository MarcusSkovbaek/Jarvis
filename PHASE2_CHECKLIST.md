# Phase 2 — validation against the real mailbox (work PC only)

Phase 1 is complete: every module verified by actual execution against mock
data, and the compiled `Jarvis.exe` passing 111 black-box checks. What remains
is confirming the wiring against real Outlook data, which can only be done on
your work PC.

Nothing below sends, deletes or modifies anything in Outlook. The only write
actions are `Display()` (opening an item) and `ReplyAll()` (opening a draft you
send by hand), and both happen only when you click.

You need three files, all in one folder you can write to (Desktop is fine —
**not** Program Files):

```
Jarvis.exe
Start-Jarvis.bat            launches against the real mailbox
Start-Jarvis-MockData.bat   launches against fake data, Outlook untouched
```

`jarvis.db` and `sync.log` are created next to the .exe on first run.

---

## Before you start: two things to expect

**1. Windows SmartScreen will probably warn you.** The .exe is unsigned and was
built on my machine, not yours. If you get "Windows protected your PC", it is
because of the missing signature, not because anything was detected. If your
policy blocks unsigned executables outright, skip to
[Appendix: build it yourself](#appendix-build-it-on-the-work-pc) — building from
source on the work PC produces a genuinely self-compiled .exe, which is what
your IT policy actually permits.

**2. First launch is slow.** The one-file .exe unpacks itself before starting,
so the first run takes several seconds longer than later ones. It has not hung.

---

## Step 1 — confirm the .exe runs at all (60 seconds, Outlook untouched)

Double-click **`Start-Jarvis-MockData.bat`**.

A console window opens, then the dashboard at http://localhost:5000 showing
invented test data — Lars Petersen, Maria Holm, a Skagen handover, and so on.
None of it is real and Outlook is never contacted.

Confirm you can see:

- three sections: "Awaiting your reply", "No response received", "Upcoming meetings"
- age bands 7–14 / 14–30 / 30+ under the second section
- a paperclip on rows with attachments
- **Copy PrivateGPT prompt** opening a panel below the row

Close the console window to stop it, then delete `jarvis.db` so the mock data
does not linger:

```powershell
del jarvis.db
```

If this step fails, stop here and send me the console text plus `sync.log` —
there is no point going further.

---

## Step 2 — the read-only probe (covers checklist items 1, 2, 3, 4 and 7)

This is the single most useful command. Open Outlook first and let the profile
finish loading. Then, in the folder containing the .exe:

```powershell
.\Jarvis.exe --probe > probe-output.txt 2>&1
```

If any of it is too sensitive to share, mask addresses and subjects instead
(this also omits the generated prompt, since that contains message bodies):

```powershell
.\Jarvis.exe --probe --redact > probe-output.txt 2>&1
```

Send me `probe-output.txt`. It starts no server, creates no database, and only
reads. What I am checking:

| # | Check | What "good" looks like |
|---|-------|------------------------|
| 1 | Raw pull works | Non-zero counts for Sent Items, Inbox and Calendar, with a readable sample item from each |
| 2 | Overdue inbound | Real subjects, senders and day counts that match your sense of what is outstanding |
| 3 | Awaiting reply | Real threads grouped into 7–14 / 14–30 / 30+ |
| 4 | Calendar | Real meetings with acceptance status, attendee counts and the two flags |
| 7 | Prompt | A complete prompt with real participants, dates and attachment names |

**The thing most likely to need fixing.** On Exchange, `SenderEmailAddress`
often comes back as an X500 DN (`/O=EXCHANGELABS/OU=...`) rather than
`mskovbaek@cedra.dk`. The reader already falls back to
`PR_SENDER_SMTP_ADDRESS` and `GetExchangeUser()`, but if the probe prints
**"WARNING: no sent item matched USER_EMAIL"**, that fallback did not resolve on
your tenant and Feature 1 will find nothing until it does. The probe prints the
raw sender values in that case — those are exactly what I need to fix it.

---

## Step 3 — run it against the real mailbox

Double-click **`Start-Jarvis.bat`** (equivalently: `.\Jarvis.exe --backend com`).

The dashboard opens at http://localhost:5000, bound to loopback only and not
reachable from the network.

### Item 5 — clicking a thread row opens that exact email

Click any row in either email section and confirm the right message opens in
Outlook. The EntryID used is recorded:

```powershell
Get-Content sync.log -Tail 20 | Select-String "Displayed item"
```

### Item 6 — clicking a meeting row opens that exact meeting

Click a row under "Upcoming meetings" and confirm the right meeting opens.

### Item 7 — a real prompt with real attachment names

On a row showing the paperclip, click **Copy PrivateGPT prompt**. The panel
opens and the prompt goes to your clipboard. Paste it into a text editor and
confirm the `Attachments:` line lists the real filenames.

> If clipboard access is blocked by policy, the prompt appears in a text box in
> the panel, already selected — Ctrl+C from there. Nothing is lost either way.

### Item 8 — "Open reply in Outlook" pre-fills a Reply All draft

Paste a PrivateGPT response into the panel, click **Save and render**, then
click **Open reply in Outlook** on the rendered card. Confirm:

- a **Reply All** draft opens with all the original recipients on it,
- the drafted text sits at the top, above the quoted history,
- **nothing is sent** — you close it or send it yourself.

```powershell
Get-Content sync.log -Tail 20 | Select-String "Reply All draft"
```

### Item 9 — Outlook closed during a sync

With Jarvis running, close Outlook completely, then click **Sync now**.
Expected: the dashboard keeps showing cached results, a warning banner appears,
and `sync.log` records the failure with no traceback. Reopen Outlook and click
**Sync now** again — it should recover.

```powershell
Get-Content sync.log -Tail 30 | Select-String "Sync skipped|Sync OK"
```

---

## If something goes wrong

**"Port 5000 is already in use."** Jarvis is probably already running, or a
previous copy did not shut down. The message says which. Either open
http://localhost:5000 to use the running one, or:

```powershell
taskkill /F /IM Jarvis.exe
```

**Nothing appears / the window closes immediately.** The reason is in
`sync.log` next to the .exe:

```powershell
Get-Content sync.log -Tail 40
```

**Check what a copy is configured for** without starting anything:

```powershell
.\Jarvis.exe --version
```

**More detail in the log:**

```powershell
.\Jarvis.exe --backend com --log-level DEBUG
```

---

## What to send back

1. `probe-output.txt` from Step 2.
2. Whether items 5, 6, 7, 8 and 9 behaved as described, and what happened if not.
3. Anything that looked wrong in the flagged lists — a thread that should have
   been flagged and was not, or one flagged that should not have been. Those
   are exactly the cases mock data cannot tell us about, and they are the most
   valuable thing you can send.

---

## Two decisions worth revisiting with real data in front of you

**1. Internal mail is currently excluded.** `EXCLUDED_DOMAINS` contains
`cedra.dk`, so threads with colleagues are not tracked at all. That was a
conservative default I chose, not something you specified. If you want internal
threads tracked, that is a one-line config change — but it needs a rebuild, or
running from source.

**2. A reply from a CC'd recipient currently resolves a thread.** Your brief
contradicted itself here; the Feature 1 wording won. `CC_REPLY_RESOLVES_THREAD`
flips it. Real data will tell you quickly which behaviour you want.

Tell me which way you want either of these and I will rebuild.

---

## Appendix: build it on the work PC

Use this if SmartScreen or policy blocks the .exe I sent, or if you want a
binary you compiled yourself. Copy the project folder across, excluding
`jarvis-env`, `build`, `dist`, `__pycache__`, `jarvis.db` and `sync.log`.

```powershell
python -m venv jarvis-env
```

```powershell
jarvis-env\Scripts\activate
```

```powershell
pip install flask pywin32 pyinstaller
```

```powershell
jarvis-env\Scripts\pyinstaller.exe --noconfirm jarvis.spec
```

`dist\Jarvis.exe` is then yours, built locally. Copy the two .bat files in
beside it and continue from Step 1.

You can also skip the .exe entirely and run from source:

```powershell
python main.py --backend com
```

```powershell
python tests\phase2_probe.py > probe-output.txt 2>&1
```

Everything in this checklist works the same either way — `Jarvis.exe --probe`
and `python tests\phase2_probe.py` run identical code.
