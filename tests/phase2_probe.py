"""
phase2_probe.py — WORK PC ONLY. Read-only probe against the real mailbox.

A thin wrapper around probe.py so the probe can be run from source. The
compiled .exe exposes exactly the same thing as:

    Jarvis.exe --probe

Run from source with Outlook open:

    jarvis-env\\Scripts\\activate
    python tests\\phase2_probe.py

Redaction is ON by default, so the report is safe to share. Add --full for
more sample rows, --no-redact for the unredacted version (your eyes only).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import probe  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true",
                        help="print more sample rows")
    parser.add_argument("--redact", action="store_true",
                        help="accepted and ignored; redaction is on by default")
    parser.add_argument("--no-redact", dest="no_redact", action="store_true",
                        help="do NOT replace real values with stand-ins; the "
                             "report then contains real mail data")
    parser.add_argument("--report", default=None,
                        help="write the report here instead of "
                             "probe-output.txt")
    parser.add_argument("--backend", default="com", choices=["com", "mock"],
                        help="'mock' exercises the probe without a mailbox")
    args = parser.parse_args()
    return probe.run(full=args.full, redact=not args.no_redact,
                     backend=args.backend, report_path=args.report)


if __name__ == "__main__":
    sys.exit(main())
