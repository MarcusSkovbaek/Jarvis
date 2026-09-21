"""
phase2_probe.py — WORK PC ONLY. Read-only probe against the real mailbox.

A thin wrapper around probe.py so the probe can be run from source. The
compiled .exe exposes exactly the same thing as:

    Jarvis.exe --probe

Run from source with Outlook open:

    jarvis-env\\Scripts\\activate
    python tests\\phase2_probe.py

Add --full for more sample rows, --redact to mask addresses and subjects.
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
                        help="mask addresses and subjects in the output")
    parser.add_argument("--backend", default="com", choices=["com", "mock"],
                        help="'mock' exercises the probe without a mailbox")
    args = parser.parse_args()
    return probe.run(full=args.full, redact=args.redact, backend=args.backend)


if __name__ == "__main__":
    sys.exit(main())
