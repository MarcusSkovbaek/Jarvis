# Handover — how this repository was created

This repository holds the Jarvis source that was built on the Windows PC at
`C:\Users\marcu\Desktop\Jarvis`. It was reconstructed from the exported Claude
Code session transcript of that build, so the project could be continued on
another machine.

## How the reconstruction was done

The transcript records every `Write` and `Edit` the build session performed.
Those operations were replayed in order onto an empty tree:

- 35 file-writes and 69 edits replayed, **zero** mismatches — every `Edit`
  matched its target exactly once, which means no file drifted out of sync
  with the transcript.
- Files written outside the project directory (the three Claude memory notes)
  were not placed in the tree; they are preserved under `docs/` instead.
- `dist\Jarvis.exe` **is not here.** A 13.5 MB compiled binary is not in the
  transcript and does not belong in git. Rebuild it with
  `pyinstaller jarvis.spec` (see README, "Building the .exe"). For reference,
  the binary handed over on 2026-09-20 was 13,527,721 bytes,
  SHA256 `85B416D7104A54A9EE4A4765D4B23A37878AFD19C7C8256D3911B53FA5836B24`.
- Two earlier launcher files (`Start Jarvis.bat`, `Start Jarvis (mock data).bat`)
  were renamed during the build and are not included; the hyphenated versions in
  `dist/` are the current ones. Both reconstruct to their exact original byte
  sizes (738 and 826 bytes), with CRLF endings preserved via `.gitattributes`.

## What was verified on the reconstruction, not just assumed

Run on Linux with Python 3.13 (the build machine also used 3.13 — `main.py`
uses a multi-line f-string, which needs 3.12+):

- `python tests/run_all.py` — all 6 suites pass.
- `python main.py --backend mock --sync-once` — sync completes:
  14 sent / 14 inbox / 7 calendar items pulled, 5 awaiting reply,
  6 overdue inbound, 7 meetings.
- `python main.py --probe --backend mock` — probe runs and prints its report.
- `python main.py --backend mock --no-browser --port 5057` — the dashboard
  serves; `/healthz`, `/api/v1/status` and `/` all respond correctly.

`tests/verify_exe.py` was **not** run here — it needs a compiled
`dist/Jarvis.exe`, which requires PyInstaller on Windows.

## Where the project stands

Phase 1 (build and self-verify against mock data) is complete. Phase 2 (wiring
against the real `mskovbaek@cedra.dk` mailbox) has not been started and cannot
be done from this machine — it needs the Windows work PC with Outlook open.
`PHASE2_CHECKLIST.md` is the step-by-step; `Jarvis.exe --probe` is the single
most useful first command.

Two carried-over notes from the build session:

- [`jarvis-phase-status.md`](jarvis-phase-status.md) — the phase split and the
  highest-risk Phase 2 unknown (Exchange returning X500 DNs instead of SMTP
  addresses).
- [`jarvis-spec-ambiguities.md`](jarvis-spec-ambiguities.md) — the two
  contradictory points in the build brief and which reading won.
- [`jarvis_project_context.md`](jarvis_project_context.md) — the original
  planning document and full build brief that started the project.
