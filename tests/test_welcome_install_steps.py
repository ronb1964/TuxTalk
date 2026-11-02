"""The welcome screen's install buttons: no frozen window, honest results.

Package installs and the pkexec script can take up to five minutes, so they
run off the GTK thread. Fix Typing must not report "Device permissions
configured" and disable itself when the fix failed. Raw installer output is
escaped before it goes into Pango markup.
"""
import threading

import pytest
from gi.repository import Gdk, GLib, Gtk

from tuxtalk import secure_input
from tuxtalk import welcome_dialog as wd


@pytest.fixture
def dialog(welcome_screen):
    """Permissions needed: the typing section shows."""
    return welcome_screen(typing_ok=False)


def test_a_failed_fix_is_reported_and_can_be_retried(dialog, monkeypatch):
    dialogs = []
    monkeypatch.setattr("tuxtalk.ui_style.message_dialog", lambda **k: dialogs.append(k) or None)
    button = dialog.fix_typing_button
    outcome = {"results": ["❌ Permissions: udevadm not found"], "uinput_fixed": False,
               "needs_restart": False, "sees_no_keyboard": False}
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
def needs_uinput(welcome_screen, monkeypatch, message_boxes):
    """The typing step to do; its message boxes answer .boxes.answer (OK),
    and .responses keeps how the screen ended."""
    d = welcome_screen(typing_ok=False)
    d.responses = []
    monkeypatch.setattr(d.dialog, "response", d.responses.append)
    d.boxes = message_boxes
    message_boxes.answer = Gtk.ResponseType.OK
    return d


def _fix_outcome(ok):
    return {"results": [] if ok else ["❌ Permissions: cancelled"], "uinput_fixed": ok,
            "needs_restart": False, "sees_no_keyboard": False}


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
    d.boxes.answer = Gtk.ResponseType.CANCEL     # "Go back"
    monkeypatch.setattr(d, "_on_fix_typing_clicked",
                        lambda button: d._finish_fix_typing(button, _fix_outcome(False)))
    d.continue_button.clicked()
    assert d.responses == []
    assert any("won't work" in box.title + box.text for box in d.boxes.shown)


def test_a_cancelled_fix_can_still_continue(needs_uinput, monkeypatch):
    d = needs_uinput
    d.boxes.answer = Gtk.ResponseType.OK         # "Continue anyway"
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
def test_the_extension_is_offered_only_when_it_is_not_running(welcome_screen, monkeypatch,
                                                              active, offered):
    monkeypatch.setattr("tuxtalk.extension_helper.is_extension_enabled", lambda: active)
    d = welcome_screen(gnome=True)
    assert (d.extension_check is not None) is offered


def test_the_welcome_screen_opens_at_the_top_with_set_it_up_focused(dialog):
    """Ron, 2026-10-20: focus on "Change language, model or graphics card"
    scrolled the window down on open and cut its title off."""
    assert dialog.dialog.get_focus() is dialog.continue_button


# --- the PortAudio step ---------------------------------------------------------

@pytest.fixture
def needs_portaudio(welcome_screen, monkeypatch):
    monkeypatch.setattr(wd, "show_message", lambda *a, **k: None)
    return welcome_screen(portaudio=False)


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
    nothing to explain it, and TuxTalk would quit under it. Escape (the
    dialog's "close") and the title bar's X go nowhere while a job runs;
    once it's done, a close ends the dialog as before."""
    d = needs_portaudio
    seen = []

    def delete():
        event = Gdk.Event.new(Gdk.EventType.DELETE)
        event.window = d.dialog.get_window()
        d.dialog.event(event)

    def after_both():
        seen.append(d.dialog.get_visible())
        d._busy = 0
        d.dialog.emit("close")
    d._busy = 1
    GLib.timeout_add(100, lambda: d.dialog.emit("close") and False)
    GLib.timeout_add(200, lambda: delete() and False)
    GLib.timeout_add(500, lambda: after_both() and False)
    GLib.timeout_add(5000, lambda: d.dialog.response(Gtk.ResponseType.OK) and False)  # no hang
    assert d.dialog.run() == Gtk.ResponseType.DELETE_EVENT
    assert seen == [True]


def test_set_it_up_fixes_typing_then_says_portaudio_is_missing(welcome_screen, monkeypatch):
    """A computer missing both: after the typing fix "Set it up" started,
    the PortAudio warning still comes, and Go Back stays on the screen."""
    d = welcome_screen(typing_ok=False, portaudio=False)
    monkeypatch.setattr(d, "_on_fix_typing_clicked",
                        lambda button: d._finish_fix_typing(button, _fix_outcome(True)))
    warned, responses = _set_it_up(d, monkeypatch, Gtk.ResponseType.CANCEL)
    assert "Recording isn't set up yet" in warned and responses == []


# --- a hotkey helper that reads no keyboard (issue #26) --------------------------

@pytest.fixture
def blind_helper(monkeypatch):
    """A hotkey helper installed and answering that keeps saying it reads no
    keyboard, with typing (/dev/uinput) working."""
    from tuxtalk import uinput_helper
    monkeypatch.setattr(secure_input, "is_installed", lambda: True)
    monkeypatch.setattr(secure_input, "keyboards_watched", lambda: 0)
    monkeypatch.setattr(uinput_helper, "check_uinput_permission", lambda: (True, ""))


def test_a_helper_that_reads_no_keyboard_is_said_not_typing_setup(welcome_screen,
                                                                   blind_helper):
    """The access check fails then, and the screen asked for Typing Setup:
    a password prompt for the same helper, then "Ready!" with the keys
    still dead. Now it says what's wrong, and asks for nothing."""
    from gtk_text import tree_text
    d = welcome_screen(typing_ok=False)
    shown = " ".join(tree_text(d.dialog))
    assert "can't see your keyboard" in shown and "Typing Setup Required" not in shown
    assert not d.needs_uinput_fix and d.fix_typing_button is None


def test_fix_typing_that_leaves_the_helper_reading_no_keyboard_isnt_called_ready(
        dialog, blind_helper, monkeypatch, message_boxes):
    """Typing needed the fix (no /dev/uinput), and the helper it installed
    still reads no keyboard: no "Ready!" and no "Typing Setup Complete"."""
    monkeypatch.setattr(secure_input, "fix_permissions", lambda: (True, "", False))
    outcome = wd._fix_typing_steps()
    assert outcome["uinput_fixed"] and outcome["sees_no_keyboard"]
    dialog._finish_fix_typing(dialog.fix_typing_button, outcome)
    status = dialog.typing_status_label.get_text()
    assert "can't see your keyboard" in status and "Ready" not in status
    assert message_boxes.shown == []
