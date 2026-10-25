"""The test suite must never write to the user's real TuxTalk log, nor
anywhere else in their home folder.

Fake entries there (a made-up AppImage mount path, simulated wtype timeouts,
"Custom commands applied") look like real problems when the log is read to
diagnose one.
"""
import logging
import os
import pwd
import subprocess
import sys
from pathlib import Path

from tuxtalk import config
from tuxtalk import logger as talktype_logger

# The account's home folder, whatever HOME says.
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def test_log_file_is_not_in_the_users_config():
    real_config = REAL_HOME / ".config"
    log_dir = talktype_logger._log_dir()
    assert log_dir != real_config and real_config not in log_dir.parents


def test_handlers_created_on_import_point_at_the_test_log_dir():
    import tuxtalk.app  # noqa: F401  (imports many modules that set up loggers)
    for name in list(logging.root.manager.loggerDict):
        if not name.startswith("talktype"):
            continue
        for handler in getattr(logging.getLogger(name), "handlers", []):
            if isinstance(handler, logging.FileHandler):
                assert REAL_HOME / ".config" not in Path(handler.baseFilename).parents, (
                    f"{name} logs to {handler.baseFilename}")


def test_folders_found_on_import_are_not_the_users():
    # Found once, when tuxtalk is imported: before any fixture could move them.
    from tuxtalk import torch_init
    for folder in (config._config_home(), config._data_home(), torch_init._appimage_cuda_path):
        assert REAL_HOME not in Path(folder).parents, folder


def test_programs_the_tests_start_get_the_throwaway_home_too():
    seen = subprocess.run([sys.executable, "-c", "import os; print(os.path.expanduser('~'))"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert Path(seen) != REAL_HOME and REAL_HOME not in Path(seen).parents
