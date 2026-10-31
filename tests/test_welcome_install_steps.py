"""The welcome screen's install buttons: no frozen window, honest results.

Package installs and the pkexec script can take up to five minutes, so they
run off the GTK thread. Fix Typing must not report "Device permissions
configured" and disable itself when the fix failed. Raw installer output is
escaped before it goes into Pango markup.
"""
import threading

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from tuxtalk import recommend as r  # noqa: E402
from tuxtalk import secure_input  # noqa: E402
from tuxtalk import welcome_dialog as wd  # noqa: E402


@pytest.fixture
def dialog(monkeypatch):
    """Permissions needed: the typing section shows."""
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    monkeypatch.setattr(r, "detect_hardware", lambda cfg=None: r.Hardware(None, False))
    monkeypatch.setattr(r, "system_language", lambda env=None: "en")
    monkeypatch.setattr(wd, "detect_uinput_access", lambda: (False, "not in input group"))
    monkeypatch.setattr(wd, "portaudio_installed", lambda: True)
    d = wd.WelcomeDialog(force_gnome=False)
    yield d
    d.destroy()


def test_a_failed_fix_is_reported_and_can_be_retried(dialog, monkeypatch):
    dialogs = []
    monkeypatch.setattr("tuxtalk.ui_style.message_dialog", lambda **k: dialogs.append(k) or None)
    button = dialog.fix_typing_button
    outcome = {"results": ["❌ Permissions: udevadm not found"], "uinput_fixed": False,
               "needs_restart": False}
    dialog._finish_fix_typing(button, outcome)
    assert button.get_sensitive()
    assert "Fix Typing" in button.get_label()
    assert "udevadm not found" in dialog.typing_status_label.get_text()
    assert "permissions configured" not in dialog.typing_status_label.get_text().lower()
    assert dialogs == []          # no "Permissions Configured" box


def test_a_cancelled_fix_says_so(monkeypatch):
    monkeypatch.setattr(secure_input, "fix_permissions",
                        lambda: (False, "Authentication was cancelled.", True))
    result = wd._fix_typing_steps()
    assert not result["uinput_fixed"] and not result["needs_restart"]
    assert "cancelled" in result["results"][0]


def test_status_text_is_escaped_and_short():
    text = "Installation failed:\n<stdin>: a & b\n" + "\n".join(f"line {i}" for i in range(30))
    markup = wd._status_markup(text, "#f44336")
    assert "&lt;stdin&gt;" in markup and "&amp;" in markup
    assert "line 29" not in markup and "…" in markup
    assert Gtk.Label().set_markup(markup) is None  # valid markup


def test_installs_run_off_the_gtk_thread(dialog, monkeypatch):
    queued = []
    monkeypatch.setattr(wd.GLib, "idle_add", lambda fn, *a: queued.append((fn, a)))
    started = threading.Event()
    release = threading.Event()
    worker = {}

    def work():
        worker["thread"] = threading.current_thread()
        started.set()
        release.wait(2)
        return "done"

    finished = []
    dialog._run_in_background(work, finished.append)
    assert started.wait(2)
    assert worker["thread"] is not threading.main_thread()
    assert not dialog.continue_button.get_sensitive()   # "Set it up" waits
    release.set()
    worker["thread"].join(2)
    fn, args = queued.pop()
    fn(*args)
    assert finished == ["done"]
    assert dialog.continue_button.get_sensitive()


def test_nothing_is_updated_after_the_window_closed(dialog, monkeypatch):
    queued = []
    monkeypatch.setattr(wd.GLib, "idle_add", lambda fn, *a: queued.append((fn, a)))
    finished = []
    done = threading.Event()
    dialog._run_in_background(lambda: done.set() or 1, finished.append)
    assert done.wait(2)
    for _ in range(100):
        if queued:
            break
        threading.Event().wait(0.01)
    dialog._closed = True
    fn, args = queued.pop()
    fn(*args)
    assert finished == []


