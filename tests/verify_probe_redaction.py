"""
verify_probe_redaction.py — proves the shareable probe report is shareable.

The probe is the one artefact that travels from the work PC to someone helping
with the build, so "we redact it" cannot be a claim in a docstring. This test
makes it a checked property:

  1. It reads every piece of content out of the mock mailbox — addresses,
     display names, subjects, attachment filenames, message bodies, item ids —
     and builds a canary list of the exact strings that must never appear in a
     redacted report.
  2. It runs the probe in redacted mode and searches the report for every
     canary, including case-insensitively and against the individual words of
     names and subjects, so a partial leak ("Q3 budget" out of "Q3 budget
     review") fails just as loudly as a whole one.
  3. It checks the stand-ins are still *useful*: the same address must map to
     the same stand-in everywhere, two different addresses must never collide,
     and the reply prefixes the matching logic depends on must survive.
  4. It confirms --no-redact really does produce the unredacted report, so the
     test cannot pass just because the probe printed nothing.

Run:  python tests\\verify_probe_redaction.py
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config  # noqa: E402

FAILURES = []
BOILERPLATE = set()
RETAINED_BY_DESIGN = set()  # file extensions: kept deliberately, documented


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# A word can only be evidence of a leak if the report would not have printed
# it anyway. The report's own prose, its field labels and the verbatim prompt
# template are all boilerplate, so the vocabulary of every string literal in
# the modules that produce the report is collected and excluded. Deriving it
# from the source rather than hand-listing it means the exclusion set cannot
# silently fall out of date as the wording changes.
BOILERPLATE_SOURCES = ("probe.py", "redaction.py", "prompt_builder.py",
                       "config.py", "calendar_reader.py", "email_processor.py")


def boilerplate_vocabulary():
    import ast

    words = set()
    for name in BOILERPLATE_SOURCES:
        tree = ast.parse(io.open(os.path.join(ROOT, name),
                                 encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                words.update(w.lower() for w in
                             re.findall(r"[A-Za-z\u00c0-\u00ff0-9]{3,}", node.value))
    # Common English that appears in mock bodies and in any prose.
    words.update("the a an and or of to for on in at is it be we you your my "
                 "me no not if as by with from this that next week all any new "
                 "are was has have will can could would should do does did but "
                 "so then than there here when what which who how out up off "
                 "get got let see send sent back before after both each".split())
    return words


def canaries_from_dataset():
    """Every string from the mock mailbox that must not survive redaction."""
    from outlook_reader import OutlookReader

    reader = OutlookReader(backend="mock")
    reader.connect()
    records = reader.read_sent() + reader.read_inbox()
    meetings = reader.read_calendar()
    reader.close()

    exact, words = set(), set()

    def add_text(value, collect_words=True):
        if not value:
            return
        text = str(value).strip()
        if len(text) < 3:
            return
        exact.add(text)
        if collect_words:
            for word in re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}", text):
                if word.lower() not in BOILERPLATE:
                    words.add(word)

    for record in records:
        add_text(record.get("subject"))
        add_text(record.get("sender_address"), collect_words=False)
        add_text(record.get("sender_name"))
        add_text(record.get("body"))
        for person in (record.get("to") or []) + (record.get("cc") or []):
            add_text(person.get("address"), collect_words=False)
            add_text(person.get("name"))
        for name in record.get("attachments") or []:
            add_text(name)
            # The extension is kept on purpose (it tells me what kind of
            # document is involved without naming it), so it is not a canary.
            stem, dot, ext = str(name).rpartition(".")
            if dot:
                RETAINED_BY_DESIGN.add(ext.lower())
    for meeting in meetings:
        add_text(meeting.get("subject"))
        add_text(meeting.get("organizer"))
        for person in meeting.get("attendees") or []:
            add_text(person.get("name") if isinstance(person, dict) else person)

    # The user's own address is content too.
    exact.add(config.USER_EMAIL)
    return exact, words


def run_probe(redact, tmpdir):
    """Run the probe, returning (console_text, report_text)."""
    import probe

    report_path = os.path.join(tmpdir, "report.txt")
    buffer = io.StringIO()
    saved = sys.stdout
    sys.stdout = buffer
    try:
        code = probe.run(full=True, redact=redact, backend="mock",
                         report_path=report_path)
    finally:
        sys.stdout = saved
    report = io.open(report_path, encoding="utf-8").read()
    return code, buffer.getvalue(), report


def main():
    import tempfile

    global BOILERPLATE
    BOILERPLATE = boilerplate_vocabulary()
    tmpdir = tempfile.mkdtemp(prefix="jarvis_redaction_")
    exact, words = canaries_from_dataset()
    print(f"  ({len(BOILERPLATE)} boilerplate words excluded, taken from the "
          f"source of {len(BOILERPLATE_SOURCES)} modules)")

    rule("Redacted probe report")
    code, console, report = run_probe(True, tmpdir)
    check("probe exits 0", code == 0, f"exit={code}")
    check("report file written and non-trivial", len(report) > 2000,
          f"{len(report)} chars")
    check("console and report carry the same content",
          console.strip().endswith(report.strip()[-200:].strip())
          or report.strip()[:200] in console,
          "report mirrors console")

    combined = console + "\n" + report
    lowered = combined.lower()

    rule(f"No verbatim content from the mailbox ({len(exact)} strings)")
    leaked = sorted(s for s in exact if s.lower() in lowered)
    check("no exact address, name, subject, filename or body survives",
          not leaked, f"leaked: {leaked[:5]}" if leaked else "none")

    rule(f"No fragments of content either ({len(words)} words)")

    def appears_as_word(word):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])",
                         combined, re.IGNORECASE) is not None

    leaked_words = sorted(w for w in words
                          if w.lower() not in RETAINED_BY_DESIGN
                          and appears_as_word(w))
    check("no distinctive word from any subject, name or body survives",
          not leaked_words,
          f"leaked: {leaked_words[:8]}" if leaked_words else "none")

    rule("No address-shaped or id-shaped values left behind")
    addresses = set(re.findall(r"[\w.+-]+@[\w.-]+\.\w+", combined))
    allowed = {a for a in addresses
               if a.endswith(".example") or a == "privategpt@example.com"}
    check("every email address in the report is a stand-in",
          addresses == allowed,
          f"real-looking: {sorted(addresses - allowed)[:5]}")

    real_ids = set()
    from outlook_reader import OutlookReader
    reader = OutlookReader(backend="mock")
    reader.connect()
    for record in reader.read_sent() + reader.read_inbox():
        real_ids.add(str(record["entry_id"]))
    reader.close()
    check("no raw Outlook EntryID appears",
          not any(i in combined for i in real_ids),
          f"{len(real_ids)} ids checked")

    rule("The report is still diagnostically useful")
    check("stand-in addresses present", "@domain1.example" in combined)
    check("the user's own address is identifiable as 'me'",
          "me@own-domain.example" in combined)
    check("subject lengths are reported", "<subject," in combined)
    check("reply prefixes survive for thread-matching",
          re.search(r"(SV|RE|VS|FW):\s*<subject,", combined, re.I) is not None)
    check("attachment extensions survive",
          re.search(r"<file\d+, \.\w+,", combined) is not None)
    check("id shapes survive", re.search(r"<id\d+, \w+, \d+ chars>",
                                         combined) is not None)
    check("counts and day arithmetic survive",
          "flagged" in combined and "days" in combined)
    check("the prompt template is shown in full",
          "=== PRIVATEGPT PROMPT" in combined
          and "=== END OF PROMPT ===" in combined)
    check("the report states it is safe to share",
          "REDACTED REPORT - safe to share." in combined)

    rule("Stand-ins are stable and collision-free")
    from redaction import Redactor
    red = Redactor(user_email=config.USER_EMAIL)
    first = red.address("someone@example.org")
    check("the same address maps to the same stand-in",
          red.address("someone@example.org") == first, first)
    check("case differences do not create a second stand-in",
          red.address("SOMEONE@example.org") == first)
    other = red.address("another@example.org")
    check("different addresses get different stand-ins", other != first,
          f"{first} vs {other}")
    check("the same domain maps to the same stand-in domain",
          first.split("@")[1] == other.split("@")[1])
    check("a different domain maps to a different stand-in domain",
          red.address("third@elsewhere.net").split("@")[1]
          != first.split("@")[1])

    rule("An Exchange legacy DN keeps its structure and loses its values")
    dn = ("/o=ExchangeLabs/ou=Exchange Administrative Group "
          "(FYDIBOHF23SPDLT)/cn=Recipients/cn=a1b2c3d4marcus")
    masked = red.dn(dn)
    check("DN key names survive", "/o=<" in masked and "/cn=<" in masked, masked)
    check("DN values do not", "ExchangeLabs" not in masked
          and "marcus" not in masked)

    rule("--no-redact really is unredacted (so the test above means something)")
    code2, console2, report2 = run_probe(False, tmpdir)
    check("probe exits 0", code2 == 0, f"exit={code2}")
    combined2 = console2 + report2
    check("real content is present when redaction is off",
          any(s in combined2 for s in exact),
          "confirms the redacted run was actually redacting")
    check("it says so, loudly",
          "UNREDACTED REPORT - contains real mail data. DO NOT SHARE."
          in combined2)

    print(f"\n{'=' * 72}")
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED:")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("All redaction checks passed. The redacted report carries no "
          "mailbox content.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
