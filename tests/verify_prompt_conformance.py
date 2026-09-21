"""
verify_prompt_conformance.py — proves the generated PrivateGPT prompt matches
the build brief character for character.

The expected prompt below is transcribed directly from the brief's template,
with the placeholders filled in for a fixture thread whose values are known
exactly. The test asserts equality of the whole string, so ANY drift — a
changed word, a different dash, a lost blank line, altered indentation — fails
with a line-by-line diff.

Run:  python tests\verify_prompt_conformance.py
"""

import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import prompt_builder  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


# A fixture whose every rendered value is known, so the expected prompt can be
# written out in full rather than assembled by the same code under test.
FIXTURE = {
    "subject": "Q3 budget revision - figures for review",
    "participants": ["Lars Petersen <lars.petersen@nordvind.dk>"],
    "counterparties": [{"name": "Lars Petersen",
                        "address": "lars.petersen@nordvind.dk"}],
    "date_display": "10 Sep 2026, 09:30",
    "days_waiting": 9,
    "attachments": ["budget_q3_v2.xlsx", "assumptions.docx"],
    "my_last_message": "Hi Lars,\n\nAttached are the revised Q3 figures.",
    "their_last_message": "",
    "entry_id": "SENT-S1",
    "conversation_id": "CONV-S1",
}

# Transcribed from the build brief. The en dash in "3-5 sentences" and the em
# dash on the URGENCY line are the brief's own characters.
EXPECTED = (
    "=== PRIVATEGPT PROMPT (v1) ===\n"
    "\n"
    "You are a professional assistant helping to manage email\n"
    "follow-ups. Below is a thread context. Based only on the\n"
    "information provided, suggest a concise follow-up action.\n"
    "Format your response exactly as specified at the end of\n"
    "this prompt.\n"
    "\n"
    "--- THREAD CONTEXT ---\n"
    "Subject: Q3 budget revision - figures for review\n"
    "Participants: Lars Petersen <lars.petersen@nordvind.dk>, "
    "Me <mskovbaek@cedra.dk>\n"
    "Original sent date: 10 Sep 2026, 09:30\n"
    "Days without reply: 9\n"
    "Attachments: budget_q3_v2.xlsx, assumptions.docx\n"
    "My last message: Hi Lars, Attached are the revised Q3 figures.\n"
    "Their last message (if any): No reply received\n"
    "\n"
    "--- YOUR TASK ---\n"
    "1. Summarise the situation in 2 sentences\n"
    "2. Suggest a follow-up action: reply, call, escalate,\n"
    "   or close\n"
    "3. If a reply is suggested, draft it (3\u20135 sentences,\n"
    "   professional tone, no fluff)\n"
    "4. Rate urgency: Low / Medium / High with one-line reason\n"
    "\n"
    "--- REQUIRED RESPONSE FORMAT ---\n"
    "Respond using exactly this structure with these exact\n"
    "headings. Do not add any text outside this structure:\n"
    "\n"
    "SITUATION: [2 sentence summary]\n"
    "ACTION: [reply / call / escalate / close]\n"
    'DRAFT REPLY: [draft text, or "N/A" if action is not reply]\n'
    "URGENCY: [Low / Medium / High] \u2014 [one line reason]\n"
    "\n"
    "=== END OF PROMPT ==="
)


