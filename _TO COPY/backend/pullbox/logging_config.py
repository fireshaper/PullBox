"""Central logging configuration for PullBox.

PullBox writes three separate, human-readable log files under
``{config_path}/logs``:

- ``pullbox.log``        — general application log: startup/shutdown, series and
                           issue lifecycle events, and issue status changes
                           (wanted → downloaded/skipped). Emitted on ``APP_LOGGER``
                           and every ``pullbox.*`` module logger that propagates
                           up to it.
- ``search.log``         — issues dispatched to the indexers. Emitted on
                           ``SEARCH_LOGGER`` (``pullbox.search``).
- ``library-import.log`` — library scans and imports. Emitted on
                           ``IMPORT_LOGGER`` (``pullbox.library_import``).

The search and import loggers do **not** propagate to the application log, so
each file stays focused on its own concern. Emit domain events by grabbing the
relevant logger via the name constants below (e.g.
``logging.getLogger(APP_LOGGER)``).
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pullbox.config import Settings

# Logger names — import these where you emit domain events.
APP_LOGGER = "pullbox"
SEARCH_LOGGER = "pullbox.search"
IMPORT_LOGGER = "pullbox.library_import"

# APScheduler logs its own job failures and misfires on this logger, not on any
# pullbox.* one. Left alone it propagates to the root logger, which alembic.ini
# points at stderr — so when PullBox runs as a service, a scheduled job that
# raises (poll_download_clients, the queue sweep) fails completely silently and
# nothing lands in any log file. Route it into the application log at WARNING:
# high enough to skip routine per-run chatter, low enough to catch every
# misfire and job exception.
SCHEDULER_LOGGER = "apscheduler"

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Rotate each file at 5 MB, keeping 3 old copies.
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3

_configured = False


def _file_handler(path: Path, formatter: logging.Formatter) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    return handler


def configure_logging(settings: Settings, *, force: bool = False) -> Path:
    """Wire up PullBox's three log files. Returns the log directory.

    Idempotent: repeated calls re-use the already-configured handlers unless
    ``force=True`` (which rebuilds them — useful in tests). The console handler
    from uvicorn is reused when present so app logs still appear on stdout.
    """
    global _configured

    log_dir = Path(settings.config_path) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if _configured and not force:
        return log_dir

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    level = logging.DEBUG if settings.debug else logging.INFO

    # Reuse uvicorn's console handler when running under uvicorn so app logs
    # still appear on the console; fall back to stdout otherwise.
    uvicorn_handler = next(iter(logging.getLogger("uvicorn").handlers), None)
    console: logging.Handler = uvicorn_handler or logging.StreamHandler(sys.stdout)

    # (logger name, file, propagate). The app logger does not propagate to the
    # root logger; the search/import loggers do not propagate to the app logger,
    # keeping each file focused.
    specs = [
        (APP_LOGGER, log_dir / "pullbox.log", False),
        (SEARCH_LOGGER, log_dir / "search.log", False),
        (IMPORT_LOGGER, log_dir / "library-import.log", False),
    ]

    for name, path, propagate in specs:
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = propagate
        # Drop any file handlers we added on a previous call to stay idempotent.
        for existing in list(lg.handlers):
            if isinstance(existing, RotatingFileHandler):
                lg.removeHandler(existing)
                existing.close()
        lg.addHandler(_file_handler(path, formatter))
        if console not in lg.handlers:
            lg.addHandler(console)

    # Send APScheduler's job-failure output to the application log too. It gets its
    # own handler instance because RotatingFileHandler must not be shared across
    # loggers that rotate independently.
    sched_log = logging.getLogger(SCHEDULER_LOGGER)
    sched_log.setLevel(logging.WARNING)
    sched_log.propagate = False
    for existing in list(sched_log.handlers):
        if isinstance(existing, RotatingFileHandler):
            sched_log.removeHandler(existing)
            existing.close()
    sched_log.addHandler(_file_handler(log_dir / "pullbox.log", formatter))
    if console not in sched_log.handlers:
        sched_log.addHandler(console)

    _configured = True
    logging.getLogger(APP_LOGGER).debug("Logging configured; log_dir=%s", log_dir)
    return log_dir
