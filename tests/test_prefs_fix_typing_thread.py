"""Fix Typing Permissions must not freeze Preferences while pkexec waits.

pkexec can wait up to a minute, so it runs off the GTK thread; otherwise the
window freezes while the password prompt is open and its "Setting up..."
label is never drawn. A failure resets the button to the same label the
status check uses.
"""
import threading
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")

from tuxtalk import prefs_system_check as P  # noqa: E402
from tuxtalk import uinput_helper  # noqa: E402
from tuxtalk.prefs_system_check import SystemCheck  # noqa: E402


class FakeButton:
    def __init__(self):
        self.label, self.sensitive = None, True

    def set_label(self, text):
        self.label = text

    def get_label(self):
        return self.label

    def set_sensitive(self, value):
        self.sensitive = value


class FakeDialog:
    def __init__(self, answer):
        self.answer = answer

    def format_secondary_text(self, text):
        pass

    def run(self):
        return self.answer

    def destroy(self):
        pass


def test_pkexec_runs_off_the_gtk_thread_and_a_failure_resets_the_button(monkeypatch):
    threads, idle = [], []
    # Only the System check's threads: the sound thread started at the
    # test's end must stay a real one.
    monkeypatch.setattr(P, "threading", SimpleNamespace(
        Thread=lambda target, daemon=None: threads.append(target) or
        SimpleNamespace(start=lambda: None)))
    monkeypatch.setattr(P.GLib, "idle_add", lambda fn, *a: idle.append((fn, a)))
    monkeypatch.setattr(P, "message_dialog", lambda **k: FakeDialog(P.Gtk.ResponseType.YES))
    monkeypatch.setattr("tuxtalk.ui_style.message_dialog",
                        lambda **k: FakeDialog(P.Gtk.ResponseType.YES))
    ran_in = []
    from tuxtalk import secure_input
    # The "input" group route; the hotkey helper's goes the same way.
    monkeypatch.setattr(secure_input, "can_install", lambda: (False, "test"))
    monkeypatch.setattr(uinput_helper, "install_udev_rule_with_pkexec",
                        lambda *a: ran_in.append(threading.current_thread()) or
                        (False, "Authentication was cancelled"))

    d = SimpleNamespace(window=None, typing_status_label=SimpleNamespace(set_markup=lambda m: None))
    d._finish_fix_typing = lambda *a: SystemCheck._finish_fix_typing(d, *a)
    button = FakeButton()
    # Checked again, the state offers the fix again.
    d._check_typing_status = lambda: (button.set_label(P.FIX_TYPING_LABEL),
                                      button.set_sensitive(True))

    SystemCheck._on_fix_typing_clicked(d, button)
    assert ran_in == [], "pkexec ran inside the click handler"
    assert button.label == "Setting up..." and not button.sensitive

    threads[0]()                      # the worker thread
    fn, args = idle[0]
    fn(*args)                         # back on the GTK thread

    assert button.label == P.FIX_TYPING_LABEL and button.sensitive


def test_update_hotkey_helper_leaves_dictation_running(monkeypatch):
    """It restarted dictation afterwards, which threw away a dictation under
    way; the service's helper connection comes back by itself."""
    from tuxtalk import update_offers
    titles, restarts, finish = [], [], []
    monkeypatch.setattr(P, "show_message", lambda win, kind, title, text, **k:
                        titles.append(title) or P.Gtk.ResponseType.YES)
    monkeypatch.setattr(update_offers, "update_helper", finish.append)
    d = SimpleNamespace(window=None, win=SimpleNamespace(restart_service=lambda: restarts.append(1)),
                        typing_status_label=SimpleNamespace(set_markup=lambda m: None))
    d._finish_update = lambda *a: SystemCheck._finish_update(d, *a)
    d._finish_fix_typing = lambda *a, **k: SystemCheck._finish_fix_typing(d, *a, **k)
    d._check_typing_status = lambda: None
    button = FakeButton()
    button.set_label(P.UPDATE_HELPER_LABEL)
    SystemCheck._on_fix_typing_clicked(d, button)
    assert button.label == "Setting up..."
    finish[0](True, "")                             # the update is over
    assert restarts == [] and titles[-1] == "Hotkey helper updated"
