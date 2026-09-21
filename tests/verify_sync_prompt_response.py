"""
verify_sync_prompt_response.py — Phase 1 validation items 7, 9, 11, 12 and 15.

  7  sync.log shows a complete run with per-category counts and a timestamp
  9  "Copy PrivateGPT prompt" produces the full prompt, attachments included
 11  a correctly formatted response parses into all four fields
 12  a malformed response shows the error and breaks nothing
 15  Outlook unavailable during sync is logged gracefully; next cycle recovers

Run:  python tests\verify_sync_prompt_response.py
"""

import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

# Redirect the database and the log into a temp directory BEFORE anything
# reads them, so the real jarvis.db and sync.log are never touched.
_TMP = tempfile.mkdtemp(prefix="jarvis_sync_")
config.DB_PATH = os.path.join(_TMP, "verify.db")
config.LOG_PATH = os.path.join(_TMP, "sync.log")

import db  # noqa: E402
import mock_outlook  # noqa: E402
import prompt_builder  # noqa: E402
import response_renderer as rr  # noqa: E402
import sync  # noqa: E402
from outlook_reader import OutlookReader  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


GOOD_RESPONSE = """SITUATION: Marcus sent the revised Q3 budget figures to Lars nine days ago and has had no response. The contingency line still needs confirmation before the forecast can be locked.
ACTION: reply
DRAFT REPLY: Hi Lars, following up on the revised Q3 figures I sent on 10 September. We need the contingency line confirmed before we can lock the forecast. Could you let me know by Wednesday, or point me to whoever should sign it off? Happy to walk through the assumptions sheet if that helps.
URGENCY: Medium - The forecast lock date is approaching but has not yet passed."""

MESSY_BUT_VALID = """**SITUATION:** [Two sentences of summary here. Second sentence.]
**ACTION:** Reply to Lars
**DRAFT REPLY:** Hi Lars,

Quick nudge on this one.

Best,
Marcus
**URGENCY:** **High** — Blocking the forecast lock."""

MALFORMED = [
    ("missing URGENCY heading",
     "SITUATION: Something happened.\nACTION: reply\nDRAFT REPLY: Hi there."),
    ("prose with no headings at all",
     "Sure! I think you should just give Lars a call about the budget."),
    ("invalid ACTION value",
     "SITUATION: A.\nACTION: think about it\nDRAFT REPLY: N/A\n"
     "URGENCY: Low - no rush."),
    ("invalid URGENCY level",
     "SITUATION: A.\nACTION: close\nDRAFT REPLY: N/A\nURGENCY: Critical - now."),
    ("empty response", "   "),
]


