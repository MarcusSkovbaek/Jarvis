"""
verify_no_egress.py — proves nothing Jarvis does reaches the network.

"It all stays on your machine" is the central promise of this app, and it is
the one promise a reader cannot check by reading the code, because a single
import three levels down could break it. This test checks it by observation
instead of by inspection.

It installs a CPython audit hook (sys.addaudithook), which fires for every
socket connection, every DNS lookup and every URL opened anywhere in the
process — including inside C code and inside libraries Jarvis did not write,
which is what makes it stronger than patching a module. It then drives the
whole application: a full sync, prompt generation, response handling, every
API endpoint, the rendered dashboard and the probe. Anything that tried to
leave the machine would be recorded.

Loopback is allowed and separated out in the report: the dashboard is served
on 127.0.0.1, and serving a page to your own browser is not egress. Any
connection to a non-loopback address, and any DNS lookup for an external
name, fails the test.

Run:  python tests\\verify_no_egress.py
"""

import io
import ipaddress
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAILURES = []
EVENTS = []          # every audited network event, as (kind, target)
_RECORDING = False


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# ---------------------------------------------------------------------------
# The audit hook
# ---------------------------------------------------------------------------

WATCHED = {
    "socket.connect": "connect",
    "socket.getaddrinfo": "dns",
    "socket.gethostbyname": "dns",
    "urllib.Request": "url",
    "http.client.connect": "http",
    "ftplib.connect": "ftp",
    "smtplib.connect": "smtp",
    "subprocess.Popen": "subprocess",
    "os.system": "subprocess",
}


def _audit(event, args):
    if not _RECORDING:
        return
    kind = WATCHED.get(event)
    if kind is None:
        return
    try:
        target = args[1] if event == "socket.connect" and len(args) > 1 else args
        EVENTS.append((kind, event, repr(target)[:200]))
    except Exception:  # noqa: BLE001 - the hook must never raise
        EVENTS.append((kind, event, "<unrepresentable>"))


sys.addaudithook(_audit)


def is_local(text):
    """True if an audited target is loopback, a unix socket or a local file."""
    lowered = text.lower()
    if "127.0.0.1" in lowered or "::1" in lowered or "localhost" in lowered:
        return True
    # AF_UNIX sockets and abstract sockets are local by construction.
    if lowered.startswith("b'") or lowered.startswith("'/"):
        return True
    for token in ("'", '"', "(", ")", ","):
        lowered = lowered.replace(token, " ")
    for word in lowered.split():
        try:
            if ipaddress.ip_address(word).is_loopback:
                return True
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------------------
# Exercising the application
# ---------------------------------------------------------------------------

def drive_everything(workdir):
    """Run every part of Jarvis that could plausibly talk to anything."""
    import config
    config.OUTLOOK_BACKEND = "mock"
    config.DB_PATH = os.path.join(workdir, "jarvis.db")
    config.LOG_PATH = os.path.join(workdir, "sync.log")

    import db
    import sync
    import prompt_builder
    import response_renderer
    import dashboard
    import app as app_module

    db.init_db()
    result = sync.run_sync()
    view = dashboard.build_view()

    # A prompt for every flagged row, plus response parsing and rendering.
    flat = (list(view.get("awaiting_reply") or [])
            + list(view.get("overdue_inbound") or [])
            + list(view.get("meetings") or []))
    prompts = 0
    for item in flat:
        if isinstance(item, dict) and item.get("entry_id"):
            prompt_builder.build(item)
            prompts += 1

    parsed = response_renderer.parse(
        "SITUATION: A test.\nACTION: reply\nDRAFT REPLY: Hello.\n"
        "URGENCY: Low \u2014 no rush")
    if flat:
        response_renderer.render_text_card(
            response_renderer.build_card(flat[0], parsed))

    # A real sync loop is attached, so POST /sync exercises the code that
    # actually reaches for the mailbox rather than short-circuiting on 503.
    loop = sync.SyncLoop()
    flask_app = app_module.create_app(sync_loop=loop)
    client = flask_app.test_client()
    prefix = config.API_PREFIX
    paths = ["/", "/healthz", f"{prefix}/status", f"{prefix}/items",
             f"{prefix}/settings", f"{prefix}/sync-log",
             "/static/style.css", "/static/app.js"]
    responses = {}
    for path in paths:
        responses[path] = client.get(path).status_code
    # The write paths too: these are where an accidental outbound call
    # would most plausibly hide.
    if flat:
        target = flat[0]
        payload = {"conversation_id": target.get("conversation_id"),
                   "category": target.get("category"),
                   "entry_id": target.get("entry_id")}
        for path in (f"{prefix}/actions/snooze", f"{prefix}/actions/dismiss",
                     f"{prefix}/actions/restore"):
            responses[path + " (POST)"] = client.post(path, json=payload).status_code
        responses[f"{prefix}/prompt (GET)"] = client.get(
            f"{prefix}/prompt", query_string=payload).status_code
        responses[f"{prefix}/sync (POST)"] = client.post(f"{prefix}/sync").status_code
    # The PrivateGPT dashboard route: building the prompt and checking a page.
    responses[f"{prefix}/prompt/dashboard (GET)"] = client.get(
        f"{prefix}/prompt/dashboard").status_code
    responses[f"{prefix}/check-page (POST)"] = client.post(
        f"{prefix}/check-page",
        json={"source": "<html><body><img src='https://example.com/x.png'>"
                        "</body></html>"}).status_code

    # The probe, which is the other thing that reads the mailbox.
    import probe
    probe.run(full=True, redact=True, backend="mock",
              report_path=os.path.join(workdir, "probe.txt"))

    return result, len(flat), prompts, responses


