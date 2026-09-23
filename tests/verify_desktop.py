"""
verify_desktop.py — the desktop window, and what happens without one.

  A. The rules, with no window needed: which addresses the window may show,
     how the Windows network report is read and masked, which renderer is
     requested, the WebView2 privacy switches, and the command-line options.

  B. A real window. Jarvis is started the way a user starts it, the window
     opens on the local dashboard, and from inside it the test checks what is
     on screen, tries to navigate the window away (it must come back), closes
     it, and confirms Jarvis stopped and let go of its port. Then the full
     --check-desktop run is made and its report read.

     On Windows this uses Edge WebView2, the real thing. On the Linux build
     machine it uses pywebview's Qt renderer — also Chromium — on a virtual
     display, so the window logic is exercised even where WebView2 cannot
     exist. With no renderer available at all, this part is SKIPPED.

  C. No window possible. With pywebview missing, Jarvis must say why in
     sync.log, open the default browser on the dashboard instead, and keep
     serving.

Run:  python tests\\verify_desktop.py
"""

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAILURES = []
SKIPPED = []

APP_FILES = [n for n in os.listdir(ROOT) if n.endswith(".py")] + \
    ["templates", "static"]


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_is_free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def app_copy():
    """A throwaway copy of the app, so runs write their data there."""
    folder = tempfile.mkdtemp(prefix="jarvis_desktop_test_")
    for name in APP_FILES:
        source = os.path.join(ROOT, name)
        target = os.path.join(folder, name)
        if os.path.isdir(source):
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    return folder


def window_env():
    """Environment for opening a window on this machine, or None."""
    env = dict(os.environ)
    if sys.platform == "win32":
        return env
    try:
        import webview  # noqa: F401
        from PyQt6 import QtWebEngineWidgets  # noqa: F401
    except Exception:  # noqa: BLE001
        return None
    env.update({"JARVIS_WEBVIEW_GUI": "qt",
                "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox",
                "QT_QPA_PLATFORM": "xcb"})
    return env


def window_command(args):
    """Prefix with xvfb-run when there is no display."""
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        if not shutil.which("xvfb-run"):
            return None
        return ["xvfb-run", "-a", "-s", "-screen 0 1600x1000x24"] + args
    return args


# ---------------------------------------------------------------------------
# A
# ---------------------------------------------------------------------------

def part_a():
    import desktop
    import desktop_check as dc
    import main

    rule("A1. The window only ever shows the local dashboard")
    for url, expected in [("http://127.0.0.1:5000/", True),
                          ("http://127.0.0.1:5000/#email", True),
                          ("http://localhost:5000/", True),
                          ("about:blank", True),
                          ("https://127.0.0.1:5000/", False),
                          ("http://example.com/", False),
                          ("http://127.0.0.1.example.com/", False),
                          ("file:///C:/Users/x/page.html", False),
                          ("", False)]:
        check(f"{url or '(empty)'} -> {'allowed' if expected else 'sent back'}",
              desktop.is_local_url(url) == expected)

    rule("A2. The renderer asked for, and its privacy switches")
    saved = sys.platform
    try:
        sys.platform = "win32"
        check("Windows asks for Edge WebView2 explicitly, never the IE engine",
              desktop._gui_backend() == "edgechromium")
    finally:
        sys.platform = saved
    for switch in ("--disable-background-networking",
                   "--disable-component-update", "--disable-domain-reliability",
                   "--disable-sync", "--no-pings", "msSmartScreenProtection"):
        check(f"WebView2 starts with {switch}",
              switch in desktop.WEBVIEW2_ARGUMENTS)

    rule("A3. Without pywebview, the reason is plain")
    real = sys.modules.get("webview")
    sys.modules["webview"] = None          # makes `import webview` fail
    try:
        ok, reason = desktop.window_available()
    finally:
        if real is None:
            sys.modules.pop("webview", None)
        else:
            sys.modules["webview"] = real
    check("reported unavailable", ok is False)
    check("…with what to do about it", reason and "pip install pywebview" in reason,
          reason)

    rule("A4. Reading the Windows network report")
    netstat = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:5000         0.0.0.0:0              LISTENING       4100
  TCP    127.0.0.1:5000         127.0.0.1:51512        ESTABLISHED     4100
  TCP    192.168.1.23:51600     13.107.42.14:443       ESTABLISHED     4230
  TCP    192.168.1.23:51601     10.20.30.40:8080       SYN_SENT        4231
  TCP    [::1]:51620            [::1]:5000             ESTABLISHED     4230
  UDP    0.0.0.0:5353           *:*                                    4230
  TCP    192.168.1.23:51700     52.1.2.3:443           ESTABLISHED     9999
