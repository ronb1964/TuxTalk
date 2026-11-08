"""The VM checks for opening apps and acting on windows by voice find what
they started, end only that, type into the editor and not the test window,
and run again on GNOME once the extension is there.

Their first runs misread real desktops: KWrite and Firefox never showed in
AT-SPI, so "no window appeared" while one had; a click in the middle of the
screen typed into the test window instead of an editor opened beside it;
and the checks skipped on GNOME "until the extension" never ran again.
"""
import pytest
from conftest import import_vm_tool

# The harness reads its settings with tomllib, new in Python 3.11.
pytest.importorskip("tomllib", reason="the VM harness needs Python 3.11 or newer")
run = import_vm_tool("run")
open_apps = import_vm_tool("checks_open_apps")
actions = import_vm_tool("checks_voice_actions")
extension = import_vm_tool("checks_extension")
voices = import_vm_tool("voices")


@pytest.mark.parametrize("exec_line, program", [
    ("/usr/bin/kwrite %U", "kwrite"),
    ("gnome-text-editor --new-window %U", "gnome-text-editor"),
    ("env GTK_THEME=Adwaita xed %U", "xed"),
    # Silverblue's editor: "flatpak" never matched its window or a process.
    ("/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=gnome-text-editor "
     "--file-forwarding org.gnome.TextEditor @@u %U @@", "gnome-text-editor"),
    ("/usr/bin/flatpak run --branch=stable org.gnome.Calculator", "Calculator"),
    ("", None),
])
def test_the_program_a_menu_entry_runs_is_read_from_its_exec_line(exec_line, program):
    assert open_apps.program_of(exec_line) == program


def test_libreoffice_is_known_by_the_soffice_it_runs_as():
    """Debian's default text editor is LibreOffice Writer: its window is
    "soffice" in AT-SPI and its process soffice.bin, so the check failed while
    Writer had opened, and left it open for the checks after it."""
    names = (*open_apps.names_of("libreoffice"), "libreoffice-writer", "LibreOffice Writer")
    assert open_apps.window_of("soffice", names)
    ps = open_apps.parse_ps(
        "  700 oosplash         /usr/lib/libreoffice/program/oosplash --writer\n"
        "  701 soffice.bin      /usr/lib/libreoffice/program/soffice.bin --writer\n"
        "  702 bash             bash\n")
    assert open_apps._new_pids(ps, {"702": ()}, "libreoffice") == ["701"]
    assert open_apps._new_pids(ps, {}, "kwrite") == []


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


def test_the_window_checks_skipped_before_the_extension_run_again_with_it():
    again = {"ext_open_again_focuses", "ext_close_asks_to_save", "ext_close_this_window",
             "ext_switch_to", "ext_minimize_maximize", "ext_go_to_in_browser", "ext_web_search"}
    ubuntu = [c.id for c in run.selected(run.VMS["ubuntu"])]
    assert again <= set(ubuntu)
    assert ubuntu.index("ext_after_reboot") < min(ubuntu.index(i) for i in again)
    assert not again & {c.id for c in run.selected(run.VMS["kde"])}


@pytest.mark.parametrize("phrase, action", [
    ("minimize_window", "minimize"),
    ("maximize_window", "maximize"),
    ("restore_window", "unmaximize"),
])
def test_the_window_phrases_are_window_commands(phrase, action):
    from tuxtalk import command_phrases
    said = voices.load_phrases()[phrase]["say"]
    request = command_phrases.parse(said, navigation=True, actions=True)
    assert isinstance(request, command_phrases.WindowRequest)
    assert request.action == action


def test_the_go_back_phrase_is_the_command_and_the_sentence_is_typed():
    """The VM says "Go back a page." for the command, and "Go back." among
    the sentences that must be typed."""
    from tuxtalk import command_phrases
    phrases = voices.load_phrases()
    command = command_phrases.parse(phrases["go_back"]["say"], navigation=True, actions=True)
    assert command == command_phrases.KeyRequest("go back")
    sentences = {phrase for phrase, _expected, _exact in actions.SENTENCES}
    assert "sentence_go_back" in sentences
    assert command_phrases.parse(phrases["sentence_go_back"]["say"], navigation=True,
                                 actions=True) is None


class _KdeAfterMinimize:
    """A stand-in KDE Wayland VM after "minimize the window": the test
    window still says it's shown and focused, as it does there, and KWin's
    answer for it is *kwin*."""

    def __init__(self, kwin):
        self.vm = self
        self.spec = type("Spec", (), {"tags": ("kde",)})()
        self.home = "/home/ron"
        self.kwin = kwin

    def log_since(self, _mark):
        return "Voice command: minimize the focused window\n"

    def ui(self, *_args, **_options):
        return [{"app": "tt-sink", "title": "TT Sink", "states": ["visible", "active"]}]

    def session(self, *command, check=True):
        out = self.kwin if command[1].endswith("kwin_window.py") else "wayland\n"
        return type("Done", (), {"stdout": out, "returncode": 0})()

    def shot(self, name):
        return f"{name}.jpg"


@pytest.mark.parametrize("kwin, status", [
    ('[{"minimized": true, "active": false}]\n', "pass"),
    ('[{"minimized": false, "active": true}]\n', "fail"),
    ('{"error": "KWin didn\'t answer"}\n', "fail"),
])
def test_on_kde_wayland_kwin_says_whether_the_window_was_minimized(monkeypatch, kwin, status):
    """On KDE the window was gone from the screen while AT-SPI still called
    it focused, and the check failed TuxTalk for it."""
    monkeypatch.setattr(actions, "_until", lambda _seconds, test: test())
    outcome = actions._minimized(_KdeAfterMinimize(kwin), 0, ["restore", "maximize"])
    assert outcome.status == status


