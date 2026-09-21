"""
mock_outlook.py — a stand-in for the win32com Outlook object model.

Phase 1 runs entirely against this module: it exposes the same properties,
1-based collections and PropertyAccessor behaviour that outlook_reader.py uses
against the real COM API, so switching to the live mailbox in Phase 2 changes
only config.OUTLOOK_BACKEND — no code changes anywhere downstream.

The dataset deliberately covers every edge case the detection logic must get
right: forwarded threads, BCC-only sends, CC-only replies, auto-replies,
mailing lists, excluded domains, attachments, all three age bands, meetings in
every acceptance state, and meetings inside 24 hours with and without an
associated thread.

Nothing here touches a real mailbox, and nothing here is used when
config.OUTLOOK_BACKEND == "com".
"""

from datetime import datetime, timedelta

import config

# Every Display() call and every draft created via ReplyAll() is recorded here
# so Phase 1 can verify "clicking opens the item in Outlook" without Outlook.
DISPLAYED = []
DRAFTS = []


def reset_call_log():
    DISPLAYED.clear()
    DRAFTS.clear()


class MockComError(Exception):
    """Stands in for pywintypes.com_error on a missing MAPI property."""


# ---------------------------------------------------------------------------
# Time helpers — the dataset is built relative to "now" so the age bands stay
# correct whenever it is run.
# ---------------------------------------------------------------------------

def _now():
    # win32com returns timezone-aware datetimes; the mock does the same.
    return datetime.now().astimezone()


def days_ago(days, hour=9, minute=30):
    base = _now() - timedelta(days=days)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def hours_ahead(hours, minute=0):
    return (_now() + timedelta(hours=hours)).replace(
        minute=minute, second=0, microsecond=0
    )