"""
    rows = dc.parse_netstat(netstat)
    check("all rows parsed, header ignored", len(rows) == 7, str(len(rows)))
    check("IPv6 addresses unwrapped", ("TCP", "::1", "5000", "ESTABLISHED", 4230) in rows)
    tree = dc.process_tree(4100, [(4100, 1, "Jarvis.exe"),
                                  (4230, 4100, "msedgewebview2.exe"),
                                  (4231, 4230, "msedgewebview2.exe"),
                                  (9999, 1, "OneDrive.exe")])
    check("the tree follows children and grandchildren",
          tree == {4100, 4230, 4231}, str(sorted(tree)))
    ours = [r for r in rows if r[4] in tree]
    check("other programs' connections are left out",
          all(r[4] != 9999 for r in ours) and len(ours) == 6)
    described = {r[1]: dc.describe_remote(r[1]) for r in ours}
    check("loopback is named", described["127.0.0.1"].startswith("loopback"))
    check("a private address is masked",
          described["10.20.30.40"] == "<private network address>")
    check("a public address is shown, so it can be traced",
          described["13.107.42.14"] == "13.107.42.14")
    check("an unconnected UDP socket is not counted as outside",
          described["*"].startswith("none"))
    check("listening sockets are not counted as outside",
          described["0.0.0.0"].startswith("none"))

    rule("A5. Command line")
    args = main.parse_args([])
    check("no option: the configured mode (a window)", args.ui is None
          and __import__("config").UI_MODE == "window")
    check("--browser", main.parse_args(["--browser"]).ui == "browser")
    check("--window", main.parse_args(["--window"]).ui == "window")
    try:
        main.parse_args(["--window", "--browser"])
        exclusive = False
    except SystemExit:
        exclusive = True
    check("--window and --browser cannot be combined", exclusive)
    check("--check-desktop is available",
          main.parse_args(["--check-desktop"]).check_desktop)


# ---------------------------------------------------------------------------
# B
# ---------------------------------------------------------------------------

DRIVER = r"""
import json, os, sys, threading, time
sys.path.insert(0, os.getcwd())
import desktop, main

port = int(sys.argv[1])
results = {}
real_open = desktop.open_window

def probe(window):
    try:
        time.sleep(1.5)
        results["url"] = window.get_current_url()
        results["page"] = json.loads(window.evaluate_js(
            "JSON.stringify({greeting: document.querySelector('.hero__greeting').textContent.trim(),"
            " cards: document.querySelectorAll('.card').length,"
            " bridge: !!(window.pywebview && window.pywebview.api && window.pywebview.api.copy_text),"
            " title: document.title})"))
        # A real call through the bridge, waiting for its answer to come
        # back into the page (True on Windows, False elsewhere).
        window.evaluate_js("window.__bridge = 'pending';"
                           " window.pywebview.api.copy_text('jarvis-test')"
                           ".then(function (v) { window.__bridge = String(v); },"
                           " function (e) { window.__bridge = 'error ' + e; });")
        for _ in range(30):
            answer = window.evaluate_js("window.__bridge")
            if answer != "pending":
                break
            time.sleep(0.1)
        results["bridge_answer"] = answer
        outside = os.path.join(os.getcwd(), "outside.html")
        open(outside, "w").write("<html><body>not Jarvis</body></html>")
        window.load_url(__import__("pathlib").Path(outside).as_uri())
        time.sleep(3)
        results["after_navigation"] = window.get_current_url()
    except Exception as exc:
        results["error"] = f"{exc.__class__.__name__}: {exc}"
    finally:
        window.destroy()

def patched(url, on_closed=None, self_test=None):
    return real_open(url, on_closed=on_closed, self_test=probe)

