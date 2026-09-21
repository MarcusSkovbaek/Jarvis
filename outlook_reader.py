"""
outlook_reader.py — the only module that talks to Outlook.

It has two interchangeable backends, selected by config.OUTLOOK_BACKEND:

    "mock" -> mock_outlook.MockOutlookApplication   (Phase 1, no mailbox)
    "com"  -> win32com Dispatch("Outlook.Application")  (Phase 2, work PC)

Both paths run identical normalisation code, so nothing downstream knows or
cares which one is active. Switching to the real mailbox is a config change,
not a code change.

Everything here is read-only apart from two explicitly user-triggered actions:
open_item() (Display) and create_reply_all_draft() (ReplyAll + Display). The
module never sends, deletes or modifies anything.
"""

import logging
from datetime import datetime, timedelta, timezone

import config

log = logging.getLogger("jarvis.outlook")


class OutlookUnavailable(RuntimeError):
    """Outlook could not be reached. Callers log this and retry next cycle."""


# ---------------------------------------------------------------------------
# Datetime normalisation
# ---------------------------------------------------------------------------

def to_utc(value):
    """Normalise anything Outlook hands back to an aware UTC datetime.

    win32com returns pywintypes.datetime (tz-aware); some items return naive
    datetimes. Both shapes are handled here so no other module has to.
    """
    if value is None:
        return None
    if not isinstance(value, datetime):
        try:
            value = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError):
            return None
    if value.tzinfo is None:
        value = value.astimezone()  # interpret as local time
    return value.astimezone(timezone.utc)


def _com_date(dt):
    """Format a datetime for an Outlook Restrict() filter (US locale form)."""
    return dt.strftime("%m/%d/%Y %I:%M %p")


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------

def parse_headers(raw):
    """Parse RFC-822 transport headers into {lowercase-name: value}.

    Folded continuation lines are joined. Repeated headers are comma-joined.
    """
    headers = {}
    if not raw:
        return headers
    name = None
    for line in str(raw).replace("\r\n", "\n").split("\n"):
        if not line.strip():
            continue
        if line[0] in " \t" and name:
            headers[name] += " " + line.strip()
            continue
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip().lower()
        value = value.strip()
        headers[name] = f"{headers[name]}, {value}" if name in headers else value
    return headers


def domain_of(address):
    if not address or "@" not in address:
        return ""
    return address.rsplit("@", 1)[1].strip().lower()


def local_part_of(address):
    if not address:
        return ""
    return address.split("@", 1)[0].strip().lower()


def _safe(getter, default=None):
    """COM property access fails in many mundane ways; never let that crash."""
    try:
        value = getter()
    except Exception:  # noqa: BLE001 - com_error, MockComError, AttributeError...
        return default
    return default if value is None else value


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def get_application(backend=None, dataset=None):
    """Return an Outlook Application object for the configured backend."""
    backend = (backend or config.OUTLOOK_BACKEND).strip().lower()

    if backend == "mock":
        import mock_outlook
        return mock_outlook.get_application(dataset)

    if backend == "com":
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:  # pragma: no cover - Phase 2 only
            raise OutlookUnavailable(
                "pywin32 is not installed; cannot use the 'com' backend"
            ) from exc
        try:
            # CoInitialize is required on every thread that touches COM,
            # including the background sync thread.
            pythoncom.CoInitialize()
            return win32com.client.Dispatch("Outlook.Application")
        except Exception as exc:  # pragma: no cover - Phase 2 only
            raise OutlookUnavailable(f"Could not attach to Outlook: {exc}") from exc

    raise ValueError(f"Unknown OUTLOOK_BACKEND: {backend!r}")


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

