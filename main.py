"""
main.py — entrypoint. Starts the sync loop, then serves the dashboard.

    python main.py                 run with the configured backend
    python main.py --backend mock  force the mock mailbox
    python main.py --no-browser    do not open a browser window
    python main.py --sync-once     run one sync, print the result, exit

The server binds to 127.0.0.1 only. It is not reachable from the network.
"""

import argparse
import logging
import sys
import threading
import webbrowser

import config


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="jarvis", description="Local Outlook assistant")
    parser.add_argument("--backend", choices=["mock", "com"],
                        help="Outlook backend to use (default: config.OUTLOOK_BACKEND)")
    parser.add_argument("--port", type=int, default=config.FLASK_PORT)
    parser.add_argument("--host", default=config.FLASK_HOST)
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser window on startup")
    parser.add_argument("--no-sync", action="store_true",
                        help="serve cached data without starting the sync loop")
    parser.add_argument("--sync-once", action="store_true",
                        help="run one sync, print the result and exit")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--log-level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="logging verbosity (default: INFO, or DEBUG with --debug)")
    parser.add_argument("--version", action="store_true",
                        help="print version details and exit")
    parser.add_argument("--probe", action="store_true",
                        help="read-only probe of the real mailbox; reports "
                             "what Jarvis sees and exits. Starts no server "
                             "and touches nothing in Outlook. Writes the "
                             "report next to the app as probe-output.txt.")
    parser.add_argument("--full", action="store_true",
                        help="with --probe: print more sample rows")
    parser.add_argument("--redact", action="store_true",
                        help="with --probe: accepted and ignored; redaction "
                             "is on by default")
    parser.add_argument("--no-redact", dest="no_redact", action="store_true",
                        help="with --probe: do NOT replace addresses, "
                             "subjects and bodies with stand-ins. The report "
                             "then contains real mail data - for your eyes "
                             "only, not for sharing.")
    parser.add_argument("--report", default=None,
                        help="with --probe: write the report here instead of "
                             "probe-output.txt")
    return parser.parse_args(argv)


def _is_loopback(host):
    """True only for addresses that cannot be reached from another machine."""
    import ipaddress

    if not host:
        return False
    name = str(host).strip().strip("[]").lower()
    if name in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _port_status(host, port):
    """(in_use, occupant) — occupant is the /healthz body if it is a Jarvis."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        if probe.connect_ex((host, port)) != 0:
            return False, None

    # Something is listening. Ask whether it is one of ours, so the message
    # can say so rather than leaving the user guessing.
    try:
        import json
        import urllib.request
        with urllib.request.urlopen(
                f"http://{host}:{port}/healthz", timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
        if isinstance(body, dict) and "instance_id" in body:
            return True, body
    except Exception:  # noqa: BLE001 - any failure just means "not ours"
        pass
    return True, None


def main(argv=None):
    args = parse_args(argv)
    if args.backend:
        config.OUTLOOK_BACKEND = args.backend

    # Imported after the backend override so every module sees the same config.
    import app as app_module
    import db
    import sync

    if args.log_level:
        level = getattr(logging, args.log_level)
    else:
        level = logging.DEBUG if args.debug else logging.INFO
    sync.configure_logging(level=level)
    log = logging.getLogger("jarvis.main")

    if args.version:
        print(f"Jarvis (prompt template v{config.PROMPT_VERSION})")
        print(f"  frozen   : {bool(getattr(sys, 'frozen', False))}")
        print(f"  backend  : {config.OUTLOOK_BACKEND}")
        print(f"  database : {config.DB_PATH}")
        print(f"  log file : {config.LOG_PATH}")
        return 0

    if args.probe:
        # Read-only diagnostic: no server, no database, no log file beyond
        # what logging already opened. Defaults to the real mailbox, because
        # that is the only thing worth probing, and to redacted output,
        # because a report that is unsafe to share by default is a report
        # that eventually gets shared unsafely.
        import probe
        return probe.run(full=args.full, redact=not args.no_redact,
                         backend=args.backend or "com",
                         report_path=args.report)

    sync.log_startup_banner(log, extra=[f"log level         : {args.log_level or
                                        ('DEBUG' if args.debug else 'INFO')}"])

    # Jarvis is a local tool and the dashboard has no authentication, so
    # binding it anywhere but loopback would publish the contents of the
    # mailbox to the network. Refuse rather than warn: a warning scrolls past.
    if not _is_loopback(args.host):
        log.error("Refusing to bind to %s.", args.host)
        log.error("  The dashboard has no login and shows your mail, so it is "
                  "served to this machine only.")
        log.error("  Use --host 127.0.0.1 (the default), or localhost.")
        return 2

    # The port is checked BEFORE the database is touched, so a launch that
    # cannot serve leaves nothing behind. Without this the user gets a raw
    # WinError 10048 traceback, which is a poor first experience — and it
    # happens easily, because a one-file .exe killed ungracefully can leave
    # the worker process behind still holding the socket.
    busy, occupant = (False, None) if args.sync_once else \
        _port_status(args.host, args.port)
    if busy:
        log.error("Port %s on %s is already in use.", args.port, args.host)
        if occupant:
            log.error("  It is another Jarvis instance (id %s, data dir %s).",
                      occupant.get("instance_id"), occupant.get("data_dir"))
            log.error("  Open http://%s:%s/ to use it, or close it first.",
                      args.host, args.port)
        else:
            log.error("  Something else is listening there.")
        log.error("  Alternatively start this one on another port: "
                  "--port %s", args.port + 1)
        return 2

    applied = db.init_db()
    if applied:
        log.info("Database migrations applied: %s",
                 ", ".join(f"v{v} {d}" for v, d in applied))
    else:
        log.info("Database schema already at v%s", db.schema_version())

    if args.sync_once:
        result = sync.run_sync()
        print(f"status={result['status']} counts={result.get('counts')}")
        return 0 if result["status"] == "ok" else 1

    loop = sync.SyncLoop()
    flask_app = app_module.create_app(sync_loop=loop)

    if not args.no_sync:
        loop.start()
    else:
        log.info("Sync loop disabled (--no-sync); serving cached data only")

    url = f"http://{args.host}:{args.port}/"
    log.info("Dashboard at %s", url)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        flask_app.run(host=args.host, port=args.port, debug=args.debug,
                      use_reloader=False, threaded=True)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        loop.stop()
        log.info("Jarvis stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
