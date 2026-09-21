---
name: jarvis-phase-status
description: Jarvis is a two-phase build; Phase 2 needs the user's work PC and cannot be done here
metadata:
  type: project
---

Jarvis is built in two phases because the machine it is developed on has no
access to the real Cedra mailbox (`mskovbaek@cedra.dk` — Conditional Access
blocks unmanaged devices).

- **Phase 1** (complete as of 2026-09-19): every module built and verified by
  actual execution against `mock_outlook.py`. Five suites in `tests/`, run with
  `python tests\run_all.py`. Compiled `dist\Jarvis.exe` confirmed to run
  standalone and serve the dashboard.
- **Phase 2** (not started): real win32com wiring, run by the user on their
  Windows work PC. Steps are in `PHASE2_CHECKLIST.md`; `tests\phase2_probe.py`
  is a read-only probe that prints everything needed in one go.

**Why:** do not attempt to connect to a real mailbox from this machine, and do
not treat Phase 2 items as verifiable here — they need the user to run commands
and paste output back.

**How to apply:** the highest-risk Phase 2 unknown is Exchange returning X500
DNs instead of SMTP addresses for senders/recipients. `outlook_reader.py`
already falls back to `PR_SENDER_SMTP_ADDRESS` and `GetExchangeUser()`, and the
probe prints a warning if no sent item matches `USER_EMAIL`. If that warning
appears, Feature 1 will return nothing until address resolution is fixed.

Related: [[jarvis-spec-ambiguities]]