class OutlookReader:
    """Pulls raw data out of Outlook and normalises it into plain dicts.

    Pass `application` to inject any object implementing the Outlook object
    model — that is how the mock, and any future backend, is swapped in.
    """

    def __init__(self, application=None, backend=None, dataset=None):
        self._backend = (backend or config.OUTLOOK_BACKEND).strip().lower()
        self._application = application
        self._namespace = None
        self._injected = application is not None

    # -- connection ---------------------------------------------------------

    @property
    def backend(self):
        return "injected" if self._injected else self._backend

    def connect(self):
        """Attach to Outlook. Raises OutlookUnavailable if it is not running."""
        if self._application is None:
            self._application = get_application(self._backend)
        if self._namespace is None:
            try:
                self._namespace = self._application.Session
            except Exception as exc:  # noqa: BLE001
                self._application = None
                raise OutlookUnavailable(f"Outlook is not available: {exc}") from exc
        return self._namespace

    def close(self):
        self._application = None
        self._namespace = None

    def _folder(self, folder_id):
        namespace = self.connect()
        try:
            return namespace.GetDefaultFolder(folder_id)
        except Exception as exc:  # noqa: BLE001
            raise OutlookUnavailable(
                f"Could not open default folder {folder_id}: {exc}"
            ) from exc

    # -- generic item walking ----------------------------------------------

    @staticmethod
    def _iter_items(items):
        """Iterate a COM Items collection safely, 1-based, skipping bad items."""
        try:
            count = int(items.Count)
        except Exception:  # noqa: BLE001
            return
        for index in range(1, count + 1):
            try:
                yield items.Item(index)
            except Exception as exc:  # noqa: BLE001
                log.warning("Skipping unreadable item at index %s: %s", index, exc)

    def _restrict_by_date(self, items, field, since):
        """Ask Outlook to pre-filter by date; fall back to no filter.

        Restrict() is only an optimisation — every caller re-filters in Python,
        so a backend that ignores it produces identical results.
        """
        try:
            return items.Restrict(f"[{field}] >= '{_com_date(since)}'")
        except Exception as exc:  # noqa: BLE001
            log.debug("Restrict on [%s] unavailable (%s); filtering in Python",
                      field, exc)
            return items

    # -- address resolution -------------------------------------------------

    @staticmethod
    def _smtp_from_recipient(recipient):
        """Resolve a recipient to an SMTP address, unwrapping Exchange DNs."""
        address = _safe(lambda: recipient.Address, "") or ""
        if address and not address.startswith("/"):
            return address.strip()
        # Exchange X500 DN -> ask the address entry for the real SMTP address.
        entry = _safe(lambda: recipient.AddressEntry)
        if entry is not None:
            user = _safe(lambda: entry.GetExchangeUser())
            if user is not None:
                smtp = _safe(lambda: user.PrimarySmtpAddress, "")
                if smtp:
                    return smtp.strip()
            smtp = _safe(lambda: entry.GetProperty(config.PR_SMTP_ADDRESS), "")
            if smtp:
                return smtp.strip()
        return address.strip()

    @staticmethod
    def _sender_smtp(item):
        address = _safe(lambda: item.SenderEmailAddress, "") or ""
        address_type = (_safe(lambda: item.SenderEmailType, "") or "").upper()
        if address_type == "EX" or address.startswith("/"):
            accessor = _safe(lambda: item.PropertyAccessor)
            if accessor is not None:
                smtp = _safe(
                    lambda: accessor.GetProperty(config.PR_SENDER_SMTP_ADDRESS), ""
                )
                if smtp:
                    return smtp.strip()
            sender = _safe(lambda: item.Sender)
            if sender is not None:
                user = _safe(lambda: sender.GetExchangeUser())
                if user is not None:
                    smtp = _safe(lambda: user.PrimarySmtpAddress, "")
                    if smtp:
                        return smtp.strip()
        return address.strip()

    # -- normalisation ------------------------------------------------------

    def normalise_mail(self, item, folder_name):
        """Turn a MailItem (mock or COM) into a plain, JSON-friendly dict."""
        recipients = {config.OL_TO: [], config.OL_CC: [], config.OL_BCC: []}
        collection = _safe(lambda: item.Recipients)
        if collection is not None:
            for index in range(1, int(_safe(lambda: collection.Count, 0)) + 1):
                recipient = _safe(lambda: collection.Item(index))
                if recipient is None:
                    continue
                kind = int(_safe(lambda: recipient.Type, config.OL_TO))
                bucket = recipients.get(kind)
                if bucket is None:
                    continue
                bucket.append({
                    "name": (_safe(lambda: recipient.Name, "") or "").strip(),
                    "address": self._smtp_from_recipient(recipient).lower(),
                })

        attachments = []
        collection = _safe(lambda: item.Attachments)
        if collection is not None:
            for index in range(1, int(_safe(lambda: collection.Count, 0)) + 1):
                attachment = _safe(lambda: collection.Item(index))
                if attachment is None:
                    continue
                name = _safe(lambda: attachment.FileName, "") or \
                    _safe(lambda: attachment.DisplayName, "")
                if name:
                    attachments.append(str(name))

        accessor = _safe(lambda: item.PropertyAccessor)
        raw_headers = ""
        if accessor is not None:
            raw_headers = _safe(
                lambda: accessor.GetProperty(config.PR_TRANSPORT_MESSAGE_HEADERS), ""
            )

        sender = self._sender_smtp(item).lower()
        subject = _safe(lambda: item.Subject, "") or ""

        return {
            "entry_id": _safe(lambda: item.EntryID, "") or "",
            "conversation_id": _safe(lambda: item.ConversationID, "") or "",
            "conversation_topic": _safe(lambda: item.ConversationTopic, "") or subject,
            "subject": subject,
            "sender_name": (_safe(lambda: item.SenderName, "") or "").strip(),
            "sender_address": sender,
            "to": recipients[config.OL_TO],
            "cc": recipients[config.OL_CC],
            "bcc": recipients[config.OL_BCC],
            "sent_on": to_utc(_safe(lambda: item.SentOn)),
            "received_time": to_utc(_safe(lambda: item.ReceivedTime)),
            "body": _safe(lambda: item.Body, "") or "",
            "attachments": attachments,
            "message_class": _safe(lambda: item.MessageClass, "") or "",
            "headers": parse_headers(raw_headers),
            "unread": bool(_safe(lambda: item.UnRead, False)),
            "folder": folder_name,
            "is_from_user": config.is_user(sender),
        }

    def normalise_appointment(self, item):
        attendees = []
        collection = _safe(lambda: item.Recipients)
        if collection is not None:
            for index in range(1, int(_safe(lambda: collection.Count, 0)) + 1):
                recipient = _safe(lambda: collection.Item(index))
                if recipient is None:
                    continue
                attendees.append({
                    "name": (_safe(lambda: recipient.Name, "") or "").strip(),
                    "address": self._smtp_from_recipient(recipient).lower(),
                })

        start = to_utc(_safe(lambda: item.Start))
        end = to_utc(_safe(lambda: item.End))
        duration = _safe(lambda: item.Duration)
        if duration is None and start and end:
            duration = int((end - start).total_seconds() // 60)

        status = int(_safe(lambda: item.ResponseStatus, config.OL_RESPONSE_NONE))

        return {
            "entry_id": _safe(lambda: item.EntryID, "") or "",
            "subject": _safe(lambda: item.Subject, "") or "(no subject)",
            "start": start,
            "end": end,
            "duration_minutes": int(duration or 0),
            "organizer": (_safe(lambda: item.Organizer, "") or "").strip(),
            "organizer_address": (
                _safe(lambda: item.OrganizerAddress, "") or ""
            ).strip().lower(),
            "response_status": status,
            "response_label": config.RESPONSE_STATUS_LABELS.get(status, "Unknown"),
            "attendees": attendees,
            "attendee_count": len(attendees),
            "location": _safe(lambda: item.Location, "") or "",
            "body": _safe(lambda: item.Body, "") or "",
            "all_day": bool(_safe(lambda: item.AllDayEvent, False)),
            "recurring": bool(_safe(lambda: item.IsRecurring, False)),
        }

    # -- public reads -------------------------------------------------------

    def read_sent(self, lookback_days=None, now=None):
        days = config.SENT_LOOKBACK_DAYS if lookback_days is None else lookback_days
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        folder = self._folder(config.OL_FOLDER_SENT)
        items = _safe(lambda: folder.Items)
        if items is None:
            return []
        _safe(lambda: items.Sort("[SentOn]", True))
        items = self._restrict_by_date(items, "SentOn", cutoff)

        out = []
        for item in self._iter_items(items):
            record = self.normalise_mail(item, "Sent Items")
            when = record["sent_on"] or record["received_time"]
            if when is None or when < cutoff:
                continue
            out.append(record)
        log.debug("read_sent: %s items since %s", len(out), cutoff.date())
        return out

    def read_inbox(self, lookback_days=None, now=None):
        days = config.INBOX_LOOKBACK_DAYS if lookback_days is None else lookback_days
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        folder = self._folder(config.OL_FOLDER_INBOX)

        folders = [folder]
        if config.INCLUDE_INBOX_SUBFOLDERS:
            sub = _safe(lambda: folder.Folders)
            if sub is not None:
                folders += list(self._iter_items(sub))

        out = []
        for current in folders:
            items = _safe(lambda: current.Items)
            if items is None:
                continue
            _safe(lambda: items.Sort("[ReceivedTime]", True))
            items = self._restrict_by_date(items, "ReceivedTime", cutoff)
            name = _safe(lambda: current.Name, "Inbox")
            for item in self._iter_items(items):
                record = self.normalise_mail(item, name)
                when = record["received_time"] or record["sent_on"]
                if when is None or when < cutoff:
                    continue
                out.append(record)
        log.debug("read_inbox: %s items since %s", len(out), cutoff.date())
        return out

    def read_calendar(self, lookahead_days=None, now=None):
        days = config.CALENDAR_LOOKAHEAD_DAYS if lookahead_days is None \
            else lookahead_days
        start = now or datetime.now(timezone.utc)
        end = start + timedelta(days=days)
        folder = self._folder(config.OL_FOLDER_CALENDAR)
        items = _safe(lambda: folder.Items)
        if items is None:
            return []

        # Recurring meetings only appear as individual occurrences when
        # IncludeRecurrences is set AND the collection is sorted by [Start].
        _safe(lambda: setattr(items, "IncludeRecurrences", True))
        _safe(lambda: items.Sort("[Start]"))
        try:
            items = items.Restrict(
                f"[Start] <= '{_com_date(end)}' AND [End] >= '{_com_date(start)}'"
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Calendar Restrict unavailable (%s); filtering in Python", exc)

        out = []
        for item in self._iter_items(items):
            record = self.normalise_appointment(item)
            if record["start"] is None:
                continue
            # Keep meetings that are upcoming or currently running.
            finish = record["end"] or record["start"]
            if finish < start or record["start"] > end:
                continue
            out.append(record)
        out.sort(key=lambda record: record["start"])
        log.debug("read_calendar: %s meetings in the next %s days", len(out), days)
        return out

    def read_all(self, now=None):
        """One pass over every source. Used by sync.py."""
        return {
            "sent": self.read_sent(now=now),
            "inbox": self.read_inbox(now=now),
            "calendar": self.read_calendar(now=now),
        }

    # -- the only two non-read-only actions, both user-triggered ------------

    def open_item(self, entry_id):
        """Open an item in Outlook (Feature 4: clicking a row)."""
        namespace = self.connect()
        try:
            item = namespace.GetItemFromID(entry_id)
        except Exception as exc:  # noqa: BLE001
            raise OutlookUnavailable(
                f"Could not find item {entry_id} in Outlook: {exc}"
            ) from exc
        item.Display()
        log.info("Displayed item %s in Outlook", entry_id)
        return True

    def create_reply_all_draft(self, entry_id, body=""):
        """Open a Reply All draft pre-filled with `body`.

        The draft is displayed for the user to review and send by hand.
        Jarvis never calls Send().
        """
        namespace = self.connect()
        try:
            item = namespace.GetItemFromID(entry_id)
        except Exception as exc:  # noqa: BLE001
            raise OutlookUnavailable(
                f"Could not find item {entry_id} in Outlook: {exc}"
            ) from exc
        try:
            draft = item.ReplyAll()
        except Exception as exc:  # noqa: BLE001
            raise OutlookUnavailable(
                f"Could not create a reply for {entry_id}: {exc}"
            ) from exc

        if body:
            existing = _safe(lambda: draft.Body, "") or ""
            # Put the drafted text above Outlook's quoted history.
            draft.Body = f"{body}\n\n{existing}"
        draft.Display()
        log.info("Opened Reply All draft for %s (%s chars pre-filled)",
                 entry_id, len(body))
        return True
