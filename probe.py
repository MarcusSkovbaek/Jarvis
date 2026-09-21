"""
probe.py — WORK PC ONLY. Read-only probe against the real Outlook mailbox.

Connects to the live Outlook profile via win32com and reports exactly what
Jarvis sees: raw counts, a sample item from each folder, both email features,
the calendar, item ids and the generated prompt.

In Outlook it opens nothing, sends nothing, deletes nothing and modifies
nothing — it only reads. On disk it writes two files next to itself, both
local and both yours: the report (probe-output.txt by default) and sync.log.
It makes no network connection of any kind.

Reachable two ways, so it works with or without Python installed:

    Jarvis.exe --probe                  (compiled, nothing else needed)
    python tests\\phase2_probe.py        (from source)

REDACTION IS ON BY DEFAULT. Addresses, names, subjects, filenames, message
bodies and item ids are replaced by stable stand-ins (see redaction.py), so
the report can be shared with someone helping with the build. Pass
--no-redact for the unredacted version, which is for your eyes only. Add
--full for more sample rows.
"""

import io
import os
import sys
from datetime import datetime, timezone

import config
from redaction import Redactor

DEFAULT_REPORT_NAME = "probe-output.txt"

_out = None       # the open report file, or None
_redactor = None  # module-level so the print helpers can reach it


def emit(line=""):
    """Print to the console and to the report file."""
    print(line)
    if _out is not None:
        _out.write(str(line) + "\n")


