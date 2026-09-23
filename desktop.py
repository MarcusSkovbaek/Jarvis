"""
desktop.py — Jarvis in its own desktop window.

The dashboard is the same local page either way; this module only decides
what shows it. In window mode it opens a native window through pywebview,
which on Windows renders with Edge WebView2 — no browser tabs, no address
bar, a taskbar entry of its own. In browser mode, or if a window cannot be
created, it opens the default browser as earlier versions did.

Nothing here changes where data goes. The window is pointed at
http://127.0.0.1 and nothing else, and WebView2 is started with its
background services (SmartScreen lookups, component updates, background
networking) switched off, so the window adds no traffic of its own.

Jarvis's own page contains nothing that can navigate away: every value in it
is escaped, and it carries a Content-Security-Policy (see app.py). As a
backstop, if the window ever does end up somewhere other than the local
server, it is sent straight back. That reverts a navigation; it cannot
un-send one, which is why the first two measures are the ones relied on.

Threading: pywebview must own the main thread, so the Flask server runs on a
daemon thread and the window runs on the main one. Closing the window ends
the process.
"""

import logging
import os
import sys
import threading
import webbrowser

import config

log = logging.getLogger("jarvis.desktop")

# Chromium switches passed to Edge WebView2. WebView2 reads them from this
# environment variable when it starts, so they must be set before the first
# window is created.
#
#   --disable-background-networking   no speculative or background requests
#   --disable-component-update        no component downloads
#   --disable-domain-reliability      no network error reporting to Microsoft
#   --disable-sync                    no profile sync
#   --no-pings                        no hyperlink auditing pings
#   --disable-features=msSmartScreenProtection
#                                     no SmartScreen URL reputation lookups
#                                     (the page is local; there is nothing
#                                     to look up, only something to leak)
WEBVIEW2_ARGUMENTS = " ".join([
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-sync",
    "--no-pings",
    "--disable-features=msSmartScreenProtection",
])

# Why the last attempt to open a window did not happen. Shown in sync.log and
# in --version output, so "why did it open in my browser?" has an answer.
last_fallback_reason = None


class Bridge:
    """Methods the page can call as window.pywebview.api.<name>(...).

    Deliberately tiny: the page already talks to Jarvis over its local API.
    The bridge exists only for what a web page is not allowed to do reliably
    on its own, which is writing to the clipboard.
    """

    def copy_text(self, text):
        """Put text on the Windows clipboard. Returns True on success."""
        try:
            return _set_clipboard(str(text))
        except Exception as exc:  # noqa: BLE001 - the page falls back itself
            log.warning("Clipboard write through the window bridge failed: %s",
                        exc)
            return False


def _set_clipboard(text):
    """Write to the OS clipboard without any third-party clipboard package."""
    if sys.platform == "win32":
        import win32clipboard  # part of pywin32, already a dependency
        import win32con

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return True
    # Elsewhere (the test machine) let the page use its own clipboard route.
    return False


def is_local_url(url):
    """True only for pages served by this Jarvis instance."""
    from urllib.parse import urlsplit

    parts = urlsplit(url or "")
    if parts.scheme in ("about", "data") and not parts.netloc:
        return True
    return (parts.scheme == "http"
            and parts.hostname in ("127.0.0.1", "localhost", "::1"))


def window_available():
    """(True, None) if a window can be opened, else (False, reason)."""
    try:
        import webview  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - ImportError or a broken install
        return False, (f"pywebview is not installed ({exc.__class__.__name__}). "
                       f"Install it with: pip install pywebview")
    if sys.platform == "win32":
        version = webview2_runtime_version()
        if version is None:
            return False, ("Microsoft Edge WebView2 Runtime was not found. It "
                           "ships with Windows 11 and current Windows 10; "
                           "without it the window cannot render")
    return True, None