def main():
    sync.configure_logging(level=logging.INFO, console=False)
    db.init_db()
    print(f"Temp workspace: {_TMP}\n")

    # =======================================================================
    print("7. sync run and sync.log")
    # =======================================================================
    loop = SyncHarness()
    result = sync.run_sync(reader=OutlookReader(backend="mock"))
    check("sync completes with status ok", result["status"] == "ok",
          str(result.get("error", "")))
    counts = result["counts"]
    print(f"       counts: {counts}")
    check("counts present for all three categories",
          {config.CATEGORY_AWAITING_REPLY, config.CATEGORY_OVERDUE_INBOUND,
           config.CATEGORY_MEETING} <= set(counts))
    check("awaiting reply count is 5", counts[config.CATEGORY_AWAITING_REPLY] == 5,
          str(counts[config.CATEGORY_AWAITING_REPLY]))
    check("overdue inbound count is 6", counts[config.CATEGORY_OVERDUE_INBOUND] == 6,
          str(counts[config.CATEGORY_OVERDUE_INBOUND]))
    check("meeting count is 7", counts[config.CATEGORY_MEETING] == 7,
          str(counts[config.CATEGORY_MEETING]))

    cached = db.cached_counts()
    check("results written to the SQLite cache",
          cached == {config.CATEGORY_AWAITING_REPLY: 5,
                     config.CATEGORY_OVERDUE_INBOUND: 6,
                     config.CATEGORY_MEETING: 7}, str(cached))

    last = db.last_sync()
    check("sync run recorded in the database", last["status"] == "ok")
    check("last sync carries a timestamp", bool(last["finished_at"]),
          str(last["finished_at"]))

    # =======================================================================
    print("\n15. Outlook unavailable during a sync")
    # =======================================================================
    broken = OutlookReader(application=mock_outlook.UnavailableOutlookApplication())
    failed = sync.run_sync(reader=broken)
    check("failure reported, not raised", failed["status"] == "error",
          failed.get("error", "")[:60])
    check("cached results survive a failed sync",
          db.cached_counts() == cached, str(db.cached_counts()))
    check("failed run recorded", db.last_sync()["status"] == "error")
    check("last SUCCESSFUL sync still available",
          db.last_successful_sync()["status"] == "ok")

    recovered = sync.run_sync(reader=OutlookReader(backend="mock"))
    check("next cycle recovers", recovered["status"] == "ok")

    print("\n  sync.log contents:")
    with open(config.LOG_PATH, encoding="utf-8") as handle:
        log_text = handle.read()
    for line in log_text.strip().split("\n"):
        print("       " + line)
    check("log records a successful run with counts",
          "Sync OK" in log_text and "awaiting your reply:" in log_text)
    check("log records the failure gracefully",
          "Sync skipped" in log_text and "retrying next cycle" in log_text)
    check("no traceback written for the expected failure",
          "Traceback" not in log_text)

    # =======================================================================
    print("\n9. PrivateGPT prompt generation")
    # =======================================================================
    awaiting = db.get_cached_items(config.CATEGORY_AWAITING_REPLY)
    with_attachments = next(i for i in awaiting if i["attachments"])
    prompt = prompt_builder.build(with_attachments)
    print("\n" + "\n".join("       " + line for line in prompt.split("\n")) + "\n")

    check("prompt carries the version header",
          prompt.startswith(f"=== PRIVATEGPT PROMPT (v{config.PROMPT_VERSION}) ==="))
    check("prompt ends with the end marker",
          prompt.rstrip().endswith("=== END OF PROMPT ==="))
    for heading in ("--- THREAD CONTEXT ---", "--- YOUR TASK ---",
                    "--- REQUIRED RESPONSE FORMAT ---"):
        check(f"section present: {heading}", heading in prompt)
    for field in ("Subject:", "Participants:", "Original sent date:",
                  "Days without reply:", "Attachments:", "My last message:",
                  "Their last message (if any):"):
        check(f"context field present: {field}", field in prompt)
    check("attachment names included",
          all(name in prompt for name in with_attachments["attachments"]),
          str(with_attachments["attachments"]))
    check("days without reply matches the row",
          f"Days without reply: {with_attachments['days_waiting']}" in prompt)
    check("user address appears in participants",
          config.USER_EMAIL in prompt)
    check("no reply yet renders the placeholder",
          prompt_builder.NO_REPLY_PLACEHOLDER in prompt
          or with_attachments["their_last_message"] != "")

    without = next(i for i in awaiting if not i["attachments"])
    check("no attachments renders 'None'",
          "Attachments: None" in prompt_builder.build(without))

    long_item = dict(with_attachments)
    long_item["my_last_message"] = " ".join(["word"] * 900)
    truncated = prompt_builder.build(long_item)
    check("long bodies truncated at the configured word limit",
          f"truncated at {config.PROMPT_BODY_TRUNCATE_WORDS} words" in truncated)

    check("prompt type registry exposes the follow-up template",
          prompt_builder.available_types()[0]["type"] == "followup",
          str(prompt_builder.available_types()))

    # =======================================================================
    print("\n11. parsing a correctly formatted response")
    # =======================================================================
    parsed = rr.parse(GOOD_RESPONSE)
    check("parse succeeds", parsed["ok"], str(parsed.get("detail", "")))
    fields = parsed["fields"]
    for key in ("situation", "action", "draft_reply", "urgency"):
        print(f"       {key:<12}= {str(fields[key])[:70]}")
    print(f"       {'urgency_reason':<12}= {fields['urgency_reason']}")
    check("SITUATION parsed", fields["situation"].startswith("Marcus sent"))
    check("ACTION parsed and normalised", fields["action"] == "reply")
    check("DRAFT REPLY parsed", fields["draft_reply"].startswith("Hi Lars,"))
    check("URGENCY level parsed", fields["urgency"] == "Medium")
    check("URGENCY reason parsed",
          fields["urgency_reason"].startswith("The forecast lock"))

    messy = rr.parse(MESSY_BUT_VALID)
    check("bold markers and bracketed placeholders tolerated", messy["ok"],
          str(messy.get("detail", "")))
    check("multi-line draft preserved",
          messy["ok"] and "\n" in messy["fields"]["draft_reply"])
    check("'Reply to Lars' normalises to 'reply'",
          messy["ok"] and messy["fields"]["action"] == "reply")
    check("em dash accepted as the urgency separator",
          messy["ok"] and messy["fields"]["urgency"] == "High"
          and messy["fields"]["urgency_reason"] == "Blocking the forecast lock.")

    na = rr.parse("SITUATION: Done.\nACTION: close\nDRAFT REPLY: N/A\n"
                  "URGENCY: Low - nothing outstanding.")
    check("'N/A' draft becomes an empty draft",
          na["ok"] and na["fields"]["draft_reply"] == ""
          and na["fields"]["has_draft"] is False)

    print("\n  Rendered card:")
    card = rr.build_card(with_attachments, parsed)
    print("\n" + "\n".join("       " + line
                           for line in rr.render_text_card(card).split("\n")))
    check("card carries subject and days waiting",
          card["subject"] == with_attachments["subject"]
          and card["days_waiting"] == with_attachments["days_waiting"])
    check("card exposes all four parsed fields",
          all(card[key] for key in ("situation", "action", "urgency",
                                    "urgency_reason")))

    # -- persistence --------------------------------------------------------
    db.save_ai_response(with_attachments["entry_id"], GOOD_RESPONSE,
                        parsed["fields"],
                        conversation_id=with_attachments["conversation_id"])
    saved = db.latest_ai_response(with_attachments["entry_id"])
    check("response saved against the thread EntryID",
          saved["entry_id"] == with_attachments["entry_id"])
    check("raw response saved verbatim", saved["raw_response"] == GOOD_RESPONSE)
    check("timestamp saved", bool(saved["created_at"]), saved["created_at"])
    rebuilt = rr.card_from_record(with_attachments, saved)
    check("card rebuilt from the saved row on reload",
          rebuilt["situation"] == card["situation"]
          and rebuilt["urgency"] == card["urgency"])

    # =======================================================================
    print("\n12. malformed responses")
    # =======================================================================
    for label, text in MALFORMED:
        outcome = rr.parse(text)
        check(f"rejected: {label}", outcome["ok"] is False)
        check(f"  exact error message for: {label}",
              outcome.get("error") == rr.PARSE_ERROR_MESSAGE)
        print(f"         detail -> {outcome.get('detail')}")
    check("nothing was persisted for malformed input",
          db.stats()["ai_responses"] == 1, str(db.stats()["ai_responses"]))

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("sync.py + prompt_builder.py + response_renderer.py — ALL CHECKS PASSED")
    return 0


class SyncHarness:
    """Placeholder so the loop class is imported and constructible."""

    def __init__(self):
        self.loop = sync.SyncLoop(
            interval_minutes=15,
            reader_factory=lambda: OutlookReader(backend="mock"),
        )


if __name__ == "__main__":
    sys.exit(main())
