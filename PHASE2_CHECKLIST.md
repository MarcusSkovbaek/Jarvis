# Phase 2 — validation on Windows and against the real mailbox

Phase 1 is complete and verified against mock data: eleven test suites, a real
desktop window on the build machine, and the acceptance test against the
application as a black box. What remains can only be done on Windows: building
the .exe, checking the desktop window in Edge WebView2, and confirming the
wiring against real Outlook data.

**Nothing here sends anything to anyone.** Jarvis never contacts a server, and
every report below is a file written on your own computer. You read it, then
decide whether to pass it back. The two reports that contain anything from your
mailbox (`probe-output.txt` and, with `--no-redact`, its unredacted version)
replace names, addresses, subjects, filenames and bodies with stand-ins by
default; the header of each report lists exactly what is still in it.

Nothing below sends, deletes or modifies anything in Outlook. The only write
actions are `Display()` (opening an item) and `ReplyAll()` (opening a draft you
send by hand), and both happen only when you click.

---

## Step 0 — build the new .exe (any Windows machine with Python)

The Jarvis.exe from 20 September predates the desktop window, so it has to be
rebuilt. Use a Windows machine where you can run Python: your home PC, the VM,
or the work PC itself (a self-compiled .exe is what your IT policy permits).
In the project folder:

```powershell
python -m venv jarvis-env
```

```powershell
jarvis-env\Scripts\activate
```

```powershell
pip install -r requirements.txt
```

Run the full test suite first. On Windows, `verify_desktop.py` opens a **real
Edge WebView2 window** for a few seconds — that is the test, let it close by
itself:

```powershell
python tests\run_all.py
```

Expected: `All 11 verification scripts passed.` Part C of
`verify_dashboard_prompt.py` will say SKIPPED unless Playwright is installed;
that part was run and passed on the build machine, so a skip here is fine.

Then build and test the binary:

```powershell
jarvis-env\Scripts\pyinstaller.exe --noconfirm jarvis.spec
```

```powershell
python tests\verify_exe.py
```