def webview2_runtime_version():
    """The installed Edge WebView2 Runtime version, or None.

    Read from the registry keys Microsoft documents for detecting the
    runtime, per-machine first, then per-user. Read-only.
    """
    if sys.platform != "win32":
        return None
    import winreg

    client = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    candidates = [
        (winreg.HKEY_LOCAL_MACHINE,
         rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{client}"),
        (winreg.HKEY_LOCAL_MACHINE,
         rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{client}"),
        (winreg.HKEY_CURRENT_USER,
         rf"Software\Microsoft\EdgeUpdate\Clients\{client}"),
    ]
    for hive, path in candidates:
        try:
            with winreg.OpenKey(hive, path) as key:
                value, _ = winreg.QueryValueEx(key, "pv")
                if value and value != "0.0.0.0":
                    return value
        except OSError:
            continue
    return None


def _gui_backend():
    """Which pywebview renderer to ask for.

    On Windows only Edge WebView2 is acceptable. pywebview would otherwise
    fall back silently to the Internet Explorer engine (MSHTML), which cannot
    render this dashboard and is not something anyone should be pointed at a
    mailbox with. Asking for edgechromium explicitly makes that a failure we
    can catch and route to the browser instead.
    """
    if sys.platform == "win32":
        return "edgechromium"
    return os.environ.get("JARVIS_WEBVIEW_GUI") or None


def open_window(url, on_closed=None, self_test=None):
    """Open the dashboard in a desktop window. Blocks until it is closed.

    Returns True if a window was shown, False if one could not be created —
    in which case the caller should fall back to the browser. The reason is
    kept in `last_fallback_reason` and logged.

    `self_test`, if given, is called on a background thread with the window
    once the page has loaded; the desktop check uses it.
    """
    global last_fallback_reason

    ok, reason = window_available()
    if not ok:
        last_fallback_reason = reason
        log.warning("Desktop window unavailable: %s", reason)
        return False

    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                          WEBVIEW2_ARGUMENTS)
    import webview

    width, height = config.WINDOW_SIZE
    min_width, min_height = config.WINDOW_MIN_SIZE
    try:
        window = webview.create_window(
            config.WINDOW_TITLE, url,
            width=width, height=height, min_size=(min_width, min_height),
            background_color="#0d1020",
            js_api=Bridge(),
            text_select=True,
        )
        _guard_navigation(window, url)
        if on_closed is not None:
            window.events.closed += on_closed
        if self_test is not None:
            # "loaded" fires on every page load, including the one after the
            # navigation guard sends the window back; the test runs once.
            started = threading.Event()

            def start_self_test():
                if not started.is_set():
                    started.set()
                    threading.Thread(target=self_test, args=(window,),
                                     daemon=True).start()

            window.events.loaded += start_self_test
        log.info("Opening desktop window (%s renderer)",
                 _gui_backend() or "default")
        if self_test is None:
            window.events.shown += lambda: set_console_visible(False)
        webview.start(gui=_gui_backend(), private_mode=True,
                      debug=False)
        return True
    except Exception as exc:  # noqa: BLE001 - any renderer failure
        last_fallback_reason = f"{exc.__class__.__name__}: {exc}"
        log.warning("Could not open the desktop window: %s",
                    last_fallback_reason)
        set_console_visible(True)
        return False


def _guard_navigation(window, home_url):
    """Keep the window on the local dashboard (a backstop, see module doc).

    The Jarvis window must never become a general browser. If it ever ends
    up anywhere but the local server, it is sent straight back and the
    attempt is logged.
    """
    def check():
        try:
            current = window.get_current_url()
        except Exception:  # noqa: BLE001 - window closing
            return
        if current and not is_local_url(current):
            log.warning("Blocked navigation away from Jarvis to %s", current)
            window.load_url(home_url)

    try:
        window.events.loaded += check
    except Exception:  # noqa: BLE001 - older pywebview without events
        pass


def set_console_visible(visible):
    """Show or hide this process's console window (Windows only).

    The .exe is a console program, because --probe and --check-desktop print
    to it and browser mode is stopped by closing it. In window mode that
    console is just clutter beside the app, so it is hidden while the window
    is up. Failure is harmless: the console simply stays visible.
    """
    if sys.platform != "win32" or not config.HIDE_CONSOLE_IN_WINDOW_MODE:
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetConsoleWindow()
        if handle:
            ctypes.windll.user32.ShowWindow(handle, 5 if visible else 0)
    except Exception:  # noqa: BLE001
        pass


def open_browser(url):
    """The pre-window behaviour: open the dashboard in the default browser."""
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
