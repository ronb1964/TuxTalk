"""TuxTalk - voice dictation for Linux"""

# First of all: move a TalkType install's folders to TuxTalk's names, before
# any module (the logger above all) creates the new ones. What was moved, or
# couldn't be, is logged by the tray. See rename_migration.py.
from . import rename_migration as _rename_migration
MIGRATION_LOG = _rename_migration.run_at_startup()

import ctypes.util  # noqa: E402
import os  # noqa: E402

__version__ = "0.18.2"

# Where the AppImage (and the .deb/.rpm made from it) keeps its own PortAudio:
# <root>/usr/src/tuxtalk/ -> <root>/usr/lib/portaudio/. In a dev checkout or
# the Flatpak this folder doesn't exist and nothing changes.
_BUNDLED_PORTAUDIO_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "lib", "portaudio")


def _use_bundled_portaudio(lib_dir=_BUNDLED_PORTAUDIO_DIR):
    """Let sounddevice fall back to the bundled PortAudio.

    sounddevice finds PortAudio with ctypes.util.find_library("portaudio"),
    which only consults the system's linker cache, so a copy inside the
    AppImage is invisible to it and a system without libportaudio would have
    no microphone at all.

    The system's PortAudio still wins when there is one. Runs on package
    import, which is always before sounddevice is imported.
    """
    bundled = os.path.join(lib_dir, "libportaudio.so.2")
    if not os.path.isfile(bundled):
        return
    system_find_library = ctypes.util.find_library

    def find_library(name):
        found = system_find_library(name)
        if found is None and name == "portaudio":
            return os.path.normpath(bundled)
        return found

    ctypes.util.find_library = find_library


_use_bundled_portaudio()
