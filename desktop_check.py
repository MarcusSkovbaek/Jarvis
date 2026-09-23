"""
desktop_check.py — WORK PC. Checks the desktop window and writes a report.

The desktop window is the one part of Jarvis that cannot be tested on the
machine it is built on: it renders with Edge WebView2, which exists only on
Windows. So this check runs on your PC and produces a report you can pass
back:

    Jarvis.exe --check-desktop

What it does:
  1. starts Jarvis on MOCK data, in a temporary folder, on a free port — your
     mailbox is never opened and your real jarvis.db and sync.log are not
     touched;
  2. opens the real desktop window and runs a self-test inside it: which
     engine rendered it, whether the layout fits, whether the views switch,
     whether the clipboard bridge works, whether the dashboard prompt builds;
  3. while the window is open, lists every network connection held by Jarvis
     and the window's own processes, so you can see for yourself that nothing
     but the local dashboard is being talked to;
  4. closes the window and writes desktop-check.txt next to the app.

The report contains no mail data (it only ever sees the invented mock
mailbox), no local IP addresses, no user or machine names. Remote addresses
on private networks are masked; public ones are shown, because if Windows
itself contacts Microsoft while the window is open, that is exactly what you
should be able to see.

Your clipboard is used once, for the clipboard test, and restored afterwards.
"""

import io
import ipaddress
import json
import logging
import os
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime

import config

log = logging.getLogger("jarvis.desktop_check")

REPORT_NAME = "desktop-check.txt"

# How long the window is watched for network activity before closing.
OBSERVE_SECONDS = 8

_lines = []


def emit(line=""):
    print(line)
    _lines.append(str(line))


