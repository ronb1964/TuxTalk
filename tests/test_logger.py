"""Tests for logger module"""
import os
import logging
from tuxtalk.logger import setup_logger


def test_logger_creation():
    """Test that setup_logger creates a logger"""
    logger = setup_logger("test_module")
    assert logger is not None
    assert isinstance(logger, logging.Logger)
    assert logger.name == "test_module"


def test_logger_level():
    """Test that logger is set to INFO level by default"""
    logger = setup_logger("test_level")
    assert logger.level == logging.INFO


def test_logger_has_handlers():
    """Test that logger has file handler"""
    logger = setup_logger("test_handlers")
    # Should have at least one handler (file handler)
    assert len(logger.handlers) >= 1


def test_logger_file_handler_level():
    """Test that file handler is set to DEBUG level"""
    logger = setup_logger("test_file_level")
    file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) > 0
    # File handler should be DEBUG level for detailed logs
    assert file_handlers[0].level == logging.DEBUG


def test_logger_singleton_behavior():
    """Test that requesting same logger name returns same instance"""
    logger1 = setup_logger("test_singleton")
    logger2 = setup_logger("test_singleton")
    # Python's logging.getLogger() returns same instance for same name
    assert logger1 is logger2


def test_logger_different_names():
    """Test that different logger names create different loggers"""
    logger1 = setup_logger("test_module_1")
    logger2 = setup_logger("test_module_2")
    assert logger1.name != logger2.name


def test_logger_can_log_messages():
    """Test that logger can actually log messages"""
    logger = setup_logger("test_logging")
    # These should not raise exceptions
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")


def test_logger_format_includes_name():
    """Test that log format includes logger name"""
    logger = setup_logger("test_format")
    # Get the formatter from one of the handlers
    for handler in logger.handlers:
        if handler.formatter:
            format_str = handler.formatter._fmt
            # Should include module name in format
            assert "%(name)s" in format_str or "name" in format_str.lower()
            break


def _fresh_handler(monkeypatch, tmp_path):
    from tuxtalk import logger as tlog
    monkeypatch.setenv("TUXTALK_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(tlog, "_handler", None)
    return tlog


def test_lines_follow_the_log_after_another_process_rolls_it_over(monkeypatch, tmp_path):
    """After the service rolls the log over, the tray's lines go into the new
    tuxtalk.log, not into tuxtalk.log.old or a deleted file."""
    tlog = _fresh_handler(monkeypatch, tmp_path)
    log = tlog.setup_logger("test_rollover_follow")
    try:
        log.info("before")
        os.replace(tmp_path / "tuxtalk.log", tmp_path / "tuxtalk.log.old")
        log.info("after")
        assert "after" in (tmp_path / "tuxtalk.log").read_text()
        assert "after" not in (tmp_path / "tuxtalk.log.old").read_text()
    finally:
        for h in list(log.handlers):
            log.removeHandler(h)
            h.close()


def _mode(path):
    return os.stat(path).st_mode & 0o777


def test_only_this_user_can_read_the_log(monkeypatch, tmp_path):
    """With "Log transcribed text" on, the log holds every dictation, and a
    home folder is often open to every account: the log is 0600 from its
    first line, after another process rolls it over, and when an earlier
    version left it 0644. A new log folder is 0700."""
    old_umask = os.umask(0o022)
    folder = tmp_path / "config"
    folder.mkdir()
    for name in ("tuxtalk.log", "tuxtalk.log.old", "talktype.log"):
        (folder / name).write_text("an earlier version's line\n")
        os.chmod(folder / name, 0o644)
    tlog = _fresh_handler(monkeypatch, folder)
    log = tlog.setup_logger("test_private_log")
    try:
        log.info("first")
        for name in ("tuxtalk.log", "tuxtalk.log.old", "talktype.log"):
            assert _mode(folder / name) == 0o600, name
        os.replace(folder / "tuxtalk.log", folder / "tuxtalk.log.old")
        log.info("after the rollover")
        assert "after the rollover" in (folder / "tuxtalk.log").read_text()
        assert _mode(folder / "tuxtalk.log") == 0o600
    finally:
        for h in list(log.handlers):
            log.removeHandler(h)
            h.close()
        os.umask(old_umask)

    monkeypatch.setattr(tlog, "_handler", None)
    monkeypatch.setenv("TUXTALK_LOG_DIR", str(tmp_path / "new"))
    log = tlog.setup_logger("test_private_folder")
    try:
        assert _mode(tmp_path / "new") == 0o700
        assert _mode(tmp_path / "new" / "tuxtalk.log") == 0o600
    finally:
        for h in list(log.handlers):
            log.removeHandler(h)
            h.close()


def test_one_handler_per_process_and_one_line_per_message(monkeypatch, tmp_path):
    tlog = _fresh_handler(monkeypatch, tmp_path)
    a = tlog.setup_logger("test_shared_a")
    b = tlog.setup_logger("test_shared_b")
    try:
        assert a.handlers == b.handlers and len(a.handlers) == 1
        a.info("only once")
        assert (tmp_path / "tuxtalk.log").read_text().count("only once") == 1
    finally:
        for log in (a, b):
            for h in list(log.handlers):
                log.removeHandler(h)
        tlog._handler.close()


def test_package_loggers_reach_the_log_even_without_setup_logger():
    """config.py and others use plain logging.getLogger(__name__), and those
    lines must still reach tuxtalk.log."""
    from tuxtalk import config, logger as tlog  # noqa: F401  (config installs the handler)
    package = logging.getLogger("tuxtalk")
    assert tlog._handler in package.handlers
    assert logging.getLogger("tuxtalk.download_utils").getEffectiveLevel() <= logging.INFO
    # Module loggers carry no handler of their own, so nothing is written twice.
    assert logging.getLogger("tuxtalk.config").handlers == []


def test_the_test_suite_never_writes_to_the_real_log():
    """conftest points the log at a temporary folder before anything imports
    tuxtalk. A tuxtalk import above that line makes the one file handler on
    the user's real log, and every test's fake dictation lands there."""
    from pathlib import Path
    from tuxtalk import logger as log
    assert Path(log._handler.baseFilename).parent == Path(os.environ["TUXTALK_LOG_DIR"])
