"""
sync.py — the background sync loop.

Runs every config.SYNC_INTERVAL_MINUTES on a daemon thread, pulls from
Outlook, runs the processors, writes the results into SQLite, and purges
expired records. The dashboard only ever reads the cache, so page loads are
instant and never block on Outlook.

If Outlook is not running the failure is logged and the loop carries on; the
next cycle retries. A failed sync leaves the previous cache untouched.
"""

import logging
import logging.handlers
import sys
import threading
import time
from datetime import datetime, timezone

import calendar_reader
import config
import db
import email_processor
from outlook_reader import OutlookReader, OutlookUnavailable

log = logging.getLogger("jarvis.sync")

_LOG_CONFIGURED = False


def configure_logging(level=logging.INFO, console=True):
    """Set up sync.log. Safe to call more than once.

    Everything lands in one file: Jarvis's own loggers, the werkzeug request
    log, and any uncaught exception. When running as the compiled .exe the
    console window may be closed or never seen, so the file is the record.
    """
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return logging.getLogger("jarvis")

    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)-16s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handlers = []
    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_PATH,
        maxBytes=config.SYNC_LOG_MAX_BYTES,
        backupCount=config.SYNC_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

    if console:
        # The Windows console often runs a legacy codepage. Ask for UTF-8 and
        # fall back to replacing unencodable characters, so a stray dash in a
        # subject line can never take the logger (and the sync) down.
        # When frozen with console=False, sys.stdout can be None entirely.
        stream_target = sys.stdout
        if stream_target is not None:
            try:
                stream_target.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
            stream = logging.StreamHandler(stream_target)
            stream.setFormatter(formatter)
            handlers.append(stream)

    root = logging.getLogger("jarvis")
    root.setLevel(level)
    for handler in handlers:
        root.addHandler(handler)

    # Flask's request log is useful evidence of what the UI actually called,
    # so it goes into the log FILE. Deliberately not onto the console handler:
    # it emits a line per HTTP request, and if Jarvis is launched by a parent
    # process that pipes stdout without draining it, that volume fills the OS
    # pipe buffer and the next write blocks a request thread mid-response.
    # File-only keeps the full record without ever blocking on a stalled pipe.
    werkzeug = logging.getLogger("werkzeug")
    werkzeug.setLevel(level)
    werkzeug.propagate = False
    werkzeug.addHandler(file_handler)

    # Anything that escapes a thread or the main loop is recorded, not lost to
    # a console window that has already closed.
    def _log_uncaught(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        root.critical("Uncaught exception",
                      exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _log_uncaught
    if hasattr(threading, "excepthook"):
        def _log_thread_exc(args):
            root.critical("Uncaught exception in thread %s", args.thread_name,
                          exc_info=(args.exc_type, args.exc_value,
                                    args.exc_traceback))
        threading.excepthook = _log_thread_exc

    _LOG_CONFIGURED = True
    return root


def log_startup_banner(log, extra=None):
    """One block at the top of every run saying exactly what is configured."""
    log.info("=" * 68)
    log.info("Jarvis starting")
    log.info("  instance id       : %s", config.INSTANCE_ID)
    log.info("  frozen executable : %s", bool(getattr(sys, "frozen", False)))
    log.info("  identity          : %s", config.USER_EMAIL)
    log.info("  outlook backend   : %s", config.OUTLOOK_BACKEND)
    log.info("  database          : %s", config.DB_PATH)
    log.info("  log file          : %s", config.LOG_PATH)
    log.info("  prompt version    : v%s", config.PROMPT_VERSION)
    log.info("  sync interval     : %s minutes", config.SYNC_INTERVAL_MINUTES)
    log.info("  thresholds        : inbound %sd, sent %sd, calendar %sd ahead",
             config.INBOUND_OVERDUE_DAYS, config.SENT_AWAITING_REPLY_DAYS,
             config.CALENDAR_LOOKAHEAD_DAYS)
    log.info("  retention         : %s days", config.RETENTION_DAYS)
    for line in (extra or []):
        log.info("  %s", line)
    log.info("=" * 68)


def run_sync(reader=None, now=None, db_path=None):
    """One complete sync cycle. Never raises — failures are logged.

    Returns a result dict: {"status": "ok"|"error", "counts": {...}, ...}
    """
    now = now or datetime.now(timezone.utc)
    run_id = db.start_sync_run(db_path=db_path)
    started = time.monotonic()
    owns_reader = reader is None
    log.info("Sync started (backend=%s)",
             reader.backend if reader else config.OUTLOOK_BACKEND)

    try:
        reader = reader or OutlookReader()
        raw = reader.read_all(now=now)
        log.info("Pulled from Outlook: %s sent, %s inbox, %s calendar items",
                 len(raw["sent"]), len(raw["inbox"]), len(raw["calendar"]))

        processed = email_processor.process(raw["sent"], raw["inbox"], now)
        topics = email_processor.conversation_topics(raw["sent"], raw["inbox"])
        meetings = calendar_reader.process_calendar(raw["calendar"], now, topics)

        results = {
            config.CATEGORY_AWAITING_REPLY:
                processed[config.CATEGORY_AWAITING_REPLY],
            config.CATEGORY_OVERDUE_INBOUND:
                processed[config.CATEGORY_OVERDUE_INBOUND],
            config.CATEGORY_MEETING: meetings,
        }
        for category, items in results.items():
            db.replace_cached_items(category, items, synced_at=now, db_path=db_path)

        purged = db.purge_old_records(now=now, db_path=db_path)

        counts = {category: len(items) for category, items in results.items()}
        counts["meetings_pending"] = sum(1 for m in meetings if m["pending_response"])
        counts["meetings_unprepared"] = sum(1 for m in meetings if m["unprepared"])
        counts["purged"] = sum(purged.values())

        bands = email_processor.group_by_age_band(
            results[config.CATEGORY_AWAITING_REPLY])
        elapsed = time.monotonic() - started

        log.info(
            "Sync OK in %.2fs - awaiting your reply: %s | no response received: "
            "%s (%s) | meetings: %s (%s pending, %s unprepared) | purged: %s",
            elapsed,
            counts[config.CATEGORY_OVERDUE_INBOUND],
            counts[config.CATEGORY_AWAITING_REPLY],
            ", ".join(f"{label}: {len(items)}" for label, items in bands.items()),
            counts[config.CATEGORY_MEETING],
            counts["meetings_pending"],
            counts["meetings_unprepared"],
            counts["purged"],
        )
        db.finish_sync_run(run_id, "ok", counts, db_path=db_path)
        return {"status": "ok", "counts": counts, "elapsed": elapsed,
                "results": results}

    except OutlookUnavailable as exc:
        # Expected condition: Outlook closed, or the profile not loaded yet.
        message = str(exc)
        log.warning("Sync skipped - %s. Cached results kept; retrying next cycle.",
                    message)
        db.finish_sync_run(run_id, "error", error=message, db_path=db_path)
        return {"status": "error", "error": message, "counts": {}}

    except Exception as exc:  # noqa: BLE001 - the loop must never die
        message = f"{type(exc).__name__}: {exc}"
        log.exception("Sync failed unexpectedly - %s", message)
        db.finish_sync_run(run_id, "error", error=message, db_path=db_path)
        return {"status": "error", "error": message, "counts": {}}

    finally:
        if owns_reader and isinstance(reader, OutlookReader):
            reader.close()


class SyncLoop:
    """Daemon thread that calls run_sync on a fixed interval."""

    def __init__(self, interval_minutes=None, reader_factory=None, db_path=None):
        self.interval = (config.SYNC_INTERVAL_MINUTES if interval_minutes is None
                         else interval_minutes) * 60
        self._reader_factory = reader_factory or OutlookReader
        self._db_path = db_path
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.last_result = None

    def sync_now(self):
        """Run one cycle on the calling thread. Used at startup and by the API."""
        with self._lock:
            reader = self._reader_factory()
            try:
                self.last_result = run_sync(reader=reader, db_path=self._db_path)
            finally:
                close = getattr(reader, "close", None)
                if callable(close):
                    close()
            return self.last_result

    def request_sync(self):
        """Ask the loop to run a cycle immediately, without blocking."""
        self._wake.set()

    def _run(self):
        if config.SYNC_ON_STARTUP:
            self.sync_now()
        while not self._stop.is_set():
            # Wake early if request_sync() was called.
            self._wake.wait(timeout=self.interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            self.sync_now()
        log.info("Sync loop stopped")

    def start(self):
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="jarvis-sync",
                                        daemon=True)
        self._thread.start()
        log.info("Sync loop started (every %s minutes)", self.interval // 60)
        return self._thread

    def stop(self, timeout=5):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())


if __name__ == "__main__":
    configure_logging()
    db.init_db()
    result = run_sync()
    print(f"\nResult: {result['status']}  counts={result.get('counts')}")
