"""
verify_exe.py — end-to-end acceptance test against the COMPILED .exe.

Nothing here imports the application. It launches dist\\Jarvis.exe as a black
box in a clean directory, drives it over HTTP exactly as the browser does, and
checks the result against the build brief. It then reads back the sync.log the
.exe wrote and asserts the logging requirements.

Run:  python tests\verify_exe.py
      python tests\verify_exe.py --exe path\\to\\Jarvis.exe --port 5099
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


class Client:
    def __init__(self, base):
        self.base = base

    def get(self, path, raw=False):
        with urllib.request.urlopen(self.base + path, timeout=20) as response:
            body = response.read().decode("utf-8")
            return body if raw else json.loads(body)

    def post(self, path, payload=None):
        data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            self.base + path, data=data, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))


# The brief's template, with this fixture's values substituted. Identical
# expectation to verify_prompt_conformance.py, restated here so the .exe is
# checked against the brief and not against the source it was built from.
def expected_prompt(subject, participants, sent_date, days, attachments,
                    my_last, their_last):
    return (
        "=== PRIVATEGPT PROMPT (v1) ===\n"
        "\n"
        "You are a professional assistant helping to manage email\n"
        "follow-ups. Below is a thread context. Based only on the\n"
        "information provided, suggest a concise follow-up action.\n"
        "Format your response exactly as specified at the end of\n"
        "this prompt.\n"
        "\n"
        "--- THREAD CONTEXT ---\n"
        f"Subject: {subject}\n"
        f"Participants: {participants}\n"
        f"Original sent date: {sent_date}\n"
        f"Days without reply: {days}\n"
        f"Attachments: {attachments}\n"
        f"My last message: {my_last}\n"
        f"Their last message (if any): {their_last}\n"
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


GOOD_RESPONSE = """SITUATION: The revised Q3 figures went out with no reply. The contingency line is still unconfirmed and the forecast cannot be locked.
ACTION: reply
DRAFT REPLY: Hi Lars, following up on the revised Q3 figures. We still need the contingency line confirmed before the forecast can be locked. Could you come back to me by Wednesday, or point me to whoever should sign it off?
URGENCY: Medium \u2014 The forecast lock date is approaching but has not passed."""

MALFORMED = "Sure! I'd just give Lars a quick call about the budget, sounds urgent."


def drain(process, sink):
    """Continuously read the child's stdout into `sink`.

    Without this the OS pipe buffer fills, the child blocks on its next write,
    and the server appears to hang. The drained output is also the console
    transcript this test prints at the end.
    """
    def _pump():
        try:
            for line in process.stdout:
                sink.append(line.rstrip("\n"))
        except (ValueError, OSError):
            pass  # pipe closed on shutdown

    thread = threading.Thread(target=_pump, daemon=True)
    thread.start()
    return thread


def port_is_free(port):
    """True if nothing is listening on the loopback port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port(preferred):
    """Use the preferred port when free, otherwise let the OS pick one.

    Without this, a leftover Jarvis.exe from an earlier run can answer on the
    preferred port and the test happily checks the WRONG process.
    """
    if port_is_free(preferred):
        return preferred
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        chosen = sock.getsockname()[1]
    print(f"  NOTE: port {preferred} is already in use; using {chosen} instead "
          f"so this test cannot talk to another process.")
    return chosen


def wait_for_server(client, process, instance_id, timeout=60):
    """Wait until OUR instance is serving, not merely until something answers.

    A one-file PyInstaller .exe runs the app in a child of the launched
    process, so the Popen pid cannot identify it. Each launch therefore gets a
    unique JARVIS_INSTANCE_ID which /healthz echoes back; anything else
    answering on this port is somebody else's process and is rejected.
    """
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        if process.poll() is not None:
            return False, f"process exited early with code {process.returncode}"
        try:
            health = client.get("/healthz")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.5)
            continue
        if health.get("instance_id") == instance_id:
            return True, None
        last_error = (f"another process answered on this port "
                      f"(instance {health.get('instance_id')!r}, "
                      f"expected {instance_id!r})")
        time.sleep(0.5)
    return False, f"timed out after {timeout}s (last error: {last_error})"


