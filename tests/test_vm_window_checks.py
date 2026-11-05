"""The VM checks for opening apps and acting on windows by voice find what
they started, end only that, and type into the editor, not the test window.

Their first runs misread real desktops: KWrite and Firefox never showed in
AT-SPI, so "no window appeared" while one had, and a click in the middle of
the screen typed into the test window instead of an editor opened beside it.
"""
import pytest
from conftest import import_vm_tool

# The harness reads its settings with tomllib, new in Python 3.11.
pytest.importorskip("tomllib", reason="the VM harness needs Python 3.11 or newer")
run = import_vm_tool("run")
open_apps = import_vm_tool("checks_open_apps")
actions = import_vm_tool("checks_voice_actions")


@pytest.mark.parametrize("exec_line, program", [
    ("/usr/bin/kwrite %U", "kwrite"),
    ("gnome-text-editor --new-window %U", "gnome-text-editor"),
    ("env GTK_THEME=Adwaita xed %U", "xed"),
    ("", None),
])
def test_the_program_a_menu_entry_runs_is_read_from_its_exec_line(exec_line, program):
    assert open_apps.program_of(exec_line) == program


def test_an_apps_window_is_known_by_its_program_or_its_menu_name_only():
    """On KDE every Qt app comes into AT-SPI when the switch goes on; the
    panel must never be taken for the editor (and then ended)."""
    names = ("cosmic-edit", "CosmicEdit", "COSMIC Text Editor")
    assert open_apps.window_of("COSMIC Text Editor", names)
    assert open_apps.window_of("cosmic-edit", names)
    assert not open_apps.window_of("plasmashell", ("kwrite", "kwrite", "KWrite"))


PS = """\
   1201 kwrite           /usr/bin/kwrite
   1300 Isolated Web Co  /usr/lib64/firefox/firefox -contentproc -isForBrowser
   1305 kwrite           kwrite --new
   1310 kwrite-helper    /usr/libexec/kwrite-helper
"""


def test_a_process_name_with_spaces_is_read_whole():
    found = open_apps.parse_ps(PS)
    assert found["1300"] == ("Isolated Web Co",
                             "/usr/lib64/firefox/firefox -contentproc -isForBrowser")


def test_only_the_programs_processes_started_since_are_new():
    """The user's KWrite from before is left running; a program whose name
    only starts the same is not the editor."""
    now = open_apps.parse_ps(PS)
    assert open_apps._new_pids(now, {"1201": ("kwrite", "/usr/bin/kwrite")}, "kwrite") == ["1305"]


class _Desktop:
    """A stand-in VM with one editor window, not focused, off to the side
    of the screen; a click inside it gives it the focus."""

    def __init__(self):
        self.vm = self
        self.clicks = []

    def ui(self, command, *args, app=None, **_options):
        focused = any(500 < x < 1100 and 200 < y < 600 for x, y in self.clicks)
        return [{"app": "xed", "title": "Unsaved Document 1 - xed", "size": [600, 400],
                 "position": [500, 200], "states": ["active"] if focused else []}]

    def click(self, x, y):
        self.clicks.append((x, y))


def test_an_editor_opened_beside_the_middle_is_clicked_in_its_own_middle(monkeypatch):
    """On Cinnamon the editor opened beside the middle of the screen, the
    click there landed on the test window, and "hi" was typed into it."""
    monkeypatch.setattr(actions, "_until", lambda _seconds, test: test())
    desktop = _Desktop()
    assert actions._in_front(desktop, "xed")
    assert desktop.clicks == [(800, 400)]
