"""
probe.py — WORK PC ONLY. Read-only probe against the real Outlook mailbox.

Connects to the live Outlook profile via win32com and prints exactly what
Jarvis sees: raw counts, a sample item from each folder, both email features,
the calendar, real EntryIDs and a real generated prompt.

In Outlook it opens nothing, sends nothing, deletes nothing and modifies
nothing — it only reads. On disk it creates no database; the one file it
touches is sync.log, where the run is recorded.

Reachable two ways, so it works with or without Python installed:

    Jarvis.exe --probe                  (compiled, nothing else needed)
    python tests\\phase2_probe.py        (from source)

Add --full for more sample rows, --redact to mask addresses and subjects
before sharing the output.
"""

from datetime import datetime, timezone

import config

_REDACT = False


def mask(text, keep=3):
    if not _REDACT or not text:
        return text
    text = str(text)
    if "@" in text:
        local, _, domain = text.partition("@")
        return f"{local[:keep]}***@{domain[:keep]}***"
    return text[:keep] + "*" * max(0, len(text) - keep)


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def run(full=False, redact=False, backend="com"):
    """Run the probe. Returns a process exit code."""
    global _REDACT
    _REDACT = redact

    # Imported here so setting the backend takes effect first.
    config.OUTLOOK_BACKEND = backend

    import calendar_reader
    import email_processor as ep
    import prompt_builder
    from outlook_reader import OutlookReader, OutlookUnavailable

    sample_count = 5 if full else 2
    now = datetime.now(timezone.utc)

    print(f"Jarvis Phase 2 probe - {now.astimezone():%Y-%m-%d %H:%M:%S}")
    print(f"Identity        : {config.USER_EMAIL}")
    print(f"Backend         : {backend}")
    print(f"Sent lookback   : {config.SENT_LOOKBACK_DAYS} days")
    print(f"Inbox lookback  : {config.INBOX_LOOKBACK_DAYS} days")
    print(f"Calendar window : {config.CALENDAR_LOOKAHEAD_DAYS} days")
    if redact:
        print("Redaction       : ON (addresses and subjects masked)")

    reader = OutlookReader(backend=backend)

    # -- 1. connection and raw pull -----------------------------------------
    rule("1. Connect to Outlook and pull raw data")
    try:
        reader.connect()
        print("  Connected to the Outlook MAPI session.")
    except OutlookUnavailable as exc:
        print(f"  FAILED: {exc}")
        print("\n  Is Outlook running? Start it, let the profile finish "
              "loading, then run this again.")
        return 1

    try:
        sent = reader.read_sent(now=now)
        inbox = reader.read_inbox(now=now)
        meetings = reader.read_calendar(now=now)
    except OutlookUnavailable as exc:
        print(f"  FAILED during read: {exc}")
        return 1

    print(f"\n  Sent Items : {len(sent)} items")
    print(f"  Inbox      : {len(inbox)} items")
    print(f"  Calendar   : {len(meetings)} meetings in the next "
          f"{config.CALENDAR_LOOKAHEAD_DAYS} days")

    if not sent and not inbox:
        print("\n  WARNING: both folders came back empty. Either the lookback "
              "windows are too short, or Outlook returned nothing. Check that "
              "the correct profile is loaded.")

    def show_mail(record, prefix="    "):
        print(f"{prefix}EntryID        : {str(record['entry_id'])[:48]}...")
        print(f"{prefix}ConversationID : {str(record['conversation_id'])[:48]}")
        print(f"{prefix}Subject        : {mask(record['subject'], 12)}")
        print(f"{prefix}From           : {mask(record['sender_address'])}")
        print(f"{prefix}To             : "
              f"{[mask(p['address']) for p in record['to']]}")
        print(f"{prefix}CC             : "
              f"{[mask(p['address']) for p in record['cc']]}")
        print(f"{prefix}SentOn         : {record['sent_on']}")
        print(f"{prefix}ReceivedTime   : {record['received_time']}")
        print(f"{prefix}Attachments    : "
              f"{[mask(a, 6) for a in record['attachments']]}")
        print(f"{prefix}MessageClass   : {record['message_class']}")
        print(f"{prefix}Headers found  : {len(record['headers'])} "
              f"({sorted(record['headers'])[:6]})")
        print(f"{prefix}is_from_user   : {record['is_from_user']}")
        print()

    rule("Sample Sent Items")
    for record in sent[:sample_count]:
        show_mail(record)
    rule("Sample Inbox items")
    for record in inbox[:sample_count]:
        show_mail(record)

    # -- identity sanity check ---------------------------------------------
    rule("Identity sanity check")
    from_user = [r for r in sent if r["is_from_user"]]
    print(f"  Sent items recognised as sent by {config.USER_EMAIL}: "
          f"{len(from_user)} of {len(sent)}")
    if sent and not from_user:
        print("  WARNING: no sent item matched USER_EMAIL. The addresses are "
              "probably coming back as Exchange X500 DNs that could not be "
              "resolved to SMTP. Sample sender values:")
        for record in sent[:3]:
            print(f"    {record['sender_address']!r}")
        print("  Feature 1 will find nothing until this resolves correctly.")
        print("  Send these sample values back - that is the fix I need.")

    headers_seen = sum(1 for r in inbox if r["headers"])
    print(f"  Inbox items exposing transport headers: {headers_seen} of "
          f"{len(inbox)}")
    if inbox and not headers_seen:
        print("  NOTE: no transport headers available. Mailing-list detection "
              "falls back to sender patterns only; auto-reply detection still "
              "works via message class and subject prefix.")

    # -- 2 and 3. the two email features ------------------------------------
    rule("2. Overdue inbound emails (Feature 2)")
    overdue = ep.find_overdue_inbound(inbox, sent, now)
    print(f"  {len(overdue)} flagged\n")
    print(f"  {'Days':>4}  {'Priority':<9} {'Att':>3}  {'From':<34} Subject")
    for item in overdue:
        print(f"  {item['days_waiting']:>4}  {item['priority']:<9} "
              f"{len(item['attachments']):>3}  "
              f"{mask(item['counterparties'][0]['address'])[:33]:<34} "
              f"{mask(item['subject'], 12)[:40]}")

    rule("3. Sent emails awaiting a reply (Feature 1), by age band")
    awaiting = ep.find_awaiting_reply(sent, inbox, now)
    grouped = ep.group_by_age_band(awaiting)
    print(f"  {len(awaiting)} flagged")
    for label, items in grouped.items():
        print(f"\n  --- {label}: {len(items)} ---")
        for item in items:
            print(f"    {item['days_waiting']:>4} days  "
                  f"{'[attach]' if item['has_attachments'] else '        '} "
                  f"{mask(item['counterparty_display'])[:30]:<32} "
                  f"{mask(item['subject'], 12)[:40]}")

    # -- 4. calendar --------------------------------------------------------
    rule("4. Upcoming meetings (Feature 3)")
    topics = ep.conversation_topics(sent, inbox)
    processed = calendar_reader.process_calendar(meetings, now, topics)
    print(f"  {len(processed)} meetings\n")
    print(f"  {'Start':<18} {'Dur':>6}  {'Status':<15} {'Att':>3}  "
          f"{'Flags':<22} Subject")
    for meeting in processed:
        print(f"  {meeting['date_display']:<18} "
              f"{meeting['duration_display']:>6}  "
              f"{meeting['response_label']:<15} {meeting['attendee_count']:>3}  "
              f"{','.join(meeting['flags']) or '-':<22} "
              f"{mask(meeting['subject'], 12)[:34]}")
    print(f"\n  {calendar_reader.summarise(processed)}")

    # -- 5. EntryIDs for the manual click tests -----------------------------
    rule("5. EntryIDs for the manual open/reply tests")
    print("  Use these to confirm the right item opens when you click a row.\n")
    for label, items in (("overdue inbound", overdue),
                         ("awaiting reply", awaiting)):
        if items:
            item = items[0]
            print(f"  {label}:")
            print(f"    subject      : {mask(item['subject'], 12)}")
            print(f"    entry_id     : {item['entry_id']}")
            print(f"    reply_to_id  : {item['reply_entry_id']}")
    if processed:
        print("  meeting:")
        print(f"    subject      : {mask(processed[0]['subject'], 12)}")
        print(f"    entry_id     : {processed[0]['entry_id']}")

    # -- 6. a real prompt ---------------------------------------------------
    rule("6. A real PrivateGPT prompt")
    source = None
    for item in awaiting + overdue:
        if item["attachments"]:
            source = item
            break
    source = source or (awaiting[0] if awaiting else
                        (overdue[0] if overdue else None))
    if source is None:
        print("  Nothing flagged, so there is no prompt to build.")
    elif redact:
        print("  (skipped under --redact: the prompt contains message bodies)")
    else:
        print(prompt_builder.build(source))

    reader.close()
    print(f"\n{'=' * 72}")
    print("Probe complete. In Outlook, nothing was opened, sent, deleted or "
          "modified - this was a read-only pass.")
    print("Paste this output back into the Claude session.")
    return 0
