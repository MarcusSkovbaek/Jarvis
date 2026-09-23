"""
page_check.py — checks a PrivateGPT-generated page before you open it.

The dashboard prompt asks PrivateGPT for a self-contained HTML page whose
first element is a Content-Security-Policy (prompt_builder.CSP_LINE). That
policy makes the browser refuse everything the page tries to *load*: fonts,
stylesheets, scripts, images, frames, fetch, XMLHttpRequest, WebSockets,
beacons and form submissions.

What a CSP cannot do is stop a page *navigating itself* somewhere else —
`location.href = "https://…?data=…"`, `window.open(…)`, a meta refresh — and
a navigation can carry data in its address. So this module reads the page's
source and refuses anything that could leave, before the page is ever run:

  BLOCKING   the policy line is missing or comes too late; a remote
             resource in an attribute or in CSS; a <base> element; a meta
             refresh; any script that navigates, opens a window, or clicks
             a link by itself.

  ADVISORY   network calls (fetch, XHR, WebSocket, ...), forms, frames.
             The policy blocks these, so they cannot leak, but the prompt
             said not to use them, so they are listed.

URLs in ordinary text are fine — an email excerpt that mentions a web
address is not a request — so only attribute, CSS and script contexts count.

The check is static and conservative: it may refuse a harmless page, and the
remedy is simply to ask PrivateGPT again. It never runs the page. It guards
against the mistakes a generated page can plausibly contain; it is not a
sandbox against an author deliberately hiding a web address in pieces.
"""

import re

import prompt_builder

VERDICT_SAFE = "safe"
VERDICT_UNSAFE = "unsafe"

_REMOTE = r"""(?:https?:|ftp:|wss?:|//)"""

# (name, pattern, blocking, explanation). Rules named script-* and
# network-call only look inside code (see _script_only), so prose such as
# "status: open (waiting on Lars)" cannot trip them.
_SCRIPT_RULES = {"script-navigation", "script-navigation-bare", "script-open",
                 "script-click", "network-call"}

# When several rules flag the same line, the finding shown is the most
# specific one: "script that changes the page's address" says more than
# "links to something outside the page" about `location.href = "https://…"`.
_PRIORITY = ["policy-missing", "policy-late", "script-navigation",
             "script-navigation-bare", "script-open", "script-click",
             "meta-refresh", "base-element", "remote-css", "css-import",
             "remote-attribute"]