Expected: every check passes, ending in `ALL CHECKS PASSED`. Copy
`dist\Jarvis.exe` and the two .bat files from `dist\` into one folder you can
write to (Desktop is fine — **not** Program Files) on the work PC.

**If anything in Step 0 fails, stop and send me the console output** — it is
all mock data, safe to share as-is.

---

## Before you start on the work PC: two things to expect

**1. Windows SmartScreen may warn you** if the .exe was built on a different
machine. If you get "Windows protected your PC", it is the missing signature,
not a detection. Building it on the work PC itself avoids this entirely.

**2. First launch is slow.** The one-file .exe unpacks itself before starting,
so the first run takes several seconds longer than later ones. It has not hung.

---

## Step 1 — the desktop check (2 minutes, Outlook untouched)

```powershell
.\Jarvis.exe --check-desktop
```

A Jarvis window opens on **invented test data**, tests itself for about ten
seconds and closes. It writes `desktop-check.txt` next to the .exe. Your
mailbox is never opened, and your real `jarvis.db` and `sync.log` are not
touched — the check runs in a temporary folder.

What it checks, and what "good" looks like:

| Check | Good |
|-------|------|
| Window possible | `yes` — pywebview and the WebView2 runtime are present |
| Renderer | a user agent containing `Edg/` (Edge WebView2) |
| Cards / rail / views | 5 / 5 / every view `ok` |
| Layout overflow | `none` |
| Clipboard bridge | `present - works` (your clipboard is restored afterwards) |
| Network connections | only `loopback (this computer)` or `none` |
| Result | `All desktop checks passed.` |

**Send me `desktop-check.txt`.** It contains no mail data, no local IP
addresses and no user or machine names. If WebView2 or Windows contacted
anything outside your computer while the window was open, the public address
is listed there — that is the one thing I cannot see from the build machine,
and exactly what I need in order to switch it off.

---

## Step 2 — confirm the .exe runs (60 seconds, Outlook untouched)

Double-click **`Start-Jarvis-MockData.bat`**.

The Jarvis window opens on invented data — Lars Petersen, Maria Holm, a Skagen
handover, and so on. Confirm:

- **Home** shows a greeting and five cards: *Awaiting your reply*, *Calendar*,
  *PrivateGPT brief*, *No response received* and *Today's focus*
- the rail on the left switches to **Email**, **Calendar**, **PrivateGPT** and
  **Settings**
- on **Email**: age bands 7–14 / 14–30 / 30+, paperclips on rows with
  attachments, and **Copy PrivateGPT prompt** opening a panel below the row

Close the Jarvis window to stop it, then delete the mock database:

```powershell
del jarvis.db
```

> If it opens in your **browser** instead of a window, Jarvis could not create
> the window. It still works; the reason is in `sync.log`
> (`Get-Content sync.log | Select-String "browser instead"`). Send me that line.

---

## Step 3 — the read-only probe (covers checklist items 1, 2, 3, 4 and 7)

This is the most useful single command. Open Outlook first and let the profile
finish loading. Then, in the folder containing the .exe:

```powershell
.\Jarvis.exe --probe
```

It reads your mailbox, changes nothing, and writes **`probe-output.txt`** next
to the .exe. **It is redacted by default:** every address becomes a stand-in
like `person3@domain2.example`, names become `Person 3`, subjects become
`SV: <subject, 22 chars>`, filenames keep only their extension, and message
bodies and Outlook item ids are reduced to their length. The same real value
always gets the same stand-in, so I can still follow a thread from section to
section. **Open the file and read it before you send it** — the top of the file
lists exactly what is still in it (counts, dates, day arithmetic, reply
prefixes, file extensions).

To see the unredacted version for yourself — **not for sharing**:

```powershell
.\Jarvis.exe --probe --no-redact --report probe-private.txt
```

What I am checking in the redacted report:

| # | Check | What "good" looks like |
|---|-------|------------------------|
| 1 | Raw pull works | Non-zero counts for Sent Items, Inbox and Calendar |
| 2 | Overdue inbound | Plausible day counts and priorities; you can compare against the private version |
| 3 | Awaiting reply | Threads grouped into 7–14 / 14–30 / 30+ |
| 4 | Calendar | Meetings with acceptance status, attendee counts and the two flags |
| 7 | Prompt | The template complete, with every value substituted |

**The thing most likely to need fixing.** On Exchange, sender addresses often
come back as an X500 DN (`/O=EXCHANGELABS/OU=...`) rather than
`mskovbaek@cedra.dk`. The reader already falls back to
`PR_SENDER_SMTP_ADDRESS` and `GetExchangeUser()`, but if the probe prints
**"WARNING: no sent item matched USER_EMAIL"**, that fallback did not resolve on
your tenant and Feature 1 will find nothing until it does. In that case the
report shows the *structure* of the values — `/o=<12>/ou=<47>/cn=<10>` — which
is what I need to fix it, without the names inside.

---

## Step 4 — run it against the real mailbox

Double-click **`Start-Jarvis.bat`** (equivalently: `.\Jarvis.exe --backend com`).
The Jarvis window opens on your real mail.

### Item 5 — clicking a row opens that exact email

Click any row — on Home or on Email — and confirm the right message opens in
Outlook. The EntryID used is recorded:

```powershell
Get-Content sync.log -Tail 20 | Select-String "Displayed item"
```

### Item 6 — clicking a meeting opens that exact meeting

Click a meeting on the Home timeline or on the Calendar view and confirm the
right meeting opens.

### Item 7 — a real prompt with real attachment names

On a row showing the paperclip, click **Copy PrivateGPT prompt**. The panel
opens and the prompt goes to your clipboard. Paste it into a text editor and
confirm the `Attachments:` line lists the real filenames.

> If the clipboard is blocked, the prompt appears in a text box in the panel,
> already selected — Ctrl+C from there. Nothing is lost either way.

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

### Item 10 — the PrivateGPT dashboard, end to end

1. On Home, click **Build a PrivateGPT dashboard from everything here** (or
   **Copy dashboard prompt**).
2. Paste it into PrivateGPT. It should answer with one block of HTML.
3. Paste that into Notepad and save as `jarvis-overview.html`, with "Save as
   type" set to *All files*.
4. In Jarvis, open **PrivateGPT** in the rail, step 4, **Check a saved page…**,
   and pick the file.
   - **Safe to open**: double-click the file. Tick an item, reload the page,
     and confirm the tick is still there.
   - **Don't open this page**: the reasons are listed. Ask PrivateGPT to build
     it again, reminding it of the rules in the prompt. Tell me what it
     flagged, so I can tighten the prompt.

This is the one step that depends on how PrivateGPT itself behaves, which I
cannot test. What I want to know: did it produce a page in one go, did the
check pass, and was the page useful?

---

## If something goes wrong

**"Port 5000 is already in use."** Jarvis is probably already running, or a
previous copy did not shut down. The message says which. Either use the
running one, or:

```powershell
taskkill /F /IM Jarvis.exe
```

**Nothing appears / it closes immediately.** The reason is in `sync.log` next
to the .exe:

```powershell
Get-Content sync.log -Tail 40
```

**Check what a copy is configured for** without starting anything:

```powershell
.\Jarvis.exe --version
```

**Prefer the browser to the window**, or the window will not open:

```powershell
.\Jarvis.exe --backend com --browser
```

**More detail in the log:**

```powershell
.\Jarvis.exe --backend com --log-level DEBUG
```

---

## What to send back

Everything below is a file on your computer. Read each one before sending.

1. The Step 0 console output, if anything failed there (mock data only).
2. `desktop-check.txt` from Step 1 (no mail data by construction).
3. `probe-output.txt` from Step 3 (redacted by default — read the header).
4. Whether items 5–10 behaved as described, and what happened if not.
5. Anything that looked wrong in the flagged lists — a thread that should have
   been flagged and was not, or one flagged that should not have been. Those
   are exactly the cases mock data cannot tell us about. Describe them in your
   own words; there is no need to send the emails themselves.

---

## Two decisions worth revisiting with real data in front of you

**1. Internal mail is currently excluded.** `EXCLUDED_DOMAINS` contains
`cedra.dk`, so threads with colleagues are not tracked at all. That was a
conservative default I chose, not something you specified. If you want internal
threads tracked, that is a one-line config change and a rebuild.

**2. A reply from a CC'd recipient currently resolves a thread.** Your brief
contradicted itself here; the Feature 1 wording won. `CC_REPLY_RESOLVES_THREAD`
flips it. Real data will tell you quickly which behaviour you want.

Tell me which way you want either of these and I will change it.

---

## Appendix: run from source instead of the .exe

Everything in this checklist works the same from source, after the first three
commands of Step 0:

```powershell
python main.py --backend com
```

```powershell
python main.py --check-desktop
```

```powershell
python tests\phase2_probe.py
```

`Jarvis.exe --probe` and `python tests\phase2_probe.py` run identical code.
