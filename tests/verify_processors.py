"""
verify_processors.py — Phase 1 validation items 4, 5 and 6.

Checks every awaiting-reply and overdue-inbound edge case in the mock dataset
against mock_outlook.EXPECTED, then the calendar flags.

Run:  python tests\verify_processors.py
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calendar_reader  # noqa: E402
import config  # noqa: E402
import email_processor as ep  # noqa: E402
import mock_outlook  # noqa: E402
from outlook_reader import OutlookReader  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def main():
    now = datetime.now(timezone.utc)
    reader = OutlookReader(backend="mock")
    sent = reader.read_sent(now=now)
    inbox = reader.read_inbox(now=now)
    meetings = reader.read_calendar(now=now)

    expected = mock_outlook.EXPECTED

    # =======================================================================
    print("FEATURE 1 - sent emails awaiting a reply")
    # =======================================================================
    awaiting = ep.find_awaiting_reply(sent, inbox, now)
    found = {item["conversation_id"]: item for item in awaiting}

    print(f"\n  {len(awaiting)} thread(s) flagged:\n")
    print(f"    {'Conversation':<12} {'Days':>4}  {'Band':<12} {'Att':>3}  Subject")
    for item in awaiting:
        print(f"    {item['conversation_id']:<12} {item['days_waiting']:>4}  "
              f"{item['age_band']:<12} {len(item['attachments']):>3}  "
              f"{item['subject'][:44]}")
    print()

    check("exactly the expected threads are flagged",
          set(found) == set(expected["awaiting_reply"]),
          f"missing={set(expected['awaiting_reply']) - set(found)} "
          f"unexpected={set(found) - set(expected['awaiting_reply'])}")

    for conv, want in expected["awaiting_reply"].items():
        item = found.get(conv)
        if not item:
            check(f"{conv} flagged", False)
            continue
        check(f"{conv}: days waiting == {want['days']}",
              item["days_waiting"] == want["days"], str(item["days_waiting"]))
        check(f"{conv}: age band == {want['band']}",
              item["age_band"] == want["band"], item["age_band"])
        check(f"{conv}: {want['attachments']} attachment(s)",
              len(item["attachments"]) == want["attachments"],
              str(item["attachments"]))

    print("\n  Exclusions (each must NOT be flagged):")
    for conv, reason in expected["awaiting_reply_excluded"].items():
        check(f"{conv} excluded - {reason}", conv not in found)

    print("\n  Edge-case specifics:")
    check("forwarding does not resolve the thread (CONV-S3 still flagged)",
          "CONV-S3" in found and found["CONV-S3"]["forwarded"] is True)
    check("forward does not reset the clock (20 days, not 6)",
          found["CONV-S3"]["days_waiting"] == 20,
          str(found["CONV-S3"]["days_waiting"]))
    check("auto-reply does not count as a reply (CONV-S6 still flagged)",
          "CONV-S6" in found)
    check("clock runs from my LATEST send (CONV-S11: 8 days, not 25)",
          found["CONV-S11"]["days_waiting"] == 8,
          str(found["CONV-S11"]["days_waiting"]))
    check("pure BCC send never flagged (CONV-S5)", "CONV-S5" not in found)
    check("CC'd reply resolves the thread while CC_REPLY_RESOLVES_THREAD is on",
          config.CC_REPLY_RESOLVES_THREAD and "CONV-S4" not in found)

    # Flip the CC rule and confirm the alternative behaviour is one flag away.
    config.CC_REPLY_RESOLVES_THREAD = False
    alt = {i["conversation_id"] for i in ep.find_awaiting_reply(sent, inbox, now)}
    config.CC_REPLY_RESOLVES_THREAD = True
    check("with CC_REPLY_RESOLVES_THREAD off, CONV-S4 flags instead",
          "CONV-S4" in alt, str(sorted(alt)))

    print("\n  Age-band grouping:")
    grouped = ep.group_by_age_band(awaiting)
    for label, items in grouped.items():
        print(f"    {label:<12} {len(items)} item(s): "
              f"{[i['conversation_id'] for i in items]}")
    check("bands populated as expected",
          {k: len(v) for k, v in grouped.items()}
          == {"7-14 days": 3, "14-30 days": 1, "30+ days": 1},
          str({k: len(v) for k, v in grouped.items()}))
    check("every band from config is present, even if empty",
          list(grouped) == [label for label, _, _ in config.AGE_BANDS])

    # =======================================================================
    print("\n\nFEATURE 2 - overdue inbound emails")
    # =======================================================================
    overdue = ep.find_overdue_inbound(inbox, sent, now)
    found_in = {item["conversation_id"]: item for item in overdue}

    print(f"\n  {len(overdue)} email(s) flagged:\n")
    print(f"    {'Conversation':<12} {'Days':>4}  {'Priority':<9} {'Att':>3}  "
          f"{'From':<28} Subject")
    for item in overdue:
        print(f"    {item['conversation_id']:<12} {item['days_waiting']:>4}  "
              f"{item['priority']:<9} {len(item['attachments']):>3}  "
              f"{item['counterparties'][0]['address'][:27]:<28} "
              f"{item['subject'][:36]}")
    print()

    check("exactly the expected emails are flagged",
          set(found_in) == set(expected["overdue_inbound"]),
          f"missing={set(expected['overdue_inbound']) - set(found_in)} "
          f"unexpected={set(found_in) - set(expected['overdue_inbound'])}")

    for conv, want in expected["overdue_inbound"].items():
        item = found_in.get(conv)
        if not item:
            check(f"{conv} flagged", False)
            continue
        check(f"{conv}: days waiting == {want['days']}",
              item["days_waiting"] == want["days"], str(item["days_waiting"]))
        check(f"{conv}: priority == {want['priority']}",
              item["priority"] == want["priority"], item["priority"])
        check(f"{conv}: {want['attachments']} attachment(s)",
              len(item["attachments"]) == want["attachments"],
              str(item["attachments"]))

    print("\n  Exclusions (each must NOT be flagged):")
    for conv, reason in expected["overdue_inbound_excluded"].items():
        check(f"{conv} excluded - {reason}", conv not in found_in)

    print("\n  Ordering:")
    check("high priority sorts first",
          [i["priority"] for i in overdue][:2] == ["high", "high"],
          str([i["priority"] for i in overdue]))

    # =======================================================================
    print("\n\nFEATURE 3 - calendar")
    # =======================================================================
    topics = ep.conversation_topics(sent, inbox)
    processed = calendar_reader.process_calendar(meetings, now, topics)
    cal = {m["entry_id"]: m for m in processed}

    print(f"\n  {len(processed)} meeting(s) in the next "
          f"{config.CALENDAR_LOOKAHEAD_DAYS} days:\n")
    print(f"    {'ID':<8} {'Start':<18} {'Dur':>5}  {'Status':<14} {'Att':>3}  "
          f"{'Flags':<22} Subject")
    for m in processed:
        print(f"    {m['entry_id']:<8} {m['date_display']:<18} "
              f"{m['duration_display']:>5}  {m['response_label']:<14} "
              f"{m['attendee_count']:>3}  {','.join(m['flags']) or '-':<22} "
              f"{m['subject'][:34]}")
    print()

    check("only meetings inside the window are returned",
          set(cal) == set(expected["calendar_in_window"]), str(sorted(cal)))
    for entry_id, reason in expected["calendar_excluded"].items():
        check(f"{entry_id} excluded - {reason}", entry_id not in cal)

    pending = sorted(m["entry_id"] for m in processed if m["pending_response"])
    check("pending-acceptance flag",
          pending == sorted(expected["calendar_pending"]), str(pending))

    unprepared = sorted(m["entry_id"] for m in processed if m["unprepared"])
    check("within-24h-no-preparation flag",
          unprepared == sorted(expected["calendar_unprepared"]), str(unprepared))

    print("\n  Preparation heuristic detail:")
    print(f"    CAL-M4 related thread : {cal['CAL-M4']['related_thread']}")
    print(f"    CAL-M3 related thread : {cal['CAL-M3']['related_thread']}")
    check("CAL-M4 is inside 24h but linked to the contract thread",
          cal["CAL-M4"]["starts_within_warning"]
          and cal["CAL-M4"]["related_thread"] is not None
          and not cal["CAL-M4"]["unprepared"])
    check("CAL-M3 is inside 24h with no notes and no thread",
          cal["CAL-M3"]["unprepared"] and not cal["CAL-M3"]["has_notes"])
    check("CAL-M9 raises both flags",
          cal["CAL-M9"]["flags"] == ["pending", "unprepared"],
          str(cal["CAL-M9"]["flags"]))
    check("meeting with body notes is not flagged unprepared",
          cal["CAL-M5"]["has_notes"] and not cal["CAL-M5"]["unprepared"])
    check("acceptance statuses reported",
          cal["CAL-M6"]["response_label"] == "Declined"
          and cal["CAL-M5"]["response_label"] == "Organiser")
    check("attendee counts reported", cal["CAL-M1"]["attendee_count"] == 6,
          str(cal["CAL-M1"]["attendee_count"]))
    check("durations reported", cal["CAL-M2"]["duration_display"] == "1h 30m",
          cal["CAL-M2"]["duration_display"])

    summary = calendar_reader.summarise(processed)
    print(f"\n  summarise() -> {summary}")

    # =======================================================================
    print("\n\nJSON-safety of all payloads")
    # =======================================================================
    import json
    combined = awaiting + overdue + processed
    try:
        json.dumps(combined)
        check("every result dict is JSON-serialisable as-is", True,
              f"{len(combined)} rows")
    except TypeError as exc:
        check("every result dict is JSON-serialisable as-is", False, str(exc))

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("email_processor.py + calendar_reader.py — ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
