"""TuxTalk's log: what a line says, which lines are kept, and one file shared
by the tray and the dictation service."""
import logging
import os
import re

import pytest


@pytest.fixture
def fresh_log(monkeypatch, tmp_path):
    """(setup_logger writing to a log file of the test's own, that file)."""
    from tuxtalk import logger as tlog
    monkeypatch.setenv("TUXTALK_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(tlog, "_handler", None)
    made = []

    def setup(name, level=logging.INFO):
        made.append(tlog.setup_logger(name, level))
        return made[-1]
    yield setup, tmp_path / "tuxtalk.log"
    for log in made:
        for handler in list(log.handlers):
            log.removeHandler(handler)
    tlog._handler.close()


def test_a_line_says_when_which_module_and_how_bad(fresh_log):
    setup, log_file = fresh_log
    setup("test_format").warning("the microphone went away")
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} - test_format - WARNING - "
                        r"the microphone went away\n", log_file.read_text())


def test_info_lines_are_kept_and_debug_lines_only_when_asked_for(fresh_log):
    setup, log_file = fresh_log
    usual = setup("test_usual")
    usual.info("kept")
    usual.debug("left out")
    setup("test_detailed", logging.DEBUG).debug("asked for")
    written = log_file.read_text()
    assert "kept" in written and "asked for" in written
    assert "left out" not in written


def test_lines_follow_the_log_after_another_process_rolls_it_over(fresh_log):
    """After the service rolls the log over, the tray's lines go into the new
    tuxtalk.log, not into tuxtalk.log.old or a deleted file."""
    setup, log_file = fresh_log
    log = setup("test_rollover_follow")
    log.info("before")
    os.replace(log_file, log_file.with_name("tuxtalk.log.old"))
    log.info("after")
    assert "after" in log_file.read_text()
    assert "after" not in log_file.with_name("tuxtalk.log.old").read_text()


def test_one_handler_per_process_and_one_line_per_message(fresh_log):
    setup, log_file = fresh_log
    a = setup("test_shared_a")
    b = setup("test_shared_b")
    assert a.handlers == b.handlers and len(a.handlers) == 1
    a.info("only once")
    assert log_file.read_text().count("only once") == 1


def _mode(path):
    return os.stat(path).st_mode & 0o777


def test_only_this_user_can_read_the_log(fresh_log):
    """With "Log transcribed text" on, the log holds every dictation, and a
    home folder is often open to every account: the log is 0600 from its
    first line, after another process rolls it over, and when an earlier
    version left it 0644."""
    setup, log_file = fresh_log
    folder = log_file.parent
    old_umask = os.umask(0o022)
    try:
        for name in ("tuxtalk.log", "tuxtalk.log.old", "talktype.log"):
            (folder / name).write_text("an earlier version's line\n")
            os.chmod(folder / name, 0o644)
        log = setup("test_private_log")
        log.info("first")
        for name in ("tuxtalk.log", "tuxtalk.log.old", "talktype.log"):
            assert _mode(folder / name) == 0o600, name
        os.replace(log_file, folder / "tuxtalk.log.old")
        log.info("after the rollover")
        assert "after the rollover" in log_file.read_text()
        assert _mode(log_file) == 0o600
    finally:
        os.umask(old_umask)


def test_a_new_log_folder_is_this_users_only(fresh_log, monkeypatch, tmp_path):
    setup, _log_file = fresh_log
    monkeypatch.setenv("TUXTALK_LOG_DIR", str(tmp_path / "new"))
    setup("test_private_folder")
    assert _mode(tmp_path / "new") == 0o700
    assert _mode(tmp_path / "new" / "tuxtalk.log") == 0o600


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
