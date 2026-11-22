"""The wall around TuxTalk's portable "brain" (CODE_HEALTH 128).

The brain is the dictation code that turns sound into text and text into
what gets typed: the text rules, the correction check, silence and voice
detection. A Windows or Mac TuxTalk would run it under screens of its own,
so it stays free of GTK, D-Bus, the Linux keyboard and the other modules
that exist only on Linux. A module joins BRAIN once everything it imports,
lazily too, is another brain module, Python's portable library or a
package that runs everywhere; what it needs from the desktop it gets from
its caller.
"""
import ast
import pathlib
import subprocess
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "tuxtalk"

BRAIN = frozenset({
    "audio_windows", "chunk_text", "chunking", "cleanup", "correction_safety",
    "custom_commands", "hallucinations", "key_commands", "logger", "normalize",
    "parakeet_bias", "silence", "spoken_numbers", "spoken_quotes", "voice_detector",
    "voice_edit_rules", "word_spelling",
})

# What only Linux (or Unix) has, or its desktop: GTK and GLib, D-Bus, the
# kernel's input devices, X11, and the Unix-only parts of Python's library.
PLATFORM_ONLY = frozenset({
    "gi", "dbus", "pydbus", "evdev", "Xlib", "pynput", "fcntl", "termios", "pwd", "grp",
    "pty", "resource", "posix",
})


def _imports(module: str) -> set:
    """Every module *module* imports, at its top or inside a function, as
    "tuxtalk.name" for its own and the full name for the rest."""
    found = set()
    for node in ast.walk(ast.parse((SRC / f"{module}.py").read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level:
            found |= ({f"tuxtalk.{node.module}"} if node.module
                      else {f"tuxtalk.{alias.name}" for alias in node.names})
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module)
    return found


@pytest.mark.parametrize("module", sorted(BRAIN))
def test_a_brain_module_imports_only_the_brain_and_what_runs_everywhere(module):
    imported = _imports(module)
    outside = sorted(name for name in imported
                     if name.startswith("tuxtalk.") and name[8:] not in BRAIN)
    platform = sorted(name for name in imported if name.split(".")[0] in PLATFORM_ONLY)
    assert outside == [] and platform == [], (
        f"{module} reaches out of the brain: {outside + platform}. Pass what it needs "
        f"in from the caller, or move the Linux part out of it")


# Run in a fresh Python where the Linux-only modules can't be imported, as on
# Windows or a Mac.
_LOAD_WITHOUT_LINUX = """
import importlib, sys

class NotHere:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in sys.argv[1].split(","):
            raise ImportError(f"{name} doesn't exist on this system")

sys.meta_path.insert(0, NotHere())
for module in sys.argv[2:]:
    importlib.import_module(f"tuxtalk.{module}")
"""


def test_the_brain_loads_where_the_linux_modules_dont_exist():
    ran = subprocess.run(
        [sys.executable, "-c", _LOAD_WITHOUT_LINUX, ",".join(PLATFORM_ONLY), *sorted(BRAIN)],
        capture_output=True, text=True, timeout=60, cwd=SRC.parent)
    assert ran.returncode == 0, ran.stderr