def rule(title):
    emit(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _report_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Inside the window
# ---------------------------------------------------------------------------

# Runs in the page. Synchronous on purpose: every renderer pywebview supports
# can return a plain value from evaluate_js, not all of them a Promise.
PAGE_PROBE = r"""
(function () {
  var r = {};
  r.userAgent = navigator.userAgent;
  r.viewport = [window.innerWidth, window.innerHeight];
  r.devicePixelRatio = window.devicePixelRatio;
  r.bridge = !!(window.pywebview && window.pywebview.api &&
                window.pywebview.api.copy_text);
  r.cssGrid = !!(window.CSS && CSS.supports("display", "grid"));
  r.cssVars = !!(window.CSS && CSS.supports("color", "var(--x)"));
  r.segoe = !!(document.fonts && document.fonts.check('14px "Segoe UI"'));
  r.bodyFont = getComputedStyle(document.body).fontFamily;
  r.cards = document.querySelectorAll(".card").length;
  r.railItems = document.querySelectorAll(".rail__item").length;
  r.greeting = (document.querySelector(".hero__greeting") || {}).textContent;
  r.greeting = r.greeting ? r.greeting.trim() : null;

  // Anything wider than the box it sits in is a layout bug.
  var overflow = [];
  var boxes = document.querySelectorAll(".card, .mini, .list__row, .focus__item, .event");
  Array.prototype.forEach.call(boxes, function (el) {
    var parent = el.parentElement.getBoundingClientRect();
    var own = el.getBoundingClientRect();
    if (own.right > parent.right + 1.5 && el.offsetParent !== null) {
      overflow.push(el.className.split(" ")[0]);
    }
  });
  r.overflowing = overflow;

  // View switching through the rail, then back home.
  var views = {};
  ["email", "calendar", "assistant", "settings", "home"].forEach(function (name) {
    var link = document.querySelector('[data-view-link="' + name + '"]');
    if (link) { link.click(); }
    var el = document.querySelector('.view[data-view="' + name + '"]');
    views[name] = !!(el && !el.hidden);
  });
  r.views = views;
  return JSON.stringify(r);
})();
"""


def _clipboard_roundtrip(window):
    """Write through the page's bridge, read back through Windows, restore."""
    if sys.platform != "win32":
        return "not tested (not Windows)"
    import win32clipboard
    import win32con

    def read():
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            win32clipboard.CloseClipboard()

    previous = None
    try:
        previous = read()
    except Exception:  # noqa: BLE001 - clipboard held by another app
        pass
    marker = f"jarvis-clipboard-check-{os.getpid()}"
    try:
        window.evaluate_js(
            f"window.pywebview.api.copy_text({json.dumps(marker)})")
        time.sleep(0.8)
        ok = read() == marker
    except Exception as exc:  # noqa: BLE001
        return f"FAILED ({exc.__class__.__name__}: {exc})"
    finally:
        if previous is not None:
            try:
                import desktop
                desktop._set_clipboard(previous)
            except Exception:  # noqa: BLE001
                pass
    return "works" if ok else "FAILED (text did not arrive on the clipboard)"


# ---------------------------------------------------------------------------
# Network observation (Windows)
# ---------------------------------------------------------------------------

def describe_remote(address):
    """Show public addresses, mask private ones, name loopback."""
    if address in ("*", ""):
        return "none (not connected)"        # UDP rows show "*:*"
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return "<unparseable>"
    if ip.is_loopback:
        return "loopback (this computer)"
    if ip.is_unspecified:
        return "none (listening)"
    if ip.is_private or ip.is_link_local:
        return "<private network address>"
    return str(ip)


def process_tree(root_pid, processes):
    """PIDs of root_pid and all its descendants. processes: [(pid, ppid, name)]."""
    children = {}
    for pid, ppid, _ in processes:
        children.setdefault(ppid, []).append(pid)
    tree, stack = set(), [root_pid]
    while stack:
        pid = stack.pop()
        if pid in tree:
            continue
        tree.add(pid)
        stack.extend(children.get(pid, []))
    return tree


def parse_netstat(text):
    """[(proto, remote_address, remote_port, state, pid)] from `netstat -ano`."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in ("TCP", "UDP"):
            continue
        proto = parts[0]
        if proto == "TCP" and len(parts) >= 5:
            remote, state, pid = parts[2], parts[3], parts[4]
        elif proto == "UDP" and len(parts) >= 4:
            remote, state, pid = parts[2], "", parts[3]
        else:
            continue
        host, _, port = remote.rpartition(":")
        host = host.strip("[]")
        try:
            rows.append((proto, host, port, state, int(pid)))
        except ValueError:
            continue
    return rows


def _run(command):
    return subprocess.run(command, capture_output=True, text=True, timeout=30,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                          ).stdout


def _list_processes():
    """[(pid, ppid, name)] via PowerShell/CIM, with WMIC as a fallback."""
    try:
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-CimInstance Win32_Process | Select-Object "
                    "ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress"])
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        return [(int(p["ProcessId"]), int(p["ParentProcessId"]), p["Name"])
                for p in data], "PowerShell CIM"
    except Exception:  # noqa: BLE001 - PowerShell blocked or absent
        pass
    out = _run(["wmic", "process", "get", "ProcessId,ParentProcessId,Name",
                "/format:csv"])
    rows = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4 and parts[-1].isdigit() and parts[-2].isdigit():
            rows.append((int(parts[-1]), int(parts[-2]), parts[1]))
    return rows, "WMIC"


def observe_network(root_pid):
    """Every connection held by Jarvis and its child processes."""
    if sys.platform != "win32":
        return None, "not collected (not Windows)"
    processes, method = _list_processes()
    tree = process_tree(root_pid, processes)
    names = {pid: name for pid, _, name in processes}
    rows = [r for r in parse_netstat(_run(["netstat", "-ano"])) if r[4] in tree]
    return ([(names.get(pid, "?"), proto, host, port, state)
             for proto, host, port, state, pid in rows],
            f"{method} + netstat, {len(tree)} processes in Jarvis's tree")


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def run():
    """Run the desktop check. Returns a process exit code."""
    import app as app_module
    import db
    import desktop
    import sync
    from werkzeug.serving import make_server

    workdir = tempfile.mkdtemp(prefix="jarvis_desktop_check_")
    config.OUTLOOK_BACKEND = "mock"
    config.DB_PATH = os.path.join(workdir, "jarvis.db")
    config.LOG_PATH = os.path.join(workdir, "sync.log")
    sync.configure_logging(level=logging.INFO, console=False)

    emit(f"Jarvis desktop check - {datetime.now():%Y-%m-%d %H:%M:%S}")
    emit("Mock data only. Your mailbox was not opened; your jarvis.db and "
         "sync.log were not touched.")

    rule("1. Environment")
    emit(f"  Operating system : {platform.platform()}")
    emit(f"  Frozen .exe      : {bool(getattr(sys, 'frozen', False))}")
    emit(f"  Python           : {platform.python_version()}")
    try:
        import importlib.metadata as md
        emit(f"  pywebview        : {md.version('pywebview')}")
    except Exception:  # noqa: BLE001
        emit("  pywebview        : (version unknown)")
    emit(f"  WebView2 runtime : {desktop.webview2_runtime_version() or 'not found'}")
    available, reason = desktop.window_available()
    emit(f"  Window possible  : {'yes' if available else 'NO - ' + reason}")

    db.init_db()
    result = sync.run_sync()
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    server = make_server("127.0.0.1", port,
                         app_module.create_app(sync_loop=None), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    emit(f"  Mock sync        : {result['status']}, counts {result.get('counts')}")

    findings = {}

    def self_test(window):
        try:
            time.sleep(1.5)                   # let the page settle
            raw = window.evaluate_js(PAGE_PROBE)
            findings["page"] = json.loads(raw) if isinstance(raw, str) else raw
            findings["clipboard"] = _clipboard_roundtrip(window)
            with urllib.request.urlopen(url + config.API_PREFIX.lstrip("/")
                                        + "/prompt/dashboard",
                                        timeout=10) as response:
                prompt = json.loads(response.read().decode("utf-8"))
            findings["prompt"] = prompt
            time.sleep(OBSERVE_SECONDS)       # let background traffic show
            findings["network"] = observe_network(os.getpid())
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            findings["error"] = f"{exc.__class__.__name__}: {exc}"
        finally:
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass

    started = time.time()
    shown = desktop.open_window(url, self_test=self_test)
    server.shutdown()

    rule("2. The window")
    failures = []
    if not shown:
        emit(f"  NOT OPENED: {desktop.last_fallback_reason}")
        emit("  Jarvis will open in your browser instead. Send this report "
             "back and I will work out why.")
        failures.append("window did not open")
    else:
        emit(f"  Opened, self-tested and closed in {time.time() - started:.1f}s")
    page = findings.get("page") or {}
    if page:
        emit(f"  Renderer         : {page.get('userAgent')}")
        emit(f"  Window size      : {page.get('viewport')} "
             f"at {page.get('devicePixelRatio')}x scaling")
        emit(f"  Segoe UI font    : {'available' if page.get('segoe') else 'not found'}")
        emit(f"  CSS grid / vars  : {page.get('cssGrid')} / {page.get('cssVars')}")
        emit(f"  Greeting shown   : {page.get('greeting')!r}")
        emit(f"  Cards on home    : {page.get('cards')} (expected 5)")
        emit(f"  Rail buttons     : {page.get('railItems')} (expected 5)")
        views = page.get("views") or {}
        emit(f"  Views switch     : "
             + ", ".join(f"{k} {'ok' if v else 'FAILED'}" for k, v in views.items()))
        over = page.get("overflowing") or []
        emit(f"  Layout overflow  : {'none' if not over else ', '.join(sorted(set(over)))}")
        emit(f"  Clipboard bridge : {'present' if page.get('bridge') else 'MISSING'}"
             f" - {findings.get('clipboard')}")
        if page.get("cards") != 5:
            failures.append("wrong number of cards")
        if page.get("railItems") != 5:
            failures.append("wrong number of rail buttons")
        if not all(views.values()):
            failures.append("a view did not switch")
        if over:
            failures.append("layout overflow")
        if not page.get("bridge"):
            failures.append("clipboard bridge missing")
        if sys.platform == "win32" and findings.get("clipboard") != "works":
            failures.append("clipboard")
    elif shown:
        failures.append("self-test did not run")
    if findings.get("error"):
        emit(f"  Self-test error  : {findings['error']}")
        failures.append("self-test error")

    prompt = findings.get("prompt") or {}
    if prompt:
        import prompt_builder
        emit(f"  Dashboard prompt : v{prompt.get('prompt_version')}, "
             f"{prompt.get('words')} words, CSP line "
             f"{'present' if prompt_builder.CSP_LINE in prompt.get('prompt', '') else 'MISSING'}")

    rule("3. Network connections while the window was open")
    connections, method = findings.get("network") or (None, "not collected")
    emit(f"  Method: {method}")
    outside = []
    if connections is not None:
        if not connections:
            emit("  (none)")
        for name, proto, host, port_, state in sorted(set(connections)):
            where = describe_remote(host)
            emit(f"  {name:<28} {proto:<4} {where:<28} port {port_:<6} {state}")
            if not where.startswith(("loopback", "none")):
                outside.append((name, where, port_))
        emit()
        if outside:
            emit(f"  {len(outside)} connection(s) left this computer. Jarvis "
                 "itself only ever talks to 127.0.0.1, so these come from the "
                 "WebView2 runtime or Windows. Send this report back and I "
                 "will switch off whatever is responsible.")
        else:
            emit("  Nothing left this computer. Every connection was to "
                 "Jarvis's own local server.")

    rule("Result")
    if failures:
        emit("  ISSUES FOUND: " + "; ".join(failures))
    else:
        emit("  All desktop checks passed.")
    emit("\nThis report contains no mail data, no local IP addresses and no "
         "user or machine names. It was written to a file on this computer "
         "and sent nowhere.")

    path = os.path.join(_report_dir(), REPORT_NAME)
    try:
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(_lines) + "\n")
        print(f"\nReport written to: {path}")
    except OSError as exc:
        print(f"\n(could not write {path}: {exc})")
    return 1 if failures else 0
