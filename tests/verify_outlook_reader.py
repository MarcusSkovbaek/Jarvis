"""
verify_outlook_reader.py — Phase 1 validation items 2 and 3.

Proves the mock mailbox exposes every required edge case, and that
outlook_reader normalises it correctly through the same code path the real
COM backend will use.

Run:  python tests\verify_outlook_reader.py
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import mock_outlook  # noqa: E402
from outlook_reader import OutlookReader, OutlookUnavailable, parse_headers  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def by_entry(records):
    return {r["entry_id"]: r for r in records}


def main():
    now = datetime.now(timezone.utc)
    mock_outlook.reset_call_log()
    reader = OutlookReader(backend="mock")

    # -- 1. raw mock data ---------------------------------------------------
    print("1. mock mailbox contents")
    data = mock_outlook.build_dataset()
    print(f"       Sent Items : {len(data['sent'])}")
    print(f"       Inbox      : {len(data['inbox'])}")
    print(f"       Calendar   : {len(data['calendar'])}")
    check("sent items present", len(data["sent"]) >= 12)
    check("inbox items present", len(data["inbox"]) >= 12)
    check("calendar items present", len(data["calendar"]) >= 8)

    # -- 2. reads -----------------------------------------------------------
    print("\n2. outlook_reader pulls from each source")
    sent = reader.read_sent(now=now)
    inbox = reader.read_inbox(now=now)
    calendar = reader.read_calendar(now=now)
    print(f"       read_sent()     -> {len(sent)} items")
    print(f"       read_inbox()    -> {len(inbox)} items")
    print(f"       read_calendar() -> {len(calendar)} meetings")
    check("read_sent returns items", len(sent) == len(data["sent"]),
          f"{len(sent)} vs {len(data['sent'])}")
    check("read_inbox returns items", len(inbox) == len(data["inbox"]),
          f"{len(inbox)} vs {len(data['inbox'])}")
    check("read_calendar applies the 7-day window",
          len(calendar) == len(mock_outlook.EXPECTED["calendar_in_window"]),
          f"{len(calendar)} meetings")

    sent_map, inbox_map = by_entry(sent), by_entry(inbox)
    cal_map = by_entry(calendar)

    # -- 3. sample records --------------------------------------------------
    print("\n3. sample normalised records")
    sample = sent_map["SENT-S1"]
    for key in ("entry_id", "conversation_id", "subject", "sender_address",
                "sent_on", "attachments", "is_from_user"):
        print(f"       sent.{key:<17}= {sample[key]}")
    print()
    sample_in = inbox_map["INBOX-I1"]
    for key in ("entry_id", "conversation_id", "subject", "sender_address",
                "received_time", "unread"):
        print(f"       inbox.{key:<16}= {sample_in[key]}")
    print()
    sample_cal = cal_map["CAL-M2"]
    for key in ("entry_id", "subject", "start", "duration_minutes", "organizer",
                "response_label", "attendee_count"):
        print(f"       cal.{key:<18}= {sample_cal[key]}")

    # -- 4. normalisation details ------------------------------------------
    print("\n4. normalisation")
    check("sender address lowercased and SMTP",
          sample["sender_address"] == config.USER_EMAIL.lower())
    check("is_from_user set for own sends", sample["is_from_user"] is True)
    check("is_from_user false for inbound", sample_in["is_from_user"] is False)
    check("datetimes normalised to aware UTC",
          sample["sent_on"].tzinfo is not None
          and sample["sent_on"].utcoffset().total_seconds() == 0,
          str(sample["sent_on"]))
    check("To recipients captured",
          [r["address"] for r in sample["to"]] == ["lars.petersen@nordvind.dk"])
    check("attachment filenames captured",
          sample["attachments"] == ["budget_q3_v2.xlsx", "assumptions.docx"])
    check("ConversationTopic strips RE:/SV: prefixes",
          inbox_map["INBOX-S2R"]["conversation_topic"] == "Site survey scheduling",
          inbox_map["INBOX-S2R"]["conversation_topic"])

    # -- 5. edge cases present in the mock ---------------------------------
    print("\n5. required edge cases present in the mock data")
    bcc_only = sent_map["SENT-S5"]
    check("pure BCC send exists (no To, BCC present)",
          bcc_only["to"] == [] and len(bcc_only["bcc"]) == 1)

    auto = inbox_map["INBOX-S6A"]
    check("auto-reply has OOF message class",
          "IPM.Note.Rules" in auto["message_class"], auto["message_class"])
    check("auto-reply subject prefix present",
          auto["subject"].lower().startswith("automatic reply:"))
    check("auto-reply headers parsed",
          auto["headers"].get("auto-submitted") == "auto-replied",
          str(auto["headers"]))

    news = inbox_map["INBOX-I7"]
    check("mailing list exposes List-Unsubscribe",
          "list-unsubscribe" in news["headers"], str(list(news["headers"])))

    fwd = sent_map["SENT-S3F"]
    check("forward exists on the same ConversationID",
          fwd["conversation_id"] == sent_map["SENT-S3"]["conversation_id"]
          and fwd["subject"].lower().startswith("vs:"))

    cc_reply = inbox_map["INBOX-S4R"]
    check("CC-only reply case exists (replier was CC'd on the original)",
          cc_reply["sender_address"] == "jens.moeller@kbh-arkitekter.dk"
          and [r["address"] for r in sent_map["SENT-S4"]["cc"]]
          == ["jens.moeller@kbh-arkitekter.dk"])

    check("multi-attachment inbound exists",
          len(inbox_map["INBOX-I11"]["attachments"]) == 2)
    check("CC-only inbound exists (user not in To)",
          config.USER_EMAIL not in
          [r["address"] for r in inbox_map["INBOX-I3"]["to"]]
          and config.USER_EMAIL in
          [r["address"] for r in inbox_map["INBOX-I3"]["cc"]])

    statuses = {m["response_label"] for m in calendar}
    check("calendar covers multiple acceptance states",
          {"Accepted", "Not responded", "Declined", "Organiser"} <= statuses,
          str(sorted(statuses)))
    within_24h = [m for m in calendar
                  if (m["start"] - now).total_seconds() / 3600 <= 24]
    check("calendar has meetings inside 24 hours", len(within_24h) >= 2,
          str([m["entry_id"] for m in within_24h]))

    # -- 6. window filtering ------------------------------------------------
    print("\n6. calendar window")
    check("past meeting excluded", "CAL-M8" not in cal_map)
    check("meeting beyond 7 days excluded", "CAL-M7" not in cal_map)
    check("meetings sorted by start time",
          [m["entry_id"] for m in calendar]
          == mock_outlook.EXPECTED["calendar_in_window"],
          str([m["entry_id"] for m in calendar]))

    # -- 7. header parser ---------------------------------------------------
    print("\n7. header parser")
    parsed = parse_headers(
        "Subject: Test\r\nList-Unsubscribe: <https://x/1>,\r\n <mailto:u@x>\r\n"
        "Received: from a\r\nReceived: from b\r\n"
    )
    check("folded continuation lines joined",
          parsed["list-unsubscribe"] == "<https://x/1>, <mailto:u@x>",
          parsed["list-unsubscribe"])
    check("repeated headers combined",
          parsed["received"] == "from a, from b", parsed["received"])
    check("missing header property yields empty dict", parse_headers(None) == {})

    # -- 8. actions (read-only except Display / ReplyAll) -------------------
    print("\n8. open item and reply-all draft")
    mock_outlook.reset_call_log()
    reader.open_item("SENT-S1")
    check("Display() called with the right EntryID",
          mock_outlook.DISPLAYED[-1]["entry_id"] == "SENT-S1",
          str(mock_outlook.DISPLAYED[-1]))
    reader.create_reply_all_draft("SENT-S1", "Drafted follow-up text.")
    draft = mock_outlook.DRAFTS[-1]
    check("ReplyAll draft created", draft.Subject.lower().startswith("re:"),
          draft.Subject)
    check("draft body pre-filled",
          draft.Body.startswith("Drafted follow-up text."), draft.Body[:40])
    check("draft displayed, never sent", draft.Displayed and not draft.Sent)

    try:
        reader.open_item("DOES-NOT-EXIST")
        check("unknown EntryID raises OutlookUnavailable", False)
    except OutlookUnavailable as exc:
        check("unknown EntryID raises OutlookUnavailable", True, str(exc)[:60])

    # -- 9. Outlook unavailable --------------------------------------------
    print("\n9. Outlook unavailable")
    broken = OutlookReader(application=mock_outlook.UnavailableOutlookApplication())
    try:
        broken.read_sent(now=now)
        check("raises OutlookUnavailable instead of crashing", False)
    except OutlookUnavailable as exc:
        check("raises OutlookUnavailable instead of crashing", True, str(exc)[:60])

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("mock_outlook.py + outlook_reader.py — ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
