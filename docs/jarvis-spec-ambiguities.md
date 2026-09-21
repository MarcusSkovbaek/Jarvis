---
name: jarvis-spec-ambiguities
description: Two genuinely ambiguous points in the Jarvis build brief and how they were resolved
metadata:
  type: project
---

The Jarvis build brief (Outlook assistant dashboard, work PC, PrivateGPT via
manual copy-paste) left two points ambiguous. Both were resolved in Phase 1 and
each is controlled by one value in `config.py`, so either behaviour is one edit
away.

1. **CC'd reply resolves a thread** (`CC_REPLY_RESOLVES_THREAD = True`).
   Feature 1's text says a thread stays flagged only while no message exists
   "from any address the email was sent To or CC'd to" — making a CC'd reply
   resolving. The mock-data notes in the same brief said a CC-only reply
   "should still flag". Direct contradiction; the Feature 1 wording won as the
   operative spec.

2. **A forward does not reset the days-waiting clock.** Decision #3 says
   forwarding does not resolve a thread but not which date the count runs from.
   Jarvis anchors on the user's last real send to the original recipients and
   ignores forwards entirely.

**Why:** these are the two places where a reasonable reader could implement the
opposite behaviour, so they are the most likely source of "that's not what I
meant" once real mailbox data is in front of the user.

**How to apply:** if the user reports a thread flagged (or not flagged) that
surprises them in Phase 2, check these two first before assuming a bug. Both
are exercised explicitly in `tests/verify_processors.py`, including a check
that flipping the CC flag produces the alternative result.

Related: [[jarvis-phase-status]]