def stop(process, port, timeout=20):
    """Terminate the .exe and wait until the port is genuinely released.

    A one-file PyInstaller .exe is a bootloader that runs the real application
    in a child process. Terminating the launched process alone leaves that
    child alive, still holding the listening socket, so the whole tree has to
    go — that is what taskkill /T does.
    """
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, check=False,
            )
        else:  # pragma: no cover - the app targets Windows
            process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_is_free(port):
            return True
        time.sleep(0.25)
    return False


def launch(exe, workdir, port, console):
    """Start the .exe with a fresh instance id. Returns (process, id)."""
    instance_id = uuid.uuid4().hex[:12]
    environment = dict(os.environ, JARVIS_INSTANCE_ID=instance_id)
    process = subprocess.Popen(
        [exe, "--backend", "mock", "--no-browser", "--port", str(port)],
        cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=environment,
    )
    drain(process, console)
    return process, instance_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=os.path.join(ROOT, "dist", "Jarvis.exe"))
    parser.add_argument("--port", type=int, default=5099)
    parser.add_argument("--keep", action="store_true",
                        help="keep the temp directory for inspection")
    args = parser.parse_args()

    if not os.path.isfile(args.exe):
        print(f"No executable at {args.exe}. Build it first:\n"
              f"  jarvis-env\\Scripts\\pyinstaller.exe --noconfirm jarvis.spec")
        return 1

    workdir = tempfile.mkdtemp(prefix="jarvis_exe_acceptance_")
    exe = os.path.join(workdir, "Jarvis.exe")
    shutil.copy2(args.exe, exe)

    rule("0. The executable, in isolation")
    size_mb = os.path.getsize(exe) / (1024 * 1024)
    print(f"  source   : {args.exe}")
    print(f"  copied to: {exe}")
    print(f"  size     : {size_mb:.2f} MB")
    print(f"  directory: {os.listdir(workdir)}")
    check("only the .exe is present - no source, no templates, no venv",
          os.listdir(workdir) == ["Jarvis.exe"], str(os.listdir(workdir)))

    port = find_free_port(args.port)
    base = f"http://127.0.0.1:{port}"
    client = Client(base)

    print(f"\n  Launching: Jarvis.exe --backend mock --no-browser "
          f"--port {port}")
    console = []
    process, instance_id = launch(exe, workdir, port, console)

    try:
        ok, error = wait_for_server(client, process, instance_id)
        if not ok:
            print(f"  FAILED to start: {error}")
            print("\n".join(console[:60]))
            return 1
        health = client.get("/healthz")
        print(f"  Server is up. instance={health['instance_id']} "
              f"pid={health['pid']} (launched pid={process.pid})")
        check("serving instance is the one this test launched",
              health["instance_id"] == instance_id)
        check("data directory is the test's own directory",
              os.path.normcase(health["data_dir"]) == os.path.normcase(workdir),
              health["data_dir"])

        # ==================================================================
        rule("1. The .exe created its own database and log beside itself")
        # ==================================================================
        files = sorted(os.listdir(workdir))
        print(f"  directory now: {files}")
        check("jarvis.db created next to the .exe", "jarvis.db" in files)
        check("sync.log created next to the .exe", "sync.log" in files)

        health = client.get("/healthz")
        check("/healthz responds", health["ok"] is True, str(health))
        check("running the mock backend", health["backend"] == "mock")

        # ==================================================================
        rule("2. The three dashboard sections, from the compiled binary")
        # ==================================================================
        status = client.get("/api/v1/status")
        counts = status["counts"]
        print(f"  counts: {json.dumps(counts, indent=2)}")
        check("Feature 2 - overdue inbound flagged",
              counts["overdue_inbound"] == 6, str(counts["overdue_inbound"]))
        check("Feature 1 - sent awaiting reply flagged",
              counts["awaiting_reply"] == 5, str(counts["awaiting_reply"]))
        check("Feature 3 - meetings in the window",
              counts["meeting"] == 7, str(counts["meeting"]))
        check("high-priority detection ran", counts["high_priority"] == 2,
              str(counts["high_priority"]))
        check("meeting flags computed",
              counts["meetings_pending"] == 2 and counts["meetings_unprepared"] == 2,
              f"pending={counts['meetings_pending']} "
              f"unprepared={counts['meetings_unprepared']}")
        check("prompt template version reported",
              status["prompt_version"] == 1, str(status["prompt_version"]))
        check("identity is the address from the brief",
              status["user_email"] == "mskovbaek@cedra.dk", status["user_email"])

        items = client.get("/api/v1/items")
        bands = {b["label"]: len(b["items"]) for b in items["awaiting_reply_bands"]}
        print(f"  age bands: {bands}")
        check("age bands are the brief's three",
              list(bands) == ["7-14 days", "14-30 days", "30+ days"], str(list(bands)))
        check("threads distributed across bands",
              bands == {"7-14 days": 3, "14-30 days": 1, "30+ days": 1}, str(bands))

        # ==================================================================
        rule("3. THE PROMPT, extracted from the running .exe")
        # ==================================================================
        target = next(i for i in items["awaiting_reply"] if i["attachments"])
        print(f"  thread: {target['subject']!r}")
        print(f"  entry_id: {target['entry_id']}  "
              f"attachments: {target['attachments']}\n")

        query = (f"/api/v1/prompt?category=awaiting_reply"
                 f"&conversation_id={target['conversation_id']}")
        result = client.get(query)
        prompt = result["prompt"]

        for line in prompt.split("\n"):
            print(f"    | {line}")
        print()

        want = expected_prompt(
            subject=target["subject"],
            participants=", ".join(target["participants"] +
                                   ["Me <mskovbaek@cedra.dk>"]),
            sent_date=target["date_display"],
            days=target["days_waiting"],
            attachments=", ".join(target["attachments"]),
            my_last=" ".join((target["my_last_message"] or "").split()),
            their_last="No reply received",
        )
        check("prompt served by the .exe matches the brief byte for byte",
              prompt == want, f"{len(prompt)} chars")
        if prompt != want:
            import difflib
            for line in difflib.unified_diff(
                    want.split("\n"), prompt.split("\n"),
                    fromfile="brief", tofile="from .exe", lineterm=""):
                print(f"    {line}")

        check("the en dash survived compilation", "3\u20135 sentences" in prompt)
        check("the em dash survived compilation",
              "High] \u2014 [one line" in prompt)
        check("real attachment names are in the prompt",
              all(name in prompt for name in target["attachments"]))
        check("prompt is self-contained - no file references",
              not any(t in prompt for t in ("C:\\", ".py", "http")))
        check("API reports the template version",
              result["prompt_version"] == 1)

        no_attach = next(i for i in items["awaiting_reply"] if not i["attachments"])
        other = client.get(f"/api/v1/prompt?category=awaiting_reply"
                           f"&conversation_id={no_attach['conversation_id']}")
        check("'Attachments: None' when the thread has none",
              "Attachments: None" in other["prompt"])

        inbound = items["overdue_inbound"][0]
        inbound_prompt = client.get(
            f"/api/v1/prompt?category=overdue_inbound"
            f"&conversation_id={inbound['conversation_id']}")["prompt"]
        check("prompts also build for the inbound section",
              inbound_prompt.startswith("=== PRIVATEGPT PROMPT (v1) ===")
              and "Their last message (if any): " in inbound_prompt)
        check("their message appears when they did write",
              "No reply received" not in inbound_prompt.split(
                  "Their last message (if any): ")[1][:40])

        # ==================================================================
        rule("4. Response parsing and the fixed card")
        # ==================================================================
        code, saved = client.post("/api/v1/responses", {
            "category": "awaiting_reply",
            "conversation_id": target["conversation_id"],
            "entry_id": target["entry_id"],
            "raw": GOOD_RESPONSE,
        })
        check("well-formed response accepted", code == 200 and saved["ok"],
              str(saved)[:100])
        card = saved["card"]
        print(f"  parsed SITUATION : {card['situation'][:70]}...")
        print(f"  parsed ACTION    : {card['action']}")
        print(f"  parsed URGENCY   : {card['urgency']} - {card['urgency_reason']}")
        print(f"  parsed DRAFT     : {card['draft_reply'][:70]}...")
        check("all four headings parsed",
              card["situation"] and card["action"] == "reply"
              and card["urgency"] == "Medium" and card["draft_reply"])
        check("em dash accepted as the urgency separator",
              card["urgency_reason"].startswith("The forecast lock"))
        html = saved["html"]
        check("card HTML rendered from response_card.html",
              'class="response-card"' in html)
        check("card shows SUBJECT and DAYS WAITING",
              f"SUBJECT: {target['subject']}" in html
              and f"DAYS WAITING: {target['days_waiting']}" in html)
        check("card offers 'Open reply in Outlook'",
              "Open reply in Outlook" in html)

        code, bad = client.post("/api/v1/responses", {
            "category": "awaiting_reply",
            "conversation_id": target["conversation_id"],
            "raw": MALFORMED,
        })
        expected_error = ("Response format does not match the expected structure. "
                          "Please check the PrivateGPT output and try again.")
        check("malformed response rejected", bad["ok"] is False)
        check("error message is exactly the brief's wording",
              bad["error"] == expected_error, bad.get("error", ""))
        check("nothing guessed or reformatted", "card" not in bad)

        history = client.get(f"/api/v1/responses/{target['entry_id']}")
        check("only the valid response was persisted",
              len(history["responses"]) == 1, str(len(history["responses"])))
        check("raw response stored verbatim",
              history["responses"][0]["raw_response"] == GOOD_RESPONSE)
        check("parsed fields stored alongside it",
              history["responses"][0]["urgency"] == "Medium"
              and history["responses"][0]["action"] == "reply")
        check("prompt version stamped on the saved response",
              history["responses"][0]["prompt_version"] == 1)

        page = client.get("/", raw=True)
        check("saved card re-renders on the dashboard",
              "The revised Q3 figures went out with no reply" in page)

        # ==================================================================
        rule("5. Outlook actions (mock backend, but the real code path)")
        # ==================================================================
        code, opened = client.post("/api/v1/outlook/open",
                                   {"entry_id": target["entry_id"]})
        check("open item succeeds", code == 200 and opened["ok"], str(opened))
        code, replied = client.post("/api/v1/outlook/reply", {
            "entry_id": target["reply_entry_id"],
            "body": card["draft_reply"],
        })
        check("reply-all draft opens", code == 200 and replied["ok"], str(replied))
        code, missing = client.post("/api/v1/outlook/open",
                                    {"entry_id": "NO-SUCH-ENTRY-ID"})
        check("unknown EntryID fails cleanly with 503, no crash", code == 503,
              str(code))
        check("server still alive after that failure",
              client.get("/healthz")["ok"] is True)

        # ==================================================================
        rule("6. Snooze, dismiss, purge")
        # ==================================================================
        victim = items["overdue_inbound"][0]
        before = len(client.get("/api/v1/items/overdue_inbound")["items"])
        code, snoozed = client.post("/api/v1/actions/snooze", {
            "category": "overdue_inbound",
            "conversation_id": victim["conversation_id"],
            "entry_id": victim["entry_id"],
            "subject": victim["subject"],
            "seconds": 3,
        })
        after = client.get("/api/v1/items/overdue_inbound")["items"]
        check("snoozed item disappears", len(after) == before - 1,
              f"{before} -> {len(after)}")
        print("  waiting 3.5s for the snooze to expire...")
        time.sleep(3.5)
        back = client.get("/api/v1/items/overdue_inbound")["items"]
        check("it reappears when the snooze expires", len(back) == before,
              str(len(back)))

        code, default_snooze = client.post("/api/v1/actions/snooze", {
            "category": "overdue_inbound",
            "conversation_id": victim["conversation_id"]})
        check("default snooze is 3 days per the brief",
              default_snooze["days"] == 3, str(default_snooze["days"]))
        client.post("/api/v1/actions/restore", {
            "category": "overdue_inbound",
            "conversation_id": victim["conversation_id"]})

        doomed = items["awaiting_reply"][0]
        client.post("/api/v1/actions/dismiss", {
            "category": "awaiting_reply",
            "conversation_id": doomed["conversation_id"],
            "entry_id": doomed["entry_id"],
            "subject": doomed["subject"]})
        left = client.get("/api/v1/items/awaiting_reply")["items"]
        check("dismissed item disappears", len(left) == 4, str(len(left)))

        code, synced = client.post("/api/v1/sync", {})
        check("manual sync runs", synced["ok"], str(synced.get("result")))
        still = client.get("/api/v1/items/awaiting_reply")["items"]
        check("dismissed item does not return after a sync", len(still) == 4,
              str(len(still)))

        code, purged = client.post("/api/v1/purge", {})
        check("manual purge runs", purged["ok"], str(purged))
        check("purge honours the 30-day window from the brief",
              purged["retention_days"] == 30)
        check("recent dismissal survives the purge",
              len(client.get("/api/v1/items/awaiting_reply")["items"]) == 4)

        # ==================================================================
        rule("7. The dashboard page served by the .exe")
        # ==================================================================
        page = client.get("/", raw=True)
        print(f"  page size: {len(page)} bytes")
        for needle, label in [
            ("Awaiting your reply", "section 1 title"),
            ("No response received", "section 2 title"),
            ("Upcoming meetings", "section 3 title"),
            ("Snooze 3 days", "snooze button"),
            ("No reply needed", "dismiss button"),
            ("Copy PrivateGPT prompt", "copy prompt button"),
            ("Paste PrivateGPT response here", "response panel label"),
            ("Save and render", "save button"),
            ("Last sync:", "last sync timestamp"),
            ("Clear old records", "settings purge button"),
            ("7-14 days", "age band"),
            ("&#128206;", "attachment paperclip"),
            ("Not accepted or declined", "pending meeting indicator"),
            ("No prep found", "unprepared meeting flag"),
        ]:
            check(f"page contains {label}", needle in page)

        css = client.get("/static/style.css", raw=True)
        js = client.get("/static/app.js", raw=True)
        check("bundled stylesheet served", len(css) > 3000, f"{len(css)} bytes")
        check("bundled script served", len(js) > 3000, f"{len(js)} bytes")
        check("no external references anywhere in the served page",
              "//" not in page.replace("http://127.0.0.1", "")
              .replace("<!--", "").replace("-->", "")
              or not any(h in page.lower() for h in
                         ("cdn.", "googleapis", "jsdelivr", "unpkg")))

        # ==================================================================
        rule("8. The log the .exe wrote")
        # ==================================================================
        with open(os.path.join(workdir, "sync.log"), encoding="utf-8") as handle:
            log_text = handle.read()
        print(log_text)

        check("startup banner recorded", "Jarvis starting" in log_text)
        check("banner records the frozen state",
              "frozen executable : True" in log_text)
        check("banner records identity", "mskovbaek@cedra.dk" in log_text)
        check("banner records the prompt version", "prompt version    : v1" in log_text)
        check("banner records the database path", "jarvis.db" in log_text)
        check("schema creation logged", "migrations applied" in log_text)
        check("sync start logged", "Sync started" in log_text)
        check("raw pull counts logged", "Pulled from Outlook:" in log_text)
        check("per-category counts logged",
              "awaiting your reply:" in log_text
              and "no response received:" in log_text
              and "meetings:" in log_text)
        check("age-band breakdown logged", "7-14 days:" in log_text)
        check("prompt generation logged", "Built followup prompt v1" in log_text)
        check("malformed response logged",
              "Rejected malformed PrivateGPT response" in log_text)
        check("open-item action logged", "Displayed item" in log_text)
        check("reply draft logged", "Reply All draft" in log_text)
        check("snooze logged", "Snoozed" in log_text)
        check("dismissal logged", "no reply needed" in log_text)
        check("purge logged", "Manual purge" in log_text)
        check("HTTP requests logged", "POST /api/v1/responses" in log_text)
        check("timestamps present on every line",
              all(line[:4].isdigit() for line in log_text.strip().split("\n")
                  if line.strip() and not line.startswith(" ")))
        check("no traceback in the log", "Traceback" not in log_text)
        check("no mojibake from the em dash",
              "\ufffd" not in log_text)

        # ==================================================================
        rule("9. Outlook unavailable, and recovery")
        # ==================================================================
        # The running .exe uses the mock backend, so this is exercised through
        # the API's error path rather than by killing Outlook.
        code, failed = client.post("/api/v1/outlook/reply",
                                   {"entry_id": "STILL-NOT-A-REAL-ID"})
        check("bad reply request returns 503 rather than crashing", code == 503)
        check("server still healthy", client.get("/healthz")["ok"])
        code, recovered = client.post("/api/v1/sync", {})
        check("sync still works afterwards", recovered["ok"])

        # ==================================================================
        rule("10. Restart: does state survive?")
        # ==================================================================
        released = stop(process, port)
        check("the .exe shuts down and releases the port", released)
        print("  stopped. restarting against the same directory...")

        process, instance_id = launch(exe, workdir, port, console)
        ok, error = wait_for_server(client, process, instance_id)
        check("the .exe restarts against an existing database", ok, str(error))
        if ok:
            check("the restarted server is a genuinely new instance",
                  client.get("/healthz")["instance_id"] == instance_id)
            again = client.get(f"/api/v1/responses/{target['entry_id']}")
            check("saved PrivateGPT response survived the restart",
                  len(again["responses"]) == 1
                  and again["responses"][0]["raw_response"] == GOOD_RESPONSE)
            survivors = client.get("/api/v1/items/awaiting_reply")["items"]
            check("dismissal survived the restart", len(survivors) == 4,
                  str(len(survivors)))
            with open(os.path.join(workdir, "sync.log"), encoding="utf-8") as handle:
                restart_log = handle.read()
            check("second run appended to the same log, did not truncate it",
                  restart_log.count("Jarvis starting") == 2,
                  str(restart_log.count("Jarvis starting")))
            check("no migration re-applied on the existing database",
                  "already at v1" in restart_log)

        # ==================================================================
        rule("10b. A second instance on the same port fails clearly")
        # ==================================================================
        clash_dir = tempfile.mkdtemp(prefix="jarvis_exe_clash_")
        clash_exe = os.path.join(clash_dir, "Jarvis.exe")
        shutil.copy2(args.exe, clash_exe)
        clash = subprocess.run(
            [clash_exe, "--backend", "mock", "--no-browser", "--port", str(port)],
            cwd=clash_dir, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=90,
        )
        output = (clash.stdout or "") + (clash.stderr or "")
        print(f"  exit code: {clash.returncode}")
        for line in output.strip().split("\n")[-8:]:
            print(f"  {line}")
        check("second instance exits non-zero instead of hanging",
              clash.returncode == 2, str(clash.returncode))
        check("it says the port is in use",
              "already in use" in output, output[-200:].replace("\n", " "))
        check("it identifies the occupant as another Jarvis",
              "another Jarvis instance" in output)
        check("it suggests a way forward", "--port" in output)
        check("no raw socket traceback shown to the user",
              "Traceback" not in output and "WinError" not in output)
        check("a launch that cannot serve leaves no database behind",
              not os.path.exists(os.path.join(clash_dir, "jarvis.db")),
              str(sorted(os.listdir(clash_dir))))
        check("the running instance is unaffected",
              client.get("/healthz")["instance_id"] == instance_id)
        shutil.rmtree(clash_dir, ignore_errors=True)

        # ==================================================================
        rule("11. Console output the .exe produced")
        # ==================================================================
        for line in console:
            print(f"  {line}")
        check("console shows the startup banner",
              any("Jarvis starting" in line for line in console))
        check("console shows sync results",
              any("Sync OK" in line for line in console))
        check("console stays readable - request spam is file-only",
              not any("GET /api/v1/" in line for line in console),
              f"{sum(1 for l in console if 'GET /api/v1/' in l)} request lines")
        check("console carries no mojibake",
              not any("�" in line for line in console))

    finally:
        stop(process, port)
        if args.keep:
            print(f"\nTemp directory kept: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    print("\n" + "=" * 72)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("COMPILED .EXE - ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