def test_installs_run_one_at_a_time(dialog, monkeypatch):
    """Started together, Install PortAudio and Fix Typing would put two
    password prompts up at once."""
    monkeypatch.setattr(wd.GLib, "idle_add", lambda fn, *a: None)
    first_running = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def first():
        first_running.set()
        release_first.wait(2)

    dialog._run_in_background(first, lambda outcome: None)
    assert first_running.wait(2)
    dialog._run_in_background(second_started.set, lambda outcome: None)
    assert not second_started.wait(0.2)      # waits for the first install
    release_first.set()
    assert second_started.wait(2)


# --- "Set it up" runs the typing fix it needs (fedora44-KDE test, 2026-10-13) ---
# The Fix Typing button sat below the window's edge at 1080p; "Set it up"
# finished onboarding without it, and the hotkey and typing never worked.

@pytest.fixture
def needs_uinput(monkeypatch):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    monkeypatch.setattr(r, "detect_hardware", lambda cfg=None: r.Hardware(None, False))
    monkeypatch.setattr(r, "system_language", lambda env=None: "en")
    monkeypatch.setattr(wd, "detect_uinput_access", lambda: (False, "not in input group"))
    monkeypatch.setattr(wd, "portaudio_installed", lambda: True)
    d = wd.WelcomeDialog(force_gnome=False)
    responses = []
    monkeypatch.setattr(d.dialog, "response", lambda r: responses.append(r))
    monkeypatch.setattr("tuxtalk.ui_style.message_dialog", lambda **k: _Answer(k, d.answer))
    d.answer = Gtk.ResponseType.OK
    d.responses = responses
    yield d
    d.destroy()


class _Answer:
    """A message box that answers by itself, recording what it said."""
    shown = []

    def __init__(self, kwargs, answer):
        self.kwargs, self.answer = kwargs, answer
        _Answer.shown.append(kwargs.get("text", ""))

    def format_secondary_text(self, text):
        _Answer.shown.append(text)

    def add_button(self, *a):
        pass

    def run(self):
        return self.answer

    def destroy(self):
        pass


def _fix_outcome(ok):
    return {"results": [] if ok else ["❌ Permissions: cancelled"], "uinput_fixed": ok,
            "needs_restart": False}


def test_set_it_up_runs_the_typing_fix_first(needs_uinput, monkeypatch):
    d = needs_uinput
    ran = []
    monkeypatch.setattr(d, "_on_fix_typing_clicked",
                        lambda button: ran.append(1) or d._finish_fix_typing(button, _fix_outcome(True)))
    d.continue_button.clicked()
    assert ran == [1]
    assert d.responses == [Gtk.ResponseType.OK]


def test_a_cancelled_fix_warns_and_can_go_back(needs_uinput, monkeypatch):
    d = needs_uinput
    d.answer = Gtk.ResponseType.CANCEL           # "Go back"
    monkeypatch.setattr(d, "_on_fix_typing_clicked",
                        lambda button: d._finish_fix_typing(button, _fix_outcome(False)))
    _Answer.shown.clear()
    d.continue_button.clicked()
    assert d.responses == []
    assert any("won't work" in t for t in _Answer.shown)


def test_a_cancelled_fix_can_still_continue(needs_uinput, monkeypatch):
    d = needs_uinput
    d.answer = Gtk.ResponseType.OK               # "Continue anyway"
    monkeypatch.setattr(d, "_on_fix_typing_clicked",
                        lambda button: d._finish_fix_typing(button, _fix_outcome(False)))
    d.continue_button.clicked()
    assert d.responses == [Gtk.ResponseType.OK]


def test_set_it_up_goes_straight_on_when_nothing_is_needed(dialog, monkeypatch):
    responses = []
    monkeypatch.setattr(dialog.dialog, "response", lambda r: responses.append(r))
    dialog.uinput_fixed = True                   # the fixture's only pending step, done
    dialog.continue_button.clicked()
    assert responses == [Gtk.ResponseType.OK]


# --- the extension offer (Ubuntu VM test, 2026-10-13) --------------------------

