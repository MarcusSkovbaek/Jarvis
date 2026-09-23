"""
app.py — the Flask application factory.

Serves exactly two things: the dashboard page, and the /api/v1 blueprint from
api.py. Everything it renders comes from dashboard.build_view(), so the page
and the API can never drift apart.

Templates and static files are local only. There are no CDN links anywhere in
this application; it renders correctly with no network connection.
"""

import logging
import os
import sys

from flask import Flask, render_template

import api
import config
import dashboard
import prompt_builder

log = logging.getLogger("jarvis.app")


# 'unsafe-eval' is required by the desktop window: pywebview delivers its
# bridge (window.pywebview.api) and the results of every bridge call into the
# page through eval(). Without it the copy buttons in the window would never
# get an answer. It does not weaken what this policy is for: loads and
# connections are still limited to Jarvis itself, and inline scripts are
# still refused, so nothing injected into the page can run.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)


def _resource_root():
    """Template/static root, accounting for a PyInstaller one-file bundle."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def create_app(sync_loop=None, outlook_reader_factory=None):
    root = _resource_root()
    app = Flask(
        __name__,
        template_folder=os.path.join(root, "templates"),
        static_folder=os.path.join(root, "static"),
    )
    app.config["SYNC_LOOP"] = sync_loop
    app.config["OUTLOOK_READER_FACTORY"] = outlook_reader_factory
    app.config["JSON_SORT_KEYS"] = False
    app.register_blueprint(api.bp)

    @app.get("/")
    def index():
        view = dashboard.build_view()
        return render_template(
            "dashboard.html",
            view=view,
            config=config,
            refresh_seconds=config.DASHBOARD_REFRESH_SECONDS,
            csp_line=prompt_builder.CSP_LINE,
        )

    @app.after_request
    def security_headers(response):
        # Jarvis's own pages load nothing from outside, and this makes the
        # browser (or the desktop window) enforce that: were a template ever
        # to reference an external font or script, it would simply not load.
        # Inline style attributes are allowed because the calendar timeline
        # positions its events with them; inline scripts are not.
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/healthz")
    def healthz():
        return {
            "ok": True,
            "backend": config.OUTLOOK_BACKEND,
            "instance_id": config.INSTANCE_ID,
            "pid": os.getpid(),
            "data_dir": config.DATA_DIR,
        }

    @app.errorhandler(404)
    def not_found(_error):
        return {"ok": False, "error": "Not found"}, 404

    @app.errorhandler(500)
    def server_error(error):  # pragma: no cover - defensive
        log.exception("Unhandled error: %s", error)
        return {"ok": False, "error": "Internal error - see sync.log"}, 500

    return app