class _Editor:
    """A stand-in VM with a focused editor window that takes typing only
    into a document: on its start page (*start_page*) until Ctrl+N, or never
    (*takes_typing* False)."""

    def __init__(self, start_page=False, takes_typing=True):
        self.vm = self
        self.document = not start_page
        self.takes_typing = takes_typing
        self.typed = ""
        self.taps = []

    def ui(self, command, *_args, **_options):
        if command == "texts":
            return [self.typed] if self.typed else []
        title = "Untitled — KWrite" if self.document else "Welcome — KWrite"
        return [{"app": "kwrite", "title": title, "states": ["active"]}]

    def tap(self, *keys):
        self.taps.append(keys)
        if keys == ("ctrl", "n"):
            self.document = True
        elif self.document and self.takes_typing:
            self.typed += keys[0]

    def sink_text(self):
        return ""


def test_an_editor_on_its_start_page_gets_a_new_document_for_the_change(monkeypatch):
    """KWrite opened on its Welcome page, the line typed there went nowhere,
    and KWrite rightly closed without asking: the check failed TuxTalk."""
    monkeypatch.setattr(actions, "_until", lambda _seconds, test: test())
    monkeypatch.setattr(actions.time, "sleep", lambda _seconds: None)
    editor = _Editor(start_page=True)
    assert actions._unsaved_change(editor, "kwrite") is None
    assert ("ctrl", "n") in editor.taps
    assert editor.typed == actions.UNSAVED


def test_no_change_in_the_editor_is_said_before_any_close(monkeypatch):
    monkeypatch.setattr(actions, "_until", lambda _seconds, test: test())
    monkeypatch.setattr(actions.time, "sleep", lambda _seconds: None)
    problem = actions._unsaved_change(_Editor(takes_typing=False), "kwrite")
    assert problem.startswith("couldn't make an unsaved change in kwrite")


def _guest_ui():
    """guest/ui.py, which runs inside the VMs, loaded under a name of its own."""
    import importlib.util
    import pathlib
    pytest.importorskip("gi")
    path = pathlib.Path(__file__).parent / "vm" / "guest" / "ui.py"
    spec = importlib.util.spec_from_file_location("tt_guest_ui", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ValueError:          # no Atspi typelib here
        pytest.skip("AT-SPI isn't installed")
    return module


class _Button:
    """An AT-SPI button offering *actions*, recording the one done."""

    def __init__(self, *actions):
        self.actions, self.done = actions, None

    def get_action_iface(self):
        return self if self.actions else None

    def get_n_actions(self):
        return len(self.actions)

    def get_action_name(self, i):
        return self.actions[i]

    def do_action(self, i):
        self.done = self.actions[i]
        return True


@pytest.mark.parametrize("actions, done", [
    (("click",), "click"),                 # GTK
    (("Press", "SetFocus"), "Press"),      # Qt: KWrite's Discard was never pressed
    (("SetFocus",), None),
])
def test_a_button_is_pressed_by_its_toolkits_own_action_name(actions, done):
    button = _Button(*actions)
    assert _guest_ui().press(button) is (done is not None)
    assert button.done == done


class _SinkRun:
    """A stand-in for harness.Run: an empty test window, and the commands sent."""

    def __init__(self):
        self.vm = self
        self.sent = []

    def sink_text(self):
        return ""

    def ssh(self, command, check=True):
        self.sent.append(command)


def test_a_close_command_heard_as_other_words_is_misheard_not_a_fail():
    """Parakeet heard the computer voice's “Close the text editor.” as “Plose
    the text editor.” on KDE and openSUSE: KWrite got the words and the check
    failed as if TuxTalk had missed a command."""
    log = "Raw transcription (Parakeet): 'Plose the text editor.'\nText injected via paste"
    outcome = actions._not_a_command(_SinkRun(), "close_text_editor", log,
                                     "Plose the text editor.")
    assert outcome.status == "misheard"
    heard_right = actions._not_a_command(_SinkRun(), "close_text_editor", log,
                                         "Close the text editor.")
    assert heard_right.status == "fail"
    assert actions._not_a_command(_SinkRun(), "close_text_editor",
                                  "Voice command: close org.kde.kwrite.desktop", None) is None


def test_an_editor_that_was_never_asked_to_close_is_closed_and_its_question_answered(
        monkeypatch):
    """KWrite answers SIGTERM with a save question and stays open until it's
    answered; left open, the checks after it skipped. Its question names the
    document after the words typed into it, so it's taken whatever it names."""
    monkeypatch.setattr(actions, "_until", lambda _s, test: test())
    monkeypatch.setattr(actions, "_question_inside", lambda run, app, document=None: (
        "Close Document — KWrite" if run.sent and document is None else None))
    run = _SinkRun()
    opened = open_apps.Opened(log="", program="kwrite", app="kwrite", pids=["41", "42"])
    assert actions._asked_by_harness(run, opened) == "Close Document — KWrite"
    assert run.sent == ["kill 41 42"]
    nothing_started = open_apps.Opened(log="", program="kwrite", app="kwrite")
    assert actions._asked_by_harness(_SinkRun(), nothing_started) is None
