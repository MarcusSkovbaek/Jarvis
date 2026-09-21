"""
redaction.py — turns real mailbox values into safe stand-ins.

Used by probe.py so its output can be sent to someone helping with the build
without any of your mail leaving your control. Redaction is ON by default;
`--no-redact` turns it off for output you only ever read yourself.

The design goal is that a redacted report is still diagnostically useful. A
blanket `****` would be safe and useless: most Phase 2 bugs are about *which*
address matched *which* item, so the identifiers have to stay distinguishable
even while their values are hidden. So instead of masking, every value is
replaced by a stable stand-in:

    soeren.vad@metalteknik.dk  ->  person3@domain2.example
    Søren Vad                  ->  Person 3
    "SV: Tender clarification" ->  "SV: <subject, 22 chars>"
    "Q4 forecast.xlsx"         ->  <file2, .xlsx, 16 chars>

The same real value always maps to the same stand-in within one run, and
never across runs, so relationships in the data survive ("person3 appears in
both sections") while the values themselves do not. Nothing is reversible:
the mapping table is held in memory for the duration of the run and never
written anywhere.

What deliberately survives redaction, because it carries no content and the
logic cannot be checked without it: counts, dates and times, day arithmetic,
message classes, transport header *names*, response status, flags, and the
*shape* of identifiers (length and character class).
"""

import re

# Recognised reply/forward prefixes, in several languages, since these drive
# thread matching and are protocol artefacts rather than content.
_PREFIX = re.compile(
    r"^\s*((?:re|sv|vs|aw|fw|fwd|vb|tr|antwort|rép|ref)\s*(?:\[\d+\])?\s*:\s*)",
    re.IGNORECASE)

_HEX = re.compile(r"^[0-9A-Fa-f]+$")


class Redactor:
    """Stable, one-way stand-ins for identifying values.

    With `enabled=False` every method returns its input untouched, so calling
    code never needs to branch on whether redaction is on.
    """

    def __init__(self, enabled=True, user_email=None):
        self.enabled = enabled
        self._addresses = {}
        self._domains = {}
        self._people = {}
        self._ids = {}
        self._files = {}
        # Registered first so the user's own address is always "me", which
        # makes "sent by me" logic readable in the report.
        if user_email:
            self._addresses[user_email.strip().lower()] = "me@own-domain.example"

    # -- addresses ------------------------------------------------------

    def address(self, value):
        """person3@domain2.example — or the X500 shape if it is a DN."""
        if not self.enabled or not value:
            return value
        text = str(value).strip()
        key = text.lower()
        if key in self._addresses:
            return self._addresses[key]
        if "@" not in text:
            # Exchange legacy DN, or something else that is not an SMTP
            # address. Which one it is matters more than its value.
            stand_in = self.dn(text)
        else:
            _, _, domain = text.partition("@")
            stand_in = f"person{len(self._addresses) + 1}@{self.domain(domain)}"
        self._addresses[key] = stand_in
        return stand_in

    def domain(self, value):
        if not self.enabled or not value:
            return value
        key = str(value).strip().lower()
        if key not in self._domains:
            self._domains[key] = f"domain{len(self._domains) + 1}.example"
        return self._domains[key]

    def dn(self, value):
        """/o=<5>/ou=<19>/cn=<26> — keeps the structure, drops the values.

        The Exchange legacy-DN case is the single most likely Phase 2 failure,
        and diagnosing it needs the key names and the segment lengths, not the
        organisation and user names they contain.
        """
        if not self.enabled or not value:
            return value
        text = str(value)
        parts = []
        for segment in text.split("/"):
            if not segment:
                continue
            key, sep, rest = segment.partition("=")
            parts.append(f"{key}=<{len(rest)}>" if sep else f"<{len(segment)}>")
        return "/" + "/".join(parts) if parts else f"<{len(text)} chars>"

    def person(self, value):
        """Person 3 — matched to the address stand-in where one exists."""
        if not self.enabled or not value:
            return value
        text = str(value).strip()
        key = text.lower()
        if key not in self._people:
            self._people[key] = f"Person {len(self._people) + 1}"
        return self._people[key]

    def participant(self, name, address):
        """'Person 3 <person3@domain2.example>' for prompt participant lines."""
        if not self.enabled:
            return f"{name or address} <{address}>"
        return f"{self.person(name or address)} <{self.address(address)}>"

    # -- text -----------------------------------------------------------

    def subject(self, value):
        """'SV: <subject, 22 chars>' — prefix kept, content dropped.

        The prefix drives reply detection, so it has to survive; everything
        after it is business content and does not.
        """
        if not self.enabled:
            return value
        if not value:
            return value
        text = str(value)
        prefixes = ""
        while True:
            match = _PREFIX.match(text)
            if not match:
                break
            prefixes += match.group(1).strip() + " "
            text = text[match.end():]
        return f"{prefixes}<subject, {len(text)} chars>"

    def body(self, value):
        """<body, 412 chars, 68 words> — length only."""
        if not self.enabled:
            return value
        if not value:
            return value
        text = str(value)
        return f"<body, {len(text)} chars, {len(text.split())} words>"

    def filename(self, value):
        """<file2, .xlsx, 16 chars> — extension kept, name dropped."""
        if not self.enabled or not value:
            return value
        text = str(value)
        key = text.lower()
        if key not in self._files:
            _, dot, ext = text.rpartition(".")
            suffix = f".{ext}" if dot and len(ext) <= 8 else "no extension"
            self._files[key] = (f"<file{len(self._files) + 1}, {suffix}, "
                                f"{len(text)} chars>")
        return self._files[key]

    # -- identifiers ----------------------------------------------------

    def entry_id(self, value):
        """<id4, hex, 140 chars> — shape only, but stable within the run.

        Outlook EntryIDs encode the mailbox and store, so the value is not
        printed. The length and character class are what tell me whether the
        id looks well-formed, and the stable number lets the same item be
        followed between sections.
        """
        if not self.enabled or not value:
            return value
        text = str(value)
        key = text
        if key not in self._ids:
            kind = "hex" if _HEX.match(text) else "mixed"
            self._ids[key] = f"<id{len(self._ids) + 1}, {kind}, {len(text)} chars>"
        return self._ids[key]

    # -- reporting ------------------------------------------------------

    def summary(self):
        """One line recording how much was replaced, for the report footer."""
        if not self.enabled:
            return "Redaction OFF — this output contains real mail data."
        return (f"Redaction ON — {len(self._addresses)} addresses, "
                f"{len(self._people)} names, {len(self._ids)} item ids and "
                f"{len(self._files)} filenames replaced with stand-ins.")