def main():
    global _RECORDING

    workdir = tempfile.mkdtemp(prefix="jarvis_egress_")
    rule("Driving the whole application with the audit hook recording")
    print(f"  workdir: {workdir}")

    buffer = io.StringIO()
    saved = sys.stdout
    sys.stdout = buffer
    _RECORDING = True
    try:
        result, item_count, prompts, responses = drive_everything(workdir)
    finally:
        _RECORDING = False
        sys.stdout = saved

    print(f"  sync status      : {result.get('status')}")
    print(f"  items processed  : {item_count}")
    print(f"  prompts built    : {prompts}")
    print(f"  endpoints served : {len(responses)}")
    print(f"  audited events   : {len(EVENTS)}")

    rule("Every endpoint answered")
    for path, code in responses.items():
        check(path, code == 200, f"HTTP {code}")

    rule("Network events")
    connects = [e for e in EVENTS if e[0] in ("connect", "http", "ftp", "smtp")]
    lookups = [e for e in EVENTS if e[0] == "dns"]
    urls = [e for e in EVENTS if e[0] == "url"]
    spawns = [e for e in EVENTS if e[0] == "subprocess"]

    remote_connects = [e for e in connects if not is_local(e[2])]
    remote_lookups = [e for e in lookups if not is_local(e[2])]
    remote_urls = [e for e in urls if not is_local(e[2])]

    print(f"  connections : {len(connects)} "
          f"({len(connects) - len(remote_connects)} loopback/local)")
    print(f"  DNS lookups : {len(lookups)}")
    print(f"  URLs opened : {len(urls)}")
    print(f"  subprocesses: {len(spawns)}")

    check("no connection to any non-loopback address",
          not remote_connects, str(remote_connects[:3]))
    check("no DNS lookup for any external name",
          not remote_lookups, str(remote_lookups[:3]))
    check("no URL opened outside loopback",
          not remote_urls, str(remote_urls[:3]))
    check("no subprocess spawned", not spawns, str(spawns[:3]))

    rule("The hook itself works (a negative control)")
    # If the hook could not see a connection, every check above would pass
    # for the wrong reason. So: make one, and confirm it is detected.
    before = len(EVENTS)
    _RECORDING = True
    try:
        import socket
        probe_socket = socket.socket()
        probe_socket.settimeout(0.2)
        try:
            probe_socket.connect(("198.51.100.1", 9))  # TEST-NET-3, RFC 5737
        except OSError:
            pass
        finally:
            probe_socket.close()
    finally:
        _RECORDING = False
    detected = EVENTS[before:]
    check("a deliberate outbound connection IS detected", bool(detected),
          f"{len(detected)} event(s) recorded")
    check("and it is correctly judged non-local",
          any(not is_local(e[2]) for e in detected))

    rule("Jarvis's pages tell the browser to load nothing from outside")
    import app as app_module
    headers = app_module.create_app().test_client().get("/").headers
    policy = headers.get("Content-Security-Policy", "")
    check("the dashboard is served with a Content-Security-Policy",
          "default-src 'self'" in policy, policy[:60])
    script_src = policy.split("script-src")[1].split(";")[0]
    check("…which forbids inline and external scripts",
          "'self'" in script_src and "unsafe-inline" not in script_src
          and "http" not in script_src, script_src.strip())
    check("…and limits requests to Jarvis itself",
          "connect-src 'self'" in policy and "frame-ancestors 'none'" in policy)

    rule("No external references in what the browser is served")
    served = []
    for name in ("templates", "static"):
        folder = os.path.join(ROOT, name)
        for entry in sorted(os.listdir(folder)):
            text = io.open(os.path.join(folder, entry), encoding="utf-8").read()
            served.append((entry, text))
    pattern = re.compile(r"""(?:src|href|action|url)\s*[=(]\s*["']?\s*"""
                         r"""(https?:|//)""", re.IGNORECASE)
    offenders = [name for name, text in served if pattern.search(text)]
    check("no template or asset loads anything over the network",
          not offenders, str(offenders))
    # Match actual references, not the word: style.css says "no CDN" in a
    # comment, and a substring search would read that as a violation.
    cdn = re.compile(r"""(?:@import|url\(|src=|href=)[^\n)]{0,40}"""
                     r"""(?:https?:)?//""", re.IGNORECASE)
    cdn_offenders = [name for name, text in served if cdn.search(text)]
    check("no font, script or style is fetched from a CDN",
          not cdn_offenders, str(cdn_offenders))

    print(f"\n{'=' * 72}")
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED:")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("All egress checks passed. Jarvis made no connection to anything "
          "beyond this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
