"""Whether first-run setup has been finished on this computer: a marker file
in the data folder. Until it's there the tray shows the welcome screens and
the dictation service watches no hotkeys. Kept apart from first_run_setup,
whose windows the dictation service has no use for.
"""
import os

from . import config

_DONE_MARKER = ".first_run_done"


def is_first_run():
    """True until first-run setup has been finished on this computer."""
    return not os.path.exists(os.path.join(config.get_data_dir(), _DONE_MARKER))


def mark_first_run_complete():
    """Record that first-run setup is finished, so it isn't shown again."""
    data_dir = config.get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, _DONE_MARKER), "w") as f:
        f.write("done")