def main():
    print("PROMPT CONFORMANCE — generated output vs the build brief\n")
    print(f"  config.PROMPT_VERSION = {config.PROMPT_VERSION}")
    check("brief specifies template v1, config agrees",
          config.PROMPT_VERSION == 1, str(config.PROMPT_VERSION))

    actual = prompt_builder.build(FIXTURE)

    print("\n  Generated prompt:\n")
    for line in actual.split("\n"):
        print(f"    | {line}")
    print()

    if actual == EXPECTED:
        check("generated prompt matches the brief byte for byte", True,
              f"{len(actual)} characters")
    else:
        check("generated prompt matches the brief byte for byte", False)
        print("\n  DIFF (expected vs generated):")
        diff = difflib.unified_diff(
            EXPECTED.split("\n"), actual.split("\n"),
            fromfile="brief", tofile="generated", lineterm="",
        )
        for line in diff:
            print(f"    {line}")
        # Character-level detail for invisible differences such as dashes.
        for index, (want, got) in enumerate(zip(EXPECTED, actual)):
            if want != got:
                print(f"\n  First difference at character {index}: "
                      f"expected {want!r} (U+{ord(want):04X}), "
                      f"got {got!r} (U+{ord(got):04X})")
                print(f"  Context expected: ...{EXPECTED[max(0,index-40):index+20]!r}")
                print(f"  Context actual  : ...{actual[max(0,index-40):index+20]!r}")
                break

    # -- structural guarantees the brief calls out explicitly ---------------
    print("\n  Structural requirements from the brief:")
    check("version number is interpolated into the header",
          f"=== PRIVATEGPT PROMPT (v{config.PROMPT_VERSION}) ===" in actual)
    check("ends with the end marker", actual.endswith("=== END OF PROMPT ==="))

    lines = actual.split("\n")
    check("exactly one THREAD CONTEXT marker",
          lines.count("--- THREAD CONTEXT ---") == 1)
    check("exactly one YOUR TASK marker", lines.count("--- YOUR TASK ---") == 1)
    check("exactly one RESPONSE FORMAT marker",
          lines.count("--- REQUIRED RESPONSE FORMAT ---") == 1)

    check("en dash present in '3-5 sentences'", "3\u20135 sentences" in actual)
    check("em dash present on the URGENCY line",
          "URGENCY: [Low / Medium / High] \u2014 [one line reason]" in actual)

    check("the four response headings appear in the brief's order",
          [l.split(":")[0] for l in lines if l.startswith(
              ("SITUATION:", "ACTION:", "DRAFT REPLY:", "URGENCY:"))]
          == ["SITUATION", "ACTION", "DRAFT REPLY", "URGENCY"])

    # -- placeholder substitution ------------------------------------------
    print("\n  Placeholder substitution:")
    check("no unsubstituted context placeholder remains",
          not any(token in actual for token in
                  ("{subject}", "{participants}", "{days}", "{attachments}",
                   "{my_last_message}", "{their_last_message}", "{version}",
                   "{sent_date}")))
    check("attachment names listed comma-separated",
          "Attachments: budget_q3_v2.xlsx, assumptions.docx" in actual)
    check("'None' used when there are no attachments",
          "Attachments: None" in prompt_builder.build(dict(FIXTURE, attachments=[])))
    check("'No reply received' used when they never replied",
          "Their last message (if any): No reply received" in actual)
    check("their reply is included when one exists",
          "Their last message (if any): We will revert Monday."
          in prompt_builder.build(
              dict(FIXTURE, their_last_message="We will revert Monday.")))
    check("user address always present in participants",
          config.USER_EMAIL in actual)

    # -- truncation at 500 words -------------------------------------------
    print("\n  500-word truncation:")
    long_item = dict(FIXTURE, my_last_message=" ".join(
        f"w{n}" for n in range(1, 801)))
    long_prompt = prompt_builder.build(long_item)
    body_line = next(l for l in long_prompt.split("\n")
                     if l.startswith("My last message: "))
    body = body_line[len("My last message: "):]
    kept = body.split(" [... truncated")[0].split()
    check(f"truncated to exactly {config.PROMPT_BODY_TRUNCATE_WORDS} words",
          len(kept) == config.PROMPT_BODY_TRUNCATE_WORDS, f"{len(kept)} words")
    check("truncation is marked, not silent", "[... truncated at 500 words]" in body)
    check("last kept word is the 500th", kept[-1] == "w500", kept[-1])
    check("bodies under the limit are untouched",
          "[... truncated" not in actual)
    check("truncated body stays on one line",
          len([l for l in long_prompt.split("\n")
               if l.startswith("My last message")]) == 1)

    # -- the prompt must be self-contained ---------------------------------
    print("\n  Self-containment (the brief: 'no references to external files'):")
    check("no file paths in the prompt",
          not any(token in actual for token in ("C:\\", "/home/", ".py", "http")))
    check("no template-internal jargon leaks",
          "entry_id" not in actual and "conversation_id" not in actual)

    # -- registry / versioning ---------------------------------------------
    print("\n  Versioning:")
    check("changelog has an entry for the current version",
          any(v == config.PROMPT_VERSION
              for v, _, _, _ in prompt_builder.PROMPT_CHANGELOG),
          str(prompt_builder.PROMPT_CHANGELOG))
    check("prompt_version() agrees with config",
          prompt_builder.prompt_version() == config.PROMPT_VERSION)
    check("unknown prompt type is rejected, not silently defaulted",
          _raises(lambda: prompt_builder.build(FIXTURE, "meeting-prep")))

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("prompt_builder.py — MATCHES THE BRIEF EXACTLY")
    return 0


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