def rule(title):
    emit(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _report_dir():
    """Next to the .exe when frozen, next to the sources otherwise."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _header(redact, backend, now):
    emit(f"Jarvis Phase 2 probe - {now.astimezone():%Y-%m-%d %H:%M:%S}")
    emit(f"Backend         : {backend}")
    emit(f"Identity        : {_redactor.address(config.USER_EMAIL)}")
    emit(f"Sent lookback   : {config.SENT_LOOKBACK_DAYS} days")
    emit(f"Inbox lookback  : {config.INBOX_LOOKBACK_DAYS} days")
    emit(f"Calendar window : {config.CALENDAR_LOOKAHEAD_DAYS} days")
    emit()
    if redact:
        emit("REDACTED REPORT - safe to share.")
        emit("  Addresses, names, subjects, attachment filenames, message")
        emit("  bodies and Outlook item ids are replaced by stand-ins such as")
        emit("  person3@domain2.example and <subject, 22 chars>. The same real")
        emit("  value always maps to the same stand-in within this run, so the")
        emit("  relationships stay checkable; the mapping is held in memory")
        emit("  only and is never written down.")
        emit()
        emit("  What is still in this file, because the logic cannot be")
        emit("  checked without it: counts, dates and times, day arithmetic,")
        emit("  reply/forward subject prefixes (SV:, RE:), attachment file")
        emit("  extensions, message classes, transport header names, meeting")
        emit("  response status, and the length of ids and subjects.")
        emit()
        emit("  Read it before sending it on. Run with --no-redact to see the")
        emit("  same report unredacted, for your eyes only.")
    else:
        emit("UNREDACTED REPORT - contains real mail data. DO NOT SHARE.")
        emit("  Run without --no-redact to produce a shareable version.")


def run(full=False, redact=True, backend="com", report_path=None):
    """Run the probe. Returns a process exit code."""
    global _out, _redactor

    # Imported here so setting the backend takes effect first.
    config.OUTLOOK_BACKEND = backend

    import calendar_reader
    import email_processor as ep
    import prompt_builder
    from outlook_reader import OutlookReader, OutlookUnavailable

    _redactor = Redactor(enabled=redact, user_email=config.USER_EMAIL)
    red = _redactor

    if report_path is None:
        report_path = os.path.join(_report_dir(), DEFAULT_REPORT_NAME)

    try:
        _out = io.open(report_path, "w", encoding="utf-8")
    except OSError as exc:
        print(f"  (could not open {report_path} for writing: {exc};"
              " console only)")
        _out = None

    try:
        return _run(full, redact, backend, report_path, red,
                    calendar_reader, ep, prompt_builder,
                    OutlookReader, OutlookUnavailable)
    finally:
        if _out is not None:
            _out.close()
            _out = None


def _run(full, redact, backend, report_path, red,
         calendar_reader, ep, prompt_builder,
         OutlookReader, OutlookUnavailable):
    sample_count = 5 if full else 2
    now = datetime.now(timezone.utc)

    _header(redact, backend, now)

    reader = OutlookReader(backend=backend)

    # -- 1. connection and raw pull -----------------------------------------
    rule("1. Connect to Outlook and pull raw data")
    try:
        reader.connect()
        emit("  Connected to the Outlook MAPI session.")
    except OutlookUnavailable as exc:
        emit(f"  FAILED: {exc}")
        emit("\n  Is Outlook running? Start it, let the profile finish "
             "loading, then run this again.")
        return 1

    try:
        sent = reader.read_sent(now=now)
        inbox = reader.read_inbox(now=now)
        meetings = reader.read_calendar(now=now)
    except OutlookUnavailable as exc:
        emit(f"  FAILED during read: {exc}")
        return 1

    emit(f"\n  Sent Items : {len(sent)} items")
    emit(f"  Inbox      : {len(inbox)} items")
    emit(f"  Calendar   : {len(meetings)} meetings in the next "
         f"{config.CALENDAR_LOOKAHEAD_DAYS} days")

    if not sent and not inbox:
        emit("\n  WARNING: both folders came back empty. Either the lookback "
             "windows are too short, or Outlook returned nothing. Check that "
             "the correct profile is loaded.")

    def show_mail(record, prefix="    "):
        emit(f"{prefix}EntryID        : {red.entry_id(record['entry_id'])}")
        emit(f"{prefix}ConversationID : "
             f"{red.entry_id(record['conversation_id'])}")
        emit(f"{prefix}Subject        : {red.subject(record['subject'])}")
        emit(f"{prefix}From           : {red.address(record['sender_address'])}")
        emit(f"{prefix}To             : "
             f"{[red.address(p['address']) for p in record['to']]}")
        emit(f"{prefix}CC             : "
             f"{[red.address(p['address']) for p in record['cc']]}")
        emit(f"{prefix}SentOn         : {record['sent_on']}")
        emit(f"{prefix}ReceivedTime   : {record['received_time']}")
        emit(f"{prefix}Attachments    : "
             f"{[red.filename(a) for a in record['attachments']]}")
        emit(f"{prefix}MessageClass   : {record['message_class']}")
        emit(f"{prefix}Headers found  : {len(record['headers'])} "
             f"({sorted(record['headers'])[:6]})")
        emit(f"{prefix}is_from_user   : {record['is_from_user']}")
        emit()

    rule("Sample Sent Items")
    for record in sent[:sample_count]:
        show_mail(record)
    rule("Sample Inbox items")
    for record in inbox[:sample_count]:
        show_mail(record)

    # -- identity sanity check ---------------------------------------------
    rule("Identity sanity check")
    from_user = [r for r in sent if r["is_from_user"]]
    emit(f"  Sent items recognised as sent by "
         f"{red.address(config.USER_EMAIL)}: {len(from_user)} of {len(sent)}")
    if sent and not from_user:
        emit("  WARNING: no sent item matched USER_EMAIL. The addresses are "
             "probably coming back as Exchange X500 DNs that could not be "
             "resolved to SMTP. Structure of the sender values:")
        for record in sent[:3]:
            value = record["sender_address"]
            emit(f"    {red.dn(value) if redact else repr(value)}"
                 f"   (len {len(str(value or ''))}, "
                 f"{'has @' if '@' in str(value or '') else 'no @'})")
        emit("  Feature 1 will find nothing until this resolves correctly.")
        emit("  Send this section back - the key names and lengths are the "
             "fix I need, and they contain no names or addresses.")

    headers_seen = sum(1 for r in inbox if r["headers"])
    emit(f"  Inbox items exposing transport headers: {headers_seen} of "
         f"{len(inbox)}")
    if inbox and not headers_seen:
        emit("  NOTE: no transport headers available. Mailing-list detection "
             "falls back to sender patterns only; auto-reply detection still "
             "works via message class and subject prefix.")

    # -- 2 and 3. the two email features ------------------------------------
    rule("2. Overdue inbound emails (Feature 2)")
    overdue = ep.find_overdue_inbound(inbox, sent, now)
    emit(f"  {len(overdue)} flagged\n")
    emit(f"  {'Days':>4}  {'Priority':<9} {'Att':>3}  {'From':<28} Subject")
    for item in overdue:
        emit(f"  {item['days_waiting']:>4}  {item['priority']:<9} "
             f"{len(item['attachments']):>3}  "
             f"{red.address(item['counterparties'][0]['address'])[:27]:<28} "
             f"{red.subject(item['subject'])}")

    rule("3. Sent emails awaiting a reply (Feature 1), by age band")
    awaiting = ep.find_awaiting_reply(sent, inbox, now)
    grouped = ep.group_by_age_band(awaiting)
    emit(f"  {len(awaiting)} flagged")
    for label, items in grouped.items():
        emit(f"\n  --- {label}: {len(items)} ---")
        for item in items:
            emit(f"    {item['days_waiting']:>4} days  "
                 f"{'[attach]' if item['has_attachments'] else '        '} "
                 f"{red.person(item['counterparty_display'])[:18]:<20} "
                 f"{red.subject(item['subject'])}")

    # -- 4. calendar --------------------------------------------------------
    rule("4. Upcoming meetings (Feature 3)")
    topics = ep.conversation_topics(sent, inbox)
    processed = calendar_reader.process_calendar(meetings, now, topics)
    emit(f"  {len(processed)} meetings\n")
    emit(f"  {'Start':<18} {'Dur':>6}  {'Status':<15} {'Att':>3}  "
         f"{'Flags':<22} Subject")
    for meeting in processed:
        emit(f"  {meeting['date_display']:<18} "
             f"{meeting['duration_display']:>6}  "
             f"{meeting['response_label']:<15} {meeting['attendee_count']:>3}  "
             f"{','.join(meeting['flags']) or '-':<22} "
             f"{red.subject(meeting['subject'])}")
    emit(f"\n  {calendar_reader.summarise(processed)}")

    # -- 5. item ids for the manual click tests -----------------------------
    rule("5. Item ids for the manual open/reply tests")
    emit("  Ids are shown by shape only. To confirm the right item opens when")
    emit("  you click a row, compare the subject in Outlook against the row")
    emit("  you clicked - the id itself is not something you need to read.")
    emit()
    for label, items in (("overdue inbound", overdue),
                         ("awaiting reply", awaiting)):
        if items:
            item = items[0]
            emit(f"  {label}:")
            emit(f"    subject      : {red.subject(item['subject'])}")
            emit(f"    entry_id     : {red.entry_id(item['entry_id'])}")
            emit(f"    reply_to_id  : {red.entry_id(item['reply_entry_id'])}")
    if processed:
        emit("  meeting:")
        emit(f"    subject      : {red.subject(processed[0]['subject'])}")
        emit(f"    entry_id     : {red.entry_id(processed[0]['entry_id'])}")

    # -- 6. a real prompt ---------------------------------------------------
    rule("6. The generated PrivateGPT prompt")
    source = None
    for item in awaiting + overdue:
        if item["attachments"]:
            source = item
            break
    source = source or (awaiting[0] if awaiting else
                        (overdue[0] if overdue else None))
    if source is None:
        emit("  Nothing flagged, so there is no prompt to build.")
    else:
        if redact:
            emit("  Built from a real flagged item, with every value")
            emit("  substituted. The template itself is verbatim, so the")
            emit("  structure can be checked without exposing the thread.")
            emit()
        emit(prompt_builder.build(_redacted_item(source, red) if redact
                                  else source))

    reader.close()
    emit(f"\n{'=' * 72}")
    emit("Probe complete. In Outlook, nothing was opened, sent, deleted or "
         "modified - this was a read-only pass.")
    emit(f"{red.summary()}")
    emit("No network connection was made. Nothing was sent anywhere; this "
         "report is a local file and only you can pass it on.")
    if _out is not None:
        print(f"\nReport written to: {report_path}")
    return 0


def _redacted_item(item, red):
    """A copy of a dashboard row with every value replaced by a stand-in.

    Built so the prompt can be shown from real data without the data: the
    template, field order and formatting are untouched, only the values move.
    """
    copy = dict(item)
    copy["user_email"] = red.address(config.USER_EMAIL)
    copy["subject"] = red.subject(item.get("subject"))
    copy["my_last_message"] = red.body(item.get("my_last_message"))
    copy["their_last_message"] = red.body(item.get("their_last_message"))
    copy["attachments"] = [red.filename(a) for a in item.get("attachments") or []]
    copy["participants"] = [
        red.participant(None, address)
        for address in _participant_addresses(item)
    ]
    copy["counterparties"] = [
        {"name": red.person(p.get("name") or p.get("address")),
         "address": red.address(p.get("address"))}
        for p in item.get("counterparties") or []
    ]
    return copy


def _participant_addresses(item):
    """Every address on the row, however the row happens to carry them."""
    found = []
    for person in item.get("counterparties") or []:
        if person.get("address"):
            found.append(person["address"])
    for entry in item.get("participants") or []:
        # "Name <address>" — take what is inside the angle brackets.
        _, _, tail = str(entry).partition("<")
        address = tail.partition(">")[0].strip()
        if address:
            found.append(address)
    seen, unique = set(), []
    for address in found:
        if address.lower() not in seen:
            seen.add(address.lower())
            unique.append(address)
    return unique