def days_ahead(days, hour=10, minute=0):
    return (_now() + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


# ---------------------------------------------------------------------------
# COM-shaped collections (1-based indexing, .Count, .Item(i))
# ---------------------------------------------------------------------------

class _Collection:
    def __init__(self, members):
        self._members = list(members)

    @property
    def Count(self):
        return len(self._members)

    def Item(self, index):
        # Outlook collections are 1-based; index 0 is an error in COM too.
        if index < 1 or index > len(self._members):
            raise MockComError(f"Array index out of bounds: {index}")
        return self._members[index - 1]

    def __call__(self, index):
        # win32com allows collection(i) as shorthand for collection.Item(i).
        return self.Item(index)

    def __len__(self):
        return len(self._members)

    def __iter__(self):
        return iter(self._members)


class MockRecipient:
    def __init__(self, name, address, recipient_type=config.OL_TO):
        self.Name = name
        self.Address = address
        self.Type = recipient_type
        self.Resolved = True
        self.AddressEntry = _AddressEntry(name, address)

    def __repr__(self):
        kind = {config.OL_TO: "To", config.OL_CC: "CC", config.OL_BCC: "BCC"}
        return f"<Recipient {kind.get(self.Type, '?')} {self.Address}>"


class _AddressEntry:
    def __init__(self, name, address):
        self.Name = name
        self.Address = address
        self.Type = "SMTP"

    def GetExchangeUser(self):
        return None


class MockAttachment:
    def __init__(self, filename, size=48000):
        self.FileName = filename
        self.DisplayName = filename
        self.Size = size
        self.Type = 1  # olByValue


class MockPropertyAccessor:
    """Mimics item.PropertyAccessor.GetProperty(schema_name)."""

    def __init__(self, headers=None, smtp_address=None):
        self._headers = dict(headers or {})
        self._smtp_address = smtp_address

    @property
    def header_text(self):
        if not self._headers:
            return ""
        return "\r\n".join(f"{k}: {v}" for k, v in self._headers.items())

    def GetProperty(self, schema_name):
        if schema_name == config.PR_TRANSPORT_MESSAGE_HEADERS:
            if not self._headers:
                # Real Outlook raises when the property is absent (e.g. an item
                # that never traversed a transport). Downstream code must cope.
                raise MockComError("Property PR_TRANSPORT_MESSAGE_HEADERS not found")
            return self.header_text
        if schema_name in (config.PR_SMTP_ADDRESS, config.PR_SENDER_SMTP_ADDRESS):
            if not self._smtp_address:
                raise MockComError("Property PR_SMTP_ADDRESS not found")
            return self._smtp_address
        raise MockComError(f"Unknown property {schema_name}")


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

class MockMailItem:
    Class = 43  # olMail

    def __init__(self, entry_id, conversation_id, subject, sender_name,
                 sender_address, to=(), cc=(), bcc=(), sent_on=None,
                 received_time=None, body="", attachments=(), headers=None,
                 message_class="IPM.Note", unread=False, folder="Inbox"):
        self.EntryID = entry_id
        self.ConversationID = conversation_id
        self.ConversationTopic = _strip_prefixes(subject)
        self.ConversationIndex = entry_id
        self.Subject = subject
        self.SenderName = sender_name
        self.SenderEmailAddress = sender_address
        self.SenderEmailType = "SMTP"
        self.MessageClass = message_class
        self.Body = body
        self.HTMLBody = f"<html><body>{body}</body></html>"
        self.UnRead = unread
        self.Importance = 1
        self.Categories = ""
        self.Folder = folder  # mock-only: which folder the item lives in

        self.SentOn = sent_on
        self.ReceivedTime = received_time or sent_on
        self.CreationTime = sent_on

        recipients = []
        recipients += [MockRecipient(n, a, config.OL_TO) for n, a in to]
        recipients += [MockRecipient(n, a, config.OL_CC) for n, a in cc]
        recipients += [MockRecipient(n, a, config.OL_BCC) for n, a in bcc]
        self.Recipients = _Collection(recipients)

        self.To = "; ".join(a for _, a in to)
        self.CC = "; ".join(a for _, a in cc)
        self.BCC = "; ".join(a for _, a in bcc)

        self.Attachments = _Collection(
            [a if isinstance(a, MockAttachment) else MockAttachment(a)
             for a in attachments]
        )
        self.PropertyAccessor = MockPropertyAccessor(headers, sender_address)

    def Display(self):
        DISPLAYED.append({"entry_id": self.EntryID, "subject": self.Subject,
                          "kind": "mail"})
        return True

    def ReplyAll(self):
        draft = MockDraft(self)
        DRAFTS.append(draft)
        return draft

    def __repr__(self):
        return f"<MailItem {self.EntryID} {self.Subject!r}>"


class MockDraft:
    """The object returned by MailItem.ReplyAll() — never sent by Jarvis."""

    def __init__(self, parent):
        self.EntryID = f"{parent.EntryID}-DRAFT"
        self.ConversationID = parent.ConversationID
        self.Subject = parent.Subject if parent.Subject.lower().startswith("re:") \
            else f"RE: {parent.Subject}"
        self.To = parent.To
        self.CC = parent.CC
        self.Body = ""
        self.HTMLBody = ""
        self.Displayed = False
        self.Sent = False

    def Display(self):
        self.Displayed = True
        DISPLAYED.append({"entry_id": self.EntryID, "subject": self.Subject,
                          "kind": "draft"})
        return True

    def Send(self):  # pragma: no cover - present only to prove it is never called
        raise AssertionError("Jarvis must never send mail")


class MockAppointmentItem:
    Class = 26  # olAppointment

    def __init__(self, entry_id, subject, start, end, organizer,
                 organizer_address, response_status, attendees=(), location="",
                 body="", all_day=False, recurring=False):
        self.EntryID = entry_id
        self.Subject = subject
        self.Start = start
        self.End = end
        self.Duration = int((end - start).total_seconds() // 60)
        self.Organizer = organizer
        self.OrganizerAddress = organizer_address  # mock convenience
        self.ResponseStatus = response_status
        self.MeetingStatus = 1  # olMeeting
        self.Location = location
        self.Body = body
        self.AllDayEvent = all_day
        self.IsRecurring = recurring
        self.RequiredAttendees = "; ".join(a for _, a in attendees)
        self.OptionalAttendees = ""
        self.Recipients = _Collection(
            [MockRecipient(n, a, config.OL_TO) for n, a in attendees]
        )

    def Display(self):
        DISPLAYED.append({"entry_id": self.EntryID, "subject": self.Subject,
                          "kind": "appointment"})
        return True

    def __repr__(self):
        return f"<AppointmentItem {self.EntryID} {self.Subject!r}>"


def _strip_prefixes(subject):
    """Normalise 'RE: FW: Subject' down to 'Subject' (ConversationTopic)."""
    text = (subject or "").strip()
    changed = True
    prefixes = config.REPLY_SUBJECT_PREFIXES + config.FORWARD_SUBJECT_PREFIXES
    while changed:
        changed = False
        for prefix in prefixes:
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
    return text


# ---------------------------------------------------------------------------
# Folders and namespace
# ---------------------------------------------------------------------------

_SORT_FIELDS = {
    "[SentOn]": "SentOn",
    "[ReceivedTime]": "ReceivedTime",
    "[Start]": "Start",
    "[CreationTime]": "CreationTime",
}


class MockItems(_Collection):
    """Mimics Outlook's Items collection, including Sort/Restrict."""

    def __init__(self, members):
        super().__init__(members)
        self.IncludeRecurrences = False

    def Sort(self, field, descending=False):
        attr = _SORT_FIELDS.get(field)
        if attr is None:
            raise MockComError(f"Cannot sort on {field}")
        self._members.sort(
            key=lambda item: getattr(item, attr) or datetime.min.astimezone(),
            reverse=bool(descending),
        )

    def Restrict(self, query):
        # Real Restrict returns a filtered collection. outlook_reader treats it
        # purely as an optimisation and always re-filters in Python, so the
        # mock can safely hand back everything — the downstream result is
        # identical either way.
        self.LastRestrictQuery = query
        return MockItems(self._members)

    def GetFirst(self):
        return self._members[0] if self._members else None

    def GetLast(self):
        return self._members[-1] if self._members else None


class MockFolder:
    def __init__(self, name, items, entry_id=None):
        self.Name = name
        self.EntryID = entry_id or f"FOLDER-{name.upper().replace(' ', '-')}"
        self.Items = MockItems(items)
        self.Folders = _Collection([])
        self.DefaultItemType = 0


class MockNamespace:
    def __init__(self, dataset):
        self._dataset = dataset
        self._folders = {
            config.OL_FOLDER_INBOX: MockFolder("Inbox", dataset["inbox"]),
            config.OL_FOLDER_SENT: MockFolder("Sent Items", dataset["sent"]),
            config.OL_FOLDER_CALENDAR: MockFolder("Calendar", dataset["calendar"]),
        }
        self._by_entry_id = {}
        for folder in self._folders.values():
            for item in folder.Items:
                self._by_entry_id[item.EntryID] = item
        self.CurrentUser = _AddressEntry("Marcus Skovbaek", config.USER_EMAIL)
        self.Accounts = _Collection([_MockAccount(config.USER_EMAIL)])

    def GetDefaultFolder(self, folder_id):
        try:
            return self._folders[folder_id]
        except KeyError:
            raise MockComError(f"No default folder with id {folder_id}")

    def GetItemFromID(self, entry_id, store_id=None):
        try:
            return self._by_entry_id[entry_id]
        except KeyError:
            raise MockComError(f"The item with EntryID {entry_id} could not be found")

    def Logon(self, *args, **kwargs):
        return True


class _MockAccount:
    def __init__(self, smtp_address):
        self.SmtpAddress = smtp_address
        self.DisplayName = smtp_address


class MockOutlookApplication:
    """Stands in for win32com.client.Dispatch('Outlook.Application')."""

    Version = "16.0.0.0 (mock)"
    Name = "Microsoft Outlook (mock)"

    def __init__(self, dataset=None):
        self._namespace = MockNamespace(dataset or build_dataset())

    def GetNamespace(self, name="MAPI"):
        if name != "MAPI":
            raise MockComError(f"Unsupported namespace {name}")
        return self._namespace

    @property
    def Session(self):
        return self._namespace

    def CreateItem(self, item_type):  # pragma: no cover - Jarvis never creates
        raise AssertionError("Jarvis must never create Outlook items")


class UnavailableOutlookApplication:
    """Simulates Outlook not running, for the graceful-failure test."""

    def __init__(self, message="Outlook is not running"):
        self._message = message

    def GetNamespace(self, name="MAPI"):
        raise MockComError(self._message)

    @property
    def Session(self):
        raise MockComError(self._message)


# ---------------------------------------------------------------------------
# The dataset
# ---------------------------------------------------------------------------

ME = ("Marcus Skovbaek", config.USER_EMAIL)

LARS = ("Lars Petersen", "lars.petersen@nordvind.dk")
MARIA = ("Maria Holm", "maria.holm@bygvaerk.dk")
THOMAS = ("Thomas Ega", "thomas.ega@vestas-partner.dk")
ANNE = ("Anne Brandt", "anne.brandt@kbh-arkitekter.dk")
JENS = ("Jens Moeller", "jens.moeller@kbh-arkitekter.dk")
PETER = ("Peter Lund", "peter.lund@stalgruppen.dk")
SOEREN = ("Soeren Vad", "soeren.vad@metalteknik.dk")
KATRINE = ("Katrine Borg", "katrine.borg@nordvind.dk")
ULRIK = ("Ulrik Dam", "ulrik.dam@ravnholt.dk")
COLLEAGUE = ("Nina Krag", "nina.krag@ramboell-ext.dk")

HR = ("Cedra HR", "hr@cedra.dk")
NEWSLETTER = ("Branchenyt", "newsletter@branchenyt.dk")
INDUSTRY_NEWS = ("Byggeindustrien", "news@byggeindustrien.dk")
INVOICE_BOT = ("InvoiceHub", "noreply@invoicehub.com")

LIST_HEADERS = {
    "List-Unsubscribe": "<https://byggeindustrien.dk/unsubscribe?id=9912>",
    "List-Id": "Byggeindustrien Daily <daily.byggeindustrien.dk>",
    "Precedence": "bulk",
}

AUTO_HEADERS = {
    "X-Auto-Response-Suppress": "All",
    "Auto-Submitted": "auto-replied",
}

NORMAL_HEADERS = {
    "Return-Path": "<sender@example.com>",
    "Auto-Submitted": "no",
}


def build_dataset():
    """Construct the full mock mailbox. Called fresh so dates stay relative."""
    sent = []
    inbox = []

    # -- S1: 9 days, no reply, two attachments -> FLAG, band 7-14 -----------
    sent.append(MockMailItem(
        "SENT-S1", "CONV-S1", "Q3 budget revision - figures for review",
        *ME, to=[LARS], sent_on=days_ago(9),
        body="Hi Lars,\n\nAttached are the revised Q3 figures with the updated "
             "assumptions sheet. Could you confirm the contingency line before "
             "we lock the forecast?\n\nBest,\nMarcus",
        attachments=["budget_q3_v2.xlsx", "assumptions.docx"],
        headers=NORMAL_HEADERS, folder="Sent Items"))

    # -- S2: 12 days, genuine reply after 2 days -> NOT flagged -------------
    sent.append(MockMailItem(
        "SENT-S2", "CONV-S2", "Site survey scheduling",
        *ME, to=[MARIA], sent_on=days_ago(12),
        body="Hi Maria,\n\nCan we lock in the survey window for week 38?\n\nMarcus",
        headers=NORMAL_HEADERS, folder="Sent Items"))
    inbox.append(MockMailItem(
        "INBOX-S2R", "CONV-S2", "SV: Site survey scheduling",
        *MARIA, to=[ME], sent_on=days_ago(10), received_time=days_ago(10, hour=11),
        body="Week 38 works from our side. I have blocked Tuesday and Wednesday "
             "for the crew.\n\nMaria",
        headers=NORMAL_HEADERS, folder="Inbox"))

    # -- S3: 20 days, no reply, later forwarded by me -> STILL FLAG, 14-30 --
    sent.append(MockMailItem(
        "SENT-S3", "CONV-S3", "Contract amendment - signature needed",
        *ME, to=[THOMAS], sent_on=days_ago(20),
        body="Hi Thomas,\n\nThe amendment is ready for signature. Clause 7 has "
             "been adjusted as we discussed.\n\nMarcus",
        attachments=["amendment_rev3.pdf"],
        headers=NORMAL_HEADERS, folder="Sent Items"))
    sent.append(MockMailItem(
        "SENT-S3F", "CONV-S3", "VS: Contract amendment - signature needed",
        *ME, to=[COLLEAGUE], sent_on=days_ago(6),
        body="Nina - forwarding for your awareness, still unsigned.\n\nMarcus",
        attachments=["amendment_rev3.pdf"],
        headers=NORMAL_HEADERS, folder="Sent Items"))

    # -- S4: 45 days, reply came from a CC'd recipient only -----------------
    # Under CC_REPLY_RESOLVES_THREAD=True this is RESOLVED (Feature 1 wording).
    sent.append(MockMailItem(
        "SENT-S4", "CONV-S4", "Revised drawings for tender",
        *ME, to=[ANNE], cc=[JENS], sent_on=days_ago(45),
        body="Anne,\n\nRevised drawings attached for the tender pack. Jens on "
             "copy for the structural notes.\n\nMarcus",
        attachments=["drawings_rev_c.pdf"],
        headers=NORMAL_HEADERS, folder="Sent Items"))
    inbox.append(MockMailItem(
        "INBOX-S4R", "CONV-S4", "SV: Revised drawings for tender",
        *JENS, to=[ME], cc=[ANNE], sent_on=days_ago(40),
        received_time=days_ago(40, hour=14),
        body="Structural notes look consistent with rev C. Anne is back from "
             "leave next week and will confirm the rest.\n\nJens",
        headers=NORMAL_HEADERS, folder="Inbox"))

    # -- S5: pure BCC send, 15 days -> EXCLUDED (no direct To recipient) ----
    sent.append(MockMailItem(
        "SENT-S5", "CONV-S5", "Follow-up on inspection report",
        *ME, to=[], bcc=[ULRIK], sent_on=days_ago(15),
        body="Sending the inspection summary quietly for the record.\n\nMarcus",
        headers=NORMAL_HEADERS, folder="Sent Items"))

    # -- S6: 11 days, only an auto-reply came back -> STILL FLAG, 7-14 ------
    sent.append(MockMailItem(
        "SENT-S6", "CONV-S6", "Delivery timeline confirmation",
        *ME, to=[PETER], sent_on=days_ago(11),
        body="Peter,\n\nCan you confirm the delivery window for the steel "
             "sections?\n\nMarcus",
        headers=NORMAL_HEADERS, folder="Sent Items"))
    inbox.append(MockMailItem(
        "INBOX-S6A", "CONV-S6", "Automatic reply: Delivery timeline confirmation",
        *PETER, to=[ME], sent_on=days_ago(11), received_time=days_ago(11, hour=9),
        body="I am out of the office until further notice with limited access "
             "to email.",
        headers=AUTO_HEADERS, message_class="IPM.Note.Rules.OofTemplate",
        folder="Inbox"))

    # -- S7: sent to a newsletter address, 21 days -> EXCLUDED --------------
    sent.append(MockMailItem(
        "SENT-S7", "CONV-S7", "Unsubscribe request",
        *ME, to=[NEWSLETTER], sent_on=days_ago(21),
        body="Please remove this address from the distribution list.",
        headers=NORMAL_HEADERS, folder="Sent Items"))

    # -- S8: internal cedra.dk recipient, 18 days -> EXCLUDED by domain -----
    sent.append(MockMailItem(
        "SENT-S8", "CONV-S8", "Internal: holiday planning",
        *ME, to=[HR], sent_on=days_ago(18),
        body="Registering my remaining days for the year.",
        headers=NORMAL_HEADERS, folder="Sent Items"))

    # -- S9: 3 days, no reply -> below threshold, NOT flagged ---------------
    sent.append(MockMailItem(
        "SENT-S9", "CONV-S9", "Quick question about the bolt spec",
        *ME, to=[SOEREN], sent_on=days_ago(3),
        body="Soeren - is the M20 spec still valid for the revised load case?",
        headers=NORMAL_HEADERS, folder="Sent Items"))

    # -- S10: 33 days, no reply, one attachment -> FLAG, band 30+ -----------
    sent.append(MockMailItem(
        "SENT-S10", "CONV-S10", "Handover notes - Skagen site",
        *ME, to=[KATRINE], sent_on=days_ago(33),
        body="Katrine,\n\nHandover notes attached. Section 4 lists the open "
             "snags that still need an owner.\n\nMarcus",
        attachments=["handover_skagen.docx"],
        headers=NORMAL_HEADERS, folder="Sent Items"))

    # -- S11: they replied, then I followed up 8 days ago -> FLAG on the
    #         follow-up date, not the original send. Band 7-14. -------------
    sent.append(MockMailItem(
        "SENT-S11A", "CONV-S11", "Invoice discrepancy on PO 44821",
        *ME, to=[ULRIK], sent_on=days_ago(25),
        body="Ulrik,\n\nThe invoice total does not match the agreed rate on "
             "PO 44821.\n\nMarcus",
        headers=NORMAL_HEADERS, folder="Sent Items"))
    inbox.append(MockMailItem(
        "INBOX-S11R", "CONV-S11", "SV: Invoice discrepancy on PO 44821",
        *ULRIK, to=[ME], sent_on=days_ago(24), received_time=days_ago(24, hour=16),
        body="Checking with accounts, will revert shortly.\n\nUlrik",
        headers=NORMAL_HEADERS, folder="Inbox"))
    sent.append(MockMailItem(
        "SENT-S11B", "CONV-S11", "SV: Invoice discrepancy on PO 44821",
        *ME, to=[ULRIK], sent_on=days_ago(8),
        body="Ulrik - any progress from accounts on this one?\n\nMarcus",
        headers=NORMAL_HEADERS, folder="Sent Items"))

    # -- I4 reply chain: they wrote 9 days ago, I answered 3 days ago -------
    inbox.append(MockMailItem(
        "INBOX-I4", "CONV-I4", "Tender clarification round 2",
        *ANNE, to=[ME], sent_on=days_ago(9), received_time=days_ago(9, hour=8),
        body="Marcus, the client added two clarification points to round 2.",
        headers=NORMAL_HEADERS, folder="Inbox"))
    sent.append(MockMailItem(
        "SENT-I4R", "CONV-I4", "SV: Tender clarification round 2",
        *ME, to=[ANNE], sent_on=days_ago(3),
        body="Noted - I have covered both points in the updated response.",
        headers=NORMAL_HEADERS, folder="Sent Items"))

    # -- I1: 5 days, To me, contains a question mark -> FLAG, high ----------
    inbox.append(MockMailItem(
        "INBOX-I1", "CONV-I1", "Can you confirm the crane access dates?",
        *LARS, to=[ME], sent_on=days_ago(5), received_time=days_ago(5, hour=7),
        body="We need the crane access window confirmed before we book the "
             "road closure. Which dates work?",
        headers=NORMAL_HEADERS, unread=True, folder="Inbox"))

    # -- I2: 4 days, To me, one attachment, no question mark -> FLAG -------
    inbox.append(MockMailItem(
        "INBOX-I2", "CONV-I2", "Updated site plan attached",
        *MARIA, to=[ME], sent_on=days_ago(4), received_time=days_ago(4, hour=13),
        body="Here is the updated site plan reflecting the new access route.",
        attachments=["site_plan_rev4.pdf"],
        headers=NORMAL_HEADERS, folder="Inbox"))

    # -- I3: 6 days but I am only CC'd -> EXCLUDED --------------------------
    inbox.append(MockMailItem(
        "INBOX-I3", "CONV-I3", "Ravnholt weekly status",
        *ULRIK, to=[KATRINE], cc=[ME], sent_on=days_ago(6),
        received_time=days_ago(6, hour=15),
        body="Weekly status for the Ravnholt package, Marcus on copy.",
        headers=NORMAL_HEADERS, folder="Inbox"))

    # -- I5: 1 day old -> below the 2-day threshold, NOT flagged -----------
    inbox.append(MockMailItem(
        "INBOX-I5", "CONV-I5", "Are we still on for Thursday?",
        *SOEREN, to=[ME], sent_on=days_ago(1), received_time=days_ago(1, hour=17),
        body="Just checking Thursday still works for the workshop visit?",
        headers=NORMAL_HEADERS, unread=True, folder="Inbox"))

    # -- I7: newsletter with List-Unsubscribe -> EXCLUDED ------------------
    inbox.append(MockMailItem(
        "INBOX-I7", "CONV-I7", "Byggeindustrien daily digest",
        *INDUSTRY_NEWS, to=[ME], sent_on=days_ago(8),
        received_time=days_ago(8, hour=6),
        body="Today's headlines from the construction sector.",
        headers=LIST_HEADERS, folder="Inbox"))

    # -- I8: automated noreply sender -> EXCLUDED --------------------------
    inbox.append(MockMailItem(
        "INBOX-I8", "CONV-I8", "Invoice 88213 is now available",
        *INVOICE_BOT, to=[ME], sent_on=days_ago(10),
        received_time=days_ago(10, hour=2),
        body="Your invoice is available for download. Do not reply.",
        headers=NORMAL_HEADERS, folder="Inbox"))

    # -- I9: internal cedra.dk sender -> EXCLUDED by domain ----------------
    inbox.append(MockMailItem(
        "INBOX-I9", "CONV-I9", "Reminder: register your holiday days",
        *HR, to=[ME], sent_on=days_ago(7), received_time=days_ago(7, hour=9),
        body="Please register remaining holiday days before the deadline.",
        headers=NORMAL_HEADERS, folder="Inbox"))

    # -- I10: 16 days, question mark -> FLAG, high -------------------------
    inbox.append(MockMailItem(
        "INBOX-I10", "CONV-I10", "Pre-qualification pack - who is presenting?",
        *THOMAS, to=[ME], cc=[ANNE], sent_on=days_ago(16),
        received_time=days_ago(16, hour=12),
        body="We need to confirm who presents the pre-qualification pack. "
             "Can you take section 3?",
        headers=NORMAL_HEADERS, folder="Inbox"))

    # -- I11: 3 days, two attachments -> FLAG, normal ----------------------
    inbox.append(MockMailItem(
        "INBOX-I11", "CONV-I11", "Snag list and photos from Friday",
        *KATRINE, to=[ME], sent_on=days_ago(3), received_time=days_ago(3, hour=16),
        body="Snag list plus the photo set from Friday's walkaround.",
        attachments=["snag_list.xlsx", "photos_friday.zip"],
        headers=NORMAL_HEADERS, folder="Inbox"))

    calendar = build_calendar()
    return {"sent": sent, "inbox": inbox, "calendar": calendar}


def build_calendar():
    """Meetings covering every acceptance state and both prep outcomes."""
    return [
        # M1 - accepted, 3 days out
        MockAppointmentItem(
            "CAL-M1", "Weekly site coordination",
            days_ahead(3, hour=9), days_ahead(3, hour=10),
            *LARS, config.OL_RESPONSE_ACCEPTED,
            attendees=[ME, MARIA, KATRINE, ULRIK, SOEREN, THOMAS],
            location="Teams", body="Standing agenda: progress, snags, logistics."),

        # M2 - not responded, 2 days out -> FLAG pending
        MockAppointmentItem(
            "CAL-M2", "Pre-qualification meeting - Skagen",
            days_ahead(2, hour=13), days_ahead(2, hour=14, minute=30),
            *THOMAS, config.OL_RESPONSE_NOT_RESPONDED,
            attendees=[ME, ANNE, JENS, KATRINE],
            location="Aarhus office", body=""),

        # M3 - accepted, within 24h, no notes and no related thread -> FLAG prep
        MockAppointmentItem(
            "CAL-M3", "Insurance renewal sync",
            hours_ahead(18), hours_ahead(18) + timedelta(minutes=45),
            *KATRINE, config.OL_RESPONSE_ACCEPTED,
            attendees=[ME, KATRINE, ULRIK],
            location="Teams", body=""),

        # M4 - accepted, within 24h, but tied to CONV-S3 -> NOT flagged
        MockAppointmentItem(
            "CAL-M4", "Contract amendment walkthrough",
            hours_ahead(20), hours_ahead(20) + timedelta(minutes=60),
            *THOMAS, config.OL_RESPONSE_ACCEPTED,
            attendees=[ME, THOMAS, ANNE],
            location="Teams", body=""),

        # M5 - organised by me, 5 days out
        MockAppointmentItem(
            "CAL-M5", "Project team standup",
            days_ahead(5, hour=8, minute=30),
            days_ahead(5, hour=8, minute=45),
            *ME, config.OL_RESPONSE_ORGANIZED,
            attendees=[ME, LARS, MARIA, KATRINE, SOEREN, ULRIK, ANNE, JENS],
            location="Teams",
            body="Round the table, blockers only, fifteen minutes hard stop.",
            recurring=True),

        # M6 - declined, 4 days out
        MockAppointmentItem(
            "CAL-M6", "Vendor demo - scaffolding system",
            days_ahead(4, hour=15), days_ahead(4, hour=16),
            *PETER, config.OL_RESPONSE_DECLINED,
            attendees=[ME, PETER, SOEREN], location="Vendor site", body=""),

        # M9 - not responded AND within 24h with no prep -> FLAG both
        MockAppointmentItem(
            "CAL-M9", "Morning logistics call",
            hours_ahead(10), hours_ahead(10) + timedelta(minutes=30),
            *SOEREN, config.OL_RESPONSE_NOT_RESPONDED,
            attendees=[ME, SOEREN, LARS], location="Phone", body=""),

        # M7 - 12 days out -> outside the 7-day window
        MockAppointmentItem(
            "CAL-M7", "Quarterly all-hands",
            days_ahead(12, hour=14), days_ahead(12, hour=16),
            *HR, config.OL_RESPONSE_ACCEPTED,
            attendees=[ME, HR], location="Auditorium", body="Quarterly update."),

        # M8 - already happened -> excluded
        MockAppointmentItem(
            "CAL-M8", "Safety briefing",
            days_ago(2, hour=8), days_ago(2, hour=9),
            *LARS, config.OL_RESPONSE_ACCEPTED,
            attendees=[ME, LARS], location="Site office", body="Monthly briefing."),
    ]


# Expected outcomes, asserted by tests/verify_processors.py. Kept beside the
# data so the two can never drift apart.
EXPECTED = {
    "awaiting_reply": {
        "CONV-S1": {"days": 9, "band": "7-14 days", "attachments": 2},
        "CONV-S3": {"days": 20, "band": "14-30 days", "attachments": 1},
        "CONV-S6": {"days": 11, "band": "7-14 days", "attachments": 0},
        "CONV-S10": {"days": 33, "band": "30+ days", "attachments": 1},
        "CONV-S11": {"days": 8, "band": "7-14 days", "attachments": 0},
    },
    "awaiting_reply_excluded": {
        "CONV-S2": "recipient replied",
        "CONV-S4": "CC'd recipient replied (CC_REPLY_RESOLVES_THREAD)",
        "CONV-S5": "pure BCC send",
        "CONV-S7": "mailing-list recipient",
        "CONV-S8": "excluded domain",
        "CONV-S9": "below the 7-day threshold",
        "CONV-I4": "my reply is only 3 days old",
    },
    "overdue_inbound": {
        "CONV-I1": {"days": 5, "priority": "high", "attachments": 0},
        "CONV-I2": {"days": 4, "priority": "normal", "attachments": 1},
        "CONV-I10": {"days": 16, "priority": "high", "attachments": 0},
        "CONV-I11": {"days": 3, "priority": "normal", "attachments": 2},
        "CONV-S2": {"days": 10, "priority": "normal", "attachments": 0},
        "CONV-S4": {"days": 40, "priority": "normal", "attachments": 0},
    },
    "overdue_inbound_excluded": {
        "CONV-I3": "CC only, not in To",
        "CONV-I5": "below the 2-day threshold",
        "CONV-I4": "I replied 3 days ago",
        "CONV-I7": "mailing list (List-Unsubscribe)",
        "CONV-I8": "automated noreply sender",
        "CONV-I9": "excluded domain",
        "CONV-S6": "auto-reply",
        "CONV-S11": "I replied 8 days ago",
    },
    "calendar_in_window": [
        "CAL-M9", "CAL-M3", "CAL-M4", "CAL-M2", "CAL-M1", "CAL-M6", "CAL-M5",
    ],
    "calendar_excluded": {
        "CAL-M7": "starts beyond the 7-day window",
        "CAL-M8": "already in the past",
    },
    "calendar_pending": ["CAL-M2", "CAL-M9"],
    "calendar_unprepared": ["CAL-M3", "CAL-M9"],
}


def get_application(dataset=None):
    """Entry point used by outlook_reader when OUTLOOK_BACKEND == 'mock'."""
    return MockOutlookApplication(dataset)


if __name__ == "__main__":
    data = build_dataset()
    print(f"Mock mailbox for {config.USER_EMAIL}")
    print(f"  Sent Items : {len(data['sent'])} items")
    print(f"  Inbox      : {len(data['inbox'])} items")
    print(f"  Calendar   : {len(data['calendar'])} items")
    print("\nSample sent item:")
    s = data["sent"][0]
    print(f"  EntryID        : {s.EntryID}")
    print(f"  ConversationID : {s.ConversationID}")
    print(f"  Subject        : {s.Subject}")
    print(f"  SentOn         : {s.SentOn}")
    print(f"  To             : {s.To}")
    print(f"  Attachments    : {[a.FileName for a in s.Attachments]}")
