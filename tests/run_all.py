"""
run_all.py — run every Phase 1 verification script in order.

Each script runs in its own process so the temp-directory redirection one
script applies cannot leak into the next.

Run:  python tests\run_all.py
      python tests\run_all.py --quiet     (summary only)
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SCRIPTS = [
    ("db.py", "verify_db.py"),
    ("prompt_builder.py vs the brief, byte for byte",
     "verify_prompt_conformance.py"),
    ("mock_outlook.py + outlook_reader.py", "verify_outlook_reader.py"),
    ("email_processor.py + calendar_reader.py", "verify_processors.py"),
    ("sync.py + prompt_builder.py + response_renderer.py",
     "verify_sync_prompt_response.py"),
    ("app.py + api.py + templates", "verify_web.py"),
]


def main():
    quiet = "--quiet" in sys.argv
    results = []
    for label, script in SCRIPTS:
        print(f"\n{'=' * 70}\nRUNNING: {script}  ({label})\n{'=' * 70}")
        completed = subprocess.run(
            [sys.executable, os.path.join(HERE, script)],
            capture_output=quiet, text=True,
        )
        if quiet and completed.returncode != 0:
            print(completed.stdout)
            print(completed.stderr)
        results.append((script, completed.returncode == 0))

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for script, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {script}")
    failed = [s for s, ok in results if not ok]
    if failed:
        print(f"\n{len(failed)} script(s) FAILED: {failed}")
        return 1
    print(f"\nAll {len(results)} verification scripts passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