_RULES = [
    ("remote-attribute",
     re.compile(r"""\b(src|href|action|formaction|data|poster|srcset|background|ping|xlink:href)\s*=\s*["']?\s*"""
                + _REMOTE, re.IGNORECASE),
     True, "loads or links to something outside the page"),
    ("remote-css",
     re.compile(r"""url\(\s*["']?\s*""" + _REMOTE, re.IGNORECASE),
     True, "CSS that fetches from outside the page"),
    ("css-import",
     re.compile(r"""@import\b""", re.IGNORECASE),
     True, "CSS @import"),
    ("base-element",
     re.compile(r"""<base\b""", re.IGNORECASE),
     True, "a <base> element, which redirects every link on the page"),
    ("meta-refresh",
     re.compile(r"""http-equiv\s*=\s*["']?\s*refresh""", re.IGNORECASE),
     True, "a meta refresh, which navigates the page automatically"),
    # Only writes and calls count: reading location.hash to drive a filter
    # is harmless, and changing the hash stays on the same page.
    ("script-navigation",
     re.compile(r"""\b(?:window|document|top|parent|self|globalThis)\s*\.\s*location\s*"""
                r"""(?:=[^=]|\.\s*(?:href|search|pathname|host|hostname|protocol|port|origin)\s*=[^=]"""
                r"""|\.\s*(?:assign|replace|reload)\s*\()"""
                r"""|(?<![\w.$])location\s*\.\s*(?:href|search|pathname|host|hostname|protocol|port)\s*=[^=]"""
                r"""|(?<![\w.$])location\s*\.\s*(?:assign|replace)\s*\("""),
     True, "script that changes the page's address"),
    # A bare `location = ...` navigates, but `const location = item.location`
    # declares a variable, so declarations are excluded below.
    ("script-navigation-bare",
     re.compile(r"""(?<![\w.$])location\s*=[^=]"""),
     True, "script that changes the page's address"),
    # window.open(...) or a bare open(...); not xhr.open() or details.open.
    ("script-open",
     re.compile(r"""\bwindow\s*\.\s*open\s*\(|(?<![\w.$])open\s*\("""),
     True, "script that opens a new window"),
    # A script can navigate without touching `location`: make a link, point
    # it somewhere, click it. A generated page has no reason to click things
    # by itself (its buttons respond to the user), so any .click() is refused.
    ("script-click",
     re.compile(r"""\.\s*click\s*\(\s*\)"""),
     True, "script that clicks a link or button by itself"),
    ("network-call",
     re.compile(r"""\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\b|\bEventSource\b"""
                r"""|\bsendBeacon\b|\bimport\s*\(|\bimportScripts\b|\bRTCPeerConnection\b"""),
     False, "a network call (blocked by the policy line)"),
    ("form",
     re.compile(r"""<form\b""", re.IGNORECASE),
     False, "a form (submission is blocked by the policy line)"),
    ("embedded-frame",
     re.compile(r"""<(?:iframe|frame|object|embed)\b""", re.IGNORECASE),
     False, "an embedded frame or object (blocked by the policy line)"),
]

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_EVENT_ATTR = re.compile(r"""\son[a-z]+\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)
_JS_URL = re.compile(r"""javascript:([^"'>]*)""", re.IGNORECASE)
_DECLARATION = re.compile(r"(?:\bvar|\blet|\bconst)\s+$")

_FIRST_ACTIVE = re.compile(r"""<(?:script|style|link|meta\s+http-equiv\s*=\s*["']?\s*refresh)\b""",
                           re.IGNORECASE)


def _strip_code_fence(text):
    """Accept the answer exactly as copied, ```html fences and all."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped


def _script_only(text):
    """The source with everything that is not code blanked out.

    Code is the body of <script> elements, event-handler attributes
    (onclick="...") and javascript: URLs. Blanking rather than extracting
    keeps every character at its original position, so line numbers in the
    findings still point at the right place.
    """
    keep = [False] * len(text)
    for pattern in (_SCRIPT_BLOCK, _EVENT_ATTR, _JS_URL):
        for match in pattern.finditer(text):
            for group in range(1, (match.lastindex or 0) + 1):
                if match.group(group) is not None:
                    for i in range(match.start(group), match.end(group)):
                        keep[i] = True
    return "".join(c if keep[i] or c == "\n" else " "
                   for i, c in enumerate(text))


def _line_of(text, index):
    return text.count("\n", 0, index) + 1


def check_page(source):
    """Check a page's source. Returns a dict describing the verdict.

    {
      "verdict": "safe" | "unsafe",
      "blocking": [ {rule, line, excerpt, why}, ... ],
      "advisory": [ ... ],
      "csp": "ok" | "missing" | "late",
      "size": <characters>,
    }
    """
    text = _strip_code_fence(source or "")
    result = {"blocking": [], "advisory": [], "size": len(text)}

    # The policy only counts where a browser would honour it: not inside an
    # HTML comment, and in <head>, ahead of anything that loads or runs.
    # Comments are blanked (not removed) so positions still line up.
    live = _COMMENT.sub(lambda m: " " * len(m.group(0)), text)
    csp_at = live.find(prompt_builder.CSP_LINE)
    first_active = _FIRST_ACTIVE.search(live)
    body = re.search(r"<body\b", live, re.IGNORECASE)
    if body and 0 <= body.start() < csp_at:
        first_active = body
    if csp_at < 0:
        result["csp"] = "missing"
        result["blocking"].append({
            "rule": "policy-missing", "line": None, "excerpt": "",
            "why": "the Content-Security-Policy line is missing or altered, "
                   "so nothing stops the page loading from the network"})
    elif first_active and first_active.start() < csp_at:
        result["csp"] = "late"
        result["blocking"].append({
            "rule": "policy-late", "line": _line_of(text, first_active.start()),
            "excerpt": text[first_active.start():first_active.start() + 60],
            "why": "the policy line comes after a script, style, link or "
                   "the page body, where the browser does not apply it"})
    else:
        result["csp"] = "ok"

    if not re.search(r"<html\b", text, re.IGNORECASE):
        result["advisory"].append({
            "rule": "not-html", "line": None, "excerpt": text[:60],
            "why": "this does not look like an HTML page"})

    code = _script_only(text)
    for name, pattern, blocking, why in _RULES:
        for match in pattern.finditer(code if name in _SCRIPT_RULES else text):
            start = match.start()
            line_start = text.rfind("\n", 0, start) + 1
            line_end = text.find("\n", start)
            line_end = len(text) if line_end < 0 else line_end
            excerpt = text[line_start:line_end].strip()
            if (name == "script-navigation-bare"
                    and _DECLARATION.search(text[max(0, start - 12):start])):
                continue
            finding = {"rule": name, "line": _line_of(text, start),
                       "excerpt": excerpt[:140], "why": why}
            (result["blocking"] if blocking else result["advisory"]).append(finding)

    result["blocking"] = _one_per_line(result["blocking"])
    result["verdict"] = VERDICT_UNSAFE if result["blocking"] else VERDICT_SAFE
    return result


def _one_per_line(findings):
    """Keep the most specific finding for each line, in line order."""
    rank = {name: i for i, name in enumerate(_PRIORITY)}
    best = {}
    for finding in findings:
        key = finding["line"] if finding["line"] is not None else f"-{finding['rule']}"
        current = best.get(key)
        if current is None or rank.get(finding["rule"], 99) < rank.get(current["rule"], 99):
            best[key] = finding
    return sorted(best.values(),
                  key=lambda f: (f["line"] is not None, f["line"] or 0))
