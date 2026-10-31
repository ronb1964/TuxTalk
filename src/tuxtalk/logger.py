"""Lightweight logging setup for TuxTalk."""
import logging
import logging.handlers
import os
import threading
from pathlib import Path

# The log file in the config folder.
LOG_FILE_NAME = "tuxtalk.log"

# The log names from before the rename to TuxTalk. The rename migration moves
# the whole config folder, so these can sit next to the new log, holding old
# dictations.
_LEGACY_LOG_NAMES = ("talktype.log", "talktype.log.old")

# Roll the log over once it passes this size (checked at process startup).
_MAX_LOG_BYTES = 5 * 1024 * 1024

# The package logger every "tuxtalk.*" module logger propagates to.
_PACKAGE = "tuxtalk"

# The one file handler this process writes the log through (see _shared_handler).
_handler = None


def _log_dir() -> Path:
    """The dev or production config directory that holds the log.

    DEV_MODE=1 (the dev build) logs to ~/.config/tuxtalk-dev/, the
    AppImage to ~/.config/tuxtalk/ — matching config.py's split so the two
    never interleave.
    """
    # TUXTALK_LOG_DIR overrides everything. The test suite sets it (see
    # tests/conftest.py) so its fake entries never reach the user's real log.
    override = os.environ.get("TUXTALK_LOG_DIR")
    if override:
        return Path(override)
    config_dir = "tuxtalk-dev" if os.environ.get("DEV_MODE") == "1" else "tuxtalk"
    # Inside a Flatpak use the private per-app XDG_CONFIG_HOME so logs don't land
    # in (or read from) a HOST install when --filesystem=home is granted.
    # Mirrors config._config_home().
    if os.environ.get("FLATPAK_ID"):
        base = Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))
    else:
        base = Path.home() / ".config"
    return base / config_dir


def clear_log(log_dir: Path = None) -> bool:
    """Empty the log: truncate the log to zero and remove its .old backup and
    the pre-rename talktype logs. False if any of it couldn't be erased.

    Truncation rather than deletion is deliberate — the dictation service holds
    the file open in append mode, and on Linux O_APPEND writes always land at the
    real end of file, so after a truncate-to-zero the service simply continues
    at offset 0 instead of leaving a sparse, null-padded file behind.
    """
    d = log_dir or _log_dir()
    cleared = True
    try:
        main = d / LOG_FILE_NAME
        if main.exists():
            open(main, "w").close()  # truncate in place
    except OSError as e:
        logging.getLogger(__name__).warning(f"Couldn't clear the log: {e}")
        cleared = False
    # Nothing holds these open, so they can simply be removed.
    for name in (LOG_FILE_NAME + ".old",) + _LEGACY_LOG_NAMES:
        try:
            (d / name).unlink(missing_ok=True)
        except OSError as e:
            logging.getLogger(__name__).warning(f"Couldn't remove {name}: {e}")
            cleared = False
    return cleared


def redact_text(text, reveal: bool = False) -> str:
    """Render transcribed text for a log line without leaking it by default.

    The service logs raw and normalized transcriptions to diagnose dictation
    issues, but that means everything the user says would otherwise be written
    to disk in plain text. Redact by default — keep the length as a breadcrumb
    so the log still shows a transcription happened — and only reveal the full
    text (as `repr`) when the user has
    explicitly opted in via the log_transcripts setting.
    """
    if reveal:
        return repr(text)
    if isinstance(text, str):
        return f"[hidden, {len(text)} chars]"
    return "[hidden]"

class FailureNotes:
    """The last failure of each check, so a check that runs before every
    typed chunk logs a failure the first time, not each time: again only
    when the error changes, and once when the check works again."""

    def __init__(self):
        self._last = {}
        self._lock = threading.Lock()

    def failed(self, what, error) -> bool:
        """Note that *what* failed with *error*; True when that is news."""
        with self._lock:
            news = self._last.get(what) != str(error)
            self._last[what] = str(error)
        return news

    def worked(self, what) -> bool:
        """Note that *what* worked; True when it had been failing."""
        with self._lock:
            return self._last.pop(what, None) is not None