@pytest.mark.parametrize("active, offered", [(True, False), (False, True)])
def test_the_extension_is_offered_only_when_it_is_not_running(monkeypatch, active, offered):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    monkeypatch.setattr(r, "detect_hardware", lambda cfg=None: r.Hardware(None, False))
    monkeypatch.setattr(r, "system_language", lambda env=None: "en")
    monkeypatch.setattr(wd, "detect_uinput_access", lambda: (True, ""))
    monkeypatch.setattr(wd, "portaudio_installed", lambda: True)
    monkeypatch.setattr("tuxtalk.extension_helper.is_extension_enabled", lambda: active)
    d = wd.WelcomeDialog(force_gnome=True)
    try:
        assert (d.extension_check is not None) is offered
    finally:
        d.destroy()


def test_the_welcome_screen_opens_at_the_top_with_set_it_up_focused(dialog):
    """Ron, 2026-10-20: focus on "Change language, model or graphics card"
    scrolled the window down on open and cut its title off."""
    assert dialog.dialog.get_focus() is dialog.continue_button


# --- the PortAudio step ---------------------------------------------------------

@pytest.fixture
def needs_portaudio(monkeypatch):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    monkeypatch.setattr(r, "detect_hardware", lambda cfg=None: r.Hardware(None, False))
    monkeypatch.setattr(r, "system_language", lambda env=None: "en")
    monkeypatch.setattr(wd, "detect_uinput_access", lambda: (True, ""))
    monkeypatch.setattr(wd, "portaudio_installed", lambda: False)
    monkeypatch.setattr(wd, "show_message", lambda *a, **k: None)
    d = wd.WelcomeDialog(force_gnome=False)
    yield d
    d.destroy()


def test_without_portaudio_the_step_says_what_its_button_does(needs_portaudio):
    from gtk_text import tree_text
    shown = tree_text(needs_portaudio.dialog)
    assert "⚠️ Audio Library Required" in shown
    assert "❌ PortAudio library not found" in shown
    assert 'What "Install PortAudio" does:' in shown
    assert "🎤 Enables microphone recording for speech recognition" in shown
    assert needs_portaudio.install_portaudio_button.get_label() == \
        "📦 Install PortAudio (Recommended)"


def test_an_installed_portaudio_updates_the_step(needs_portaudio):
    d = needs_portaudio
    d._finish_install_portaudio(d.install_portaudio_button, (True, "installed"))
    assert d.portaudio_initial_status.get_text() == "✅ PortAudio library installed"
    assert "PortAudio installed!" in d.portaudio_status_label.get_text()
    assert d.install_portaudio_button.get_label() == "✅ Installed!"
    assert not d.needs_portaudio_install       # "Set it up" doesn't warn any more


def _set_it_up(d, monkeypatch, answer):
    """Press "Set it up"; *answer* is the warning's. Returns (warnings, responses)."""
    warned, responses = [], []
    monkeypatch.setattr(wd, "show_message",
                        lambda parent, kind, title, *a, **k: warned.append(title) or answer)
    monkeypatch.setattr(d.dialog, "response", responses.append)
    d._on_set_it_up(None)
    return warned, responses


def test_set_it_up_without_portaudio_says_recording_wont_work(needs_portaudio, monkeypatch):
    """As it does for typing: before, setup went on with no microphone and no word."""
    warned, responses = _set_it_up(needs_portaudio, monkeypatch, Gtk.ResponseType.CANCEL)
    assert warned == ["Recording isn't set up yet"] and responses == []      # Go Back
    warned, responses = _set_it_up(needs_portaudio, monkeypatch, Gtk.ResponseType.OK)
    assert responses == [Gtk.ResponseType.OK]                                  # Continue Anyway


def test_set_it_up_after_installing_portaudio_goes_straight_on(needs_portaudio, monkeypatch):
    d = needs_portaudio
    d._finish_install_portaudio(d.install_portaudio_button, (True, "installed"))
    warned, responses = _set_it_up(d, monkeypatch, Gtk.ResponseType.CANCEL)
    assert warned == [] and responses == [Gtk.ResponseType.OK]


def test_the_welcome_cant_be_closed_while_an_install_runs(needs_portaudio):
    """Closed mid-install, the password prompt would still come up with
    nothing to explain it, and the result would be lost."""
    d = needs_portaudio
    event = Gdk.Event.new(Gdk.EventType.DELETE)
    d._busy = 1
    assert d.dialog.emit("delete-event", event) is True       # stays open
    d._busy = 0
    assert d.dialog.emit("delete-event", event) is False