desktop.open_window = patched
code = main.main(["--backend", "mock", "--port", str(port)])
results["exit"] = code
print("RESULTS " + json.dumps(results))
"""


def part_b():
    rule("B. A real desktop window")
    env = window_env()
    if env is None:
        print("  SKIPPED: no window renderer on this machine "
              "(pywebview with WebView2 on Windows, or Qt WebEngine elsewhere).")
        SKIPPED.append("real window")
        return

    folder = app_copy()
    io.open(os.path.join(folder, "driver.py"), "w").write(DRIVER)
    port = free_port()
    command = window_command([sys.executable, "driver.py", str(port)])
    if command is None:
        print("  SKIPPED: no display and no xvfb-run.")
        SKIPPED.append("real window")
        return

    started = time.time()
    run = subprocess.run(command, cwd=folder, env=env, capture_output=True,
                         text=True, timeout=120)
    elapsed = time.time() - started
    line = next((l for l in run.stdout.splitlines() if l.startswith("RESULTS ")),
                None)
    results = json.loads(line[8:]) if line else {}
    check("Jarvis started, showed a window and exited when it closed",
          run.returncode == 0 and results.get("exit") == 0,
          f"exit={run.returncode} in {elapsed:.1f}s"
          + (f" stderr: {run.stderr.strip()[-300:]}" if run.returncode else ""))
    check("the window opened on the local dashboard",
          (results.get("url") or "").startswith(f"http://127.0.0.1:{port}/"),
          results.get("url"))
    page = results.get("page") or {}
    check("the dashboard rendered inside it",
          page.get("cards") == 5 and page.get("greeting", "").startswith("Good"),
          str(page))
    check("the clipboard bridge is exposed to the page", page.get("bridge") is True)
    expected = "true" if sys.platform == "win32" else "false"
    check("a bridge call gets its answer back into the page",
          results.get("bridge_answer") == expected,
          f"answer={results.get('bridge_answer')!r}")
    check("navigating the window away sends it straight back",
          (results.get("after_navigation") or "").startswith(
              f"http://127.0.0.1:{port}/"),
          results.get("after_navigation"))
    check("no error inside the window", "error" not in results,
          results.get("error"))
    check("closing the window stopped the server and freed the port",
          port_is_free(port))
    log_text = io.open(os.path.join(folder, "sync.log"), encoding="utf-8").read()
    check("sync.log records the window opening and closing",
          "Opening desktop window" in log_text
          and "Window closed - Jarvis stopped" in log_text)
    check("…and the blocked navigation",
          "Blocked navigation away from Jarvis" in log_text)

    rule("B2. --check-desktop, as it will be run on the work PC")
    folder2 = app_copy()
    command = window_command([sys.executable, "main.py", "--check-desktop"])
    run = subprocess.run(command, cwd=folder2, env=env, capture_output=True,
                         text=True, timeout=120)
    report_path = os.path.join(folder2, "desktop-check.txt")
    report = io.open(report_path, encoding="utf-8").read() \
        if os.path.exists(report_path) else ""
    check("it exits 0", run.returncode == 0, f"exit={run.returncode}")
    check("it writes desktop-check.txt next to the app", bool(report))
    check("the report ends in a pass", "All desktop checks passed." in report)
    check("it did not touch the real database or log",
          not os.path.exists(os.path.join(folder2, "jarvis.db"))
          and not os.path.exists(os.path.join(folder2, "sync.log")))
    check("the report carries no mail data",
          "cedra.dk" not in report and "@" not in report.replace(
              "user or machine names", ""))


# ---------------------------------------------------------------------------
# C
# ---------------------------------------------------------------------------

def part_c():
    rule("C. No window possible: fall back to the browser")
    folder = app_copy()
    # A stand-in pywebview that cannot be imported.
    stub = os.path.join(folder, "stub", "webview")
    os.makedirs(stub)
    io.open(os.path.join(stub, "__init__.py"), "w").write(
        "raise ImportError('pywebview removed for this test')\n")
    opened = os.path.join(folder, "browser-opened.txt")
    recorder = os.path.join(folder, "record_browser.py")
    io.open(recorder, "w").write(
        "import sys\nopen(%r, 'w').write(sys.argv[1])\n" % opened)

    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(folder, "stub")
    env["BROWSER"] = f'"{sys.executable}" "{recorder}" %s'
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "main.py", "--backend", "mock", "--port", str(port)],
        cwd=folder, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        served = False
        for _ in range(60):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                    served = r.status == 200
                    break
            except OSError:
                time.sleep(0.25)
        for _ in range(20):
            if os.path.exists(opened):
                break
            time.sleep(0.25)
        check("Jarvis still serves the dashboard", served)
        url = io.open(opened).read().strip() if os.path.exists(opened) else None
        check("the default browser was opened on it",
              url == f"http://127.0.0.1:{port}/", str(url))
        check("the process keeps running", process.poll() is None)
        log_text = io.open(os.path.join(folder, "sync.log"),
                           encoding="utf-8").read()
        check("sync.log says why, in words",
              "Opening the dashboard in the browser instead. Reason: "
              "pywebview is not installed" in log_text)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def main():
    part_a()
    part_b()
    part_c()
    print(f"\n{'=' * 72}")
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED:")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    if SKIPPED:
        print("All checks that could run passed. SKIPPED: " + ", ".join(SKIPPED))
        return 0
    print("All desktop checks passed, including in a real window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