def _private_open(path, flags):
    """os.open for the log: a new file only this user can read."""
    return os.open(path, flags, 0o600)


class _PrivateLogHandler(logging.handlers.WatchedFileHandler):
    """The log's handler. Its file, and each one it reopens after another
    process rolled the log over, is made 0600: with "Log transcribed text"
    on the log holds every dictation, and a home folder is often readable
    by every account on the computer."""

    def _open(self):
        return open(self.baseFilename, self.mode, encoding=self.encoding,
                    errors=self.errors, opener=_private_open)


def _make_private(log_dir: Path) -> None:
    """Make the logs an earlier version left 0644 this user's only."""
    for name in (LOG_FILE_NAME, LOG_FILE_NAME + ".old") + _LEGACY_LOG_NAMES:
        try:
            os.chmod(log_dir / name, 0o600)
        except FileNotFoundError:
            pass
        except OSError as e:
            logging.getLogger(__name__).warning(f"Couldn't make {name} private: {e}")


def _shared_handler() -> logging.Handler:
    """The process's single log-file handler, created on first use.

    The tray and the dictation service are SEPARATE processes sharing this
    file, so a per-write RotatingFileHandler would race on rollover (lost or
    truncated lines). Instead the first logger set up in a process does one
    atomic rename when the file has grown past the cap, then plain appends,
    which are safe across processes. A WatchedFileHandler reopens the path when
    another process has renamed the file away, so this process's lines follow
    the new log instead of going into tuxtalk.log.old.
    """
    global _handler
    if _handler is not None:
        return _handler

    # Match config.py's dev/prod directory split
    log_dir = _log_dir()
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILE_NAME
    _make_private(log_dir)

    # Startup rollover: bound the log without a per-write rotation race.
    try:
        if log_file.exists() and log_file.stat().st_size > _MAX_LOG_BYTES:
            # os.replace is atomic; if another process already rolled over,
            # the source is gone and this raises (caught) — no double-rename.
            os.replace(log_file, log_dir / (LOG_FILE_NAME + ".old"))
    except OSError:
        pass

    handler = _PrivateLogHandler(log_file)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    _handler = handler
    return handler


def stop_file_logging() -> None:
    """Write no more to the log file in this process. Uninstalling with the
    settings deletes the log's folder, and the WatchedFileHandler would then
    raise on the next line: Python 3.10 reopens a removed file outside the
    handler's own error handling, and here the folder is gone too. Loggers
    set up afterwards get a handler that drops the lines."""
    global _handler
    if _handler is None:
        return
    loggers = [logging.getLogger()] + [lg for lg in logging.Logger.manager.loggerDict.values()
                                       if isinstance(lg, logging.Logger)]
    for lg in loggers:
        lg.removeHandler(_handler)
    _handler.close()
    _handler = logging.NullHandler()


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return the logger *name*, writing to the dev or production log file.

    Honours DEV_MODE so the dev version (DEV_MODE=1) logs to
    ~/.config/tuxtalk-dev/ and the AppImage logs to ~/.config/tuxtalk/ —
    they never interleave into one shared file.

    The file handler sits on the "tuxtalk" package logger, so every module
    logger under it, including plain logging.getLogger(__name__) ones, reaches
    the log through propagation, once per line. A logger outside the package
    (the tray and the service run as "__main__") gets the same handler itself.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    in_package = name == _PACKAGE or name.startswith(_PACKAGE + ".")
    target = logging.getLogger(_PACKAGE) if in_package else logger
    handler = _shared_handler()
    if handler not in target.handlers:
        if in_package:
            target.setLevel(logging.INFO)
        target.addHandler(handler)
    return logger
