"""A hotkey helper older than the one this TuxTalk brings is offered for
update by itself, at every start until it's updated, not only in System
check, where nobody looks until the keys stop working."""
import types

import pytest

from tuxtalk import hotkey_helper, secure_input
from tuxtalk import update_offers as offers


@pytest.fixture
def helper(monkeypatch):
    """Helper 1.4 installed and running, dictation up: (what was asked,
    updates run, restarts, the answer to give)."""
    asked, updates, restarts, answer = [], [], [], {"update": True, "result": (True, "")}
    monkeypatch.setattr("tuxtalk.cuda_helper.is_first_run", lambda: False)
    monkeypatch.setattr("tuxtalk.service_launcher.find_service_pids", lambda: [4242])
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(secure_input, "is_installed", lambda: True)
    monkeypatch.setattr(secure_input, "running_version", lambda: "1.4")
    monkeypatch.setattr(offers, "_ask_helper_update",
                        lambda running: asked.append(running) or answer["update"])
    monkeypatch.setattr(secure_input, "install_with_pkexec",
                        lambda: updates.append(1) or answer["result"])


    class Thread:                                   # the update's thread, run at once
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(offers, "threading", types.SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(offers, "GLib", types.SimpleNamespace(idle_add=lambda fn, *args: fn(*args)))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: None)
    parakeet = []
    monkeypatch.setattr(offers, "maybe_offer_parakeet_update", parakeet.append)
    return asked, updates, restarts, answer, parakeet


def test_update_helper_updates_it_without_restarting_dictation(helper):
    """A restart would throw away a dictation under way; the service's
    helper connection comes back by itself and asks for its keys again."""
    asked, updates, restarts, answer, parakeet = helper
    assert offers._offer_in_turn(restarts.append) is False
    assert asked == ["1.4"] and updates == [1] and restarts == []


def test_not_now_asks_again_next_start(helper):
    asked, updates, restarts, answer, parakeet = helper
    answer["update"] = False
    offers._offer_in_turn(restarts.append)
    offers._offer_in_turn(restarts.append)          # the next start
    assert asked == ["1.4", "1.4"] and updates == [] and restarts == []


def test_a_cancelled_password_prompt_restarts_nothing(helper):
    asked, updates, restarts, answer, parakeet = helper
    answer["result"] = (False, "Setup cancelled.")
    offers._offer_in_turn(restarts.append)
    assert updates == [1] and restarts == []


def test_one_offer_a_start(helper):
    """With the helper asked about, Parakeet Ultra waits for the next start:
    two questions at once, and a second restart behind the first."""
    asked, updates, restarts, answer, parakeet = helper
    offers._offer_in_turn(restarts.append)
    assert parakeet == []


@pytest.mark.parametrize("running, installed, flatpak", [
    (hotkey_helper.VERSION, True, False),     # up to date
    (None, True, False),            # not running: System check says why
    ("1.4", False, False),          # the input group, no helper
    ("1.4", True, True),            # the Flatpak can't install one
])
def test_no_offer_without_an_older_helper_to_update(helper, monkeypatch, running, installed,
                                                    flatpak):
    asked, updates, restarts, answer, parakeet = helper
    monkeypatch.setattr(secure_input, "running_version", lambda: running)
    monkeypatch.setattr(secure_input, "is_installed", lambda: installed)
    if flatpak:
        monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    offers._offer_in_turn(restarts.append)
    assert asked == [] and parakeet == [restarts.append]


def test_the_offer_says_what_the_helper_does_and_what_doesn_t_work_yet():
    text = offers.helper_update_text("1.4")
    assert "reads your dictation keys" in text and "1.4" in text
    assert "Right Ctrl and Right Alt can't be dictation keys" in text
    assert "Right Ctrl" not in offers.helper_update_text("1.5")


def test_not_now_is_the_default_button(monkeypatch):
    """It comes up while people type: an Enter meant for their own window
    mustn't start a password prompt."""
    from tuxtalk import update_offers
    calls = []

    class Dialog:
        def __getattr__(self, name):
            return lambda *a, **k: calls.append((name, a))

        def run(self):
            return update_offers.Gtk.ResponseType.CANCEL
    monkeypatch.setattr("tuxtalk.ui_style.message_dialog", lambda **k: Dialog())
    assert update_offers._ask_helper_update("1.4") is False
    assert ("set_default_response", (update_offers.Gtk.ResponseType.CANCEL,)) in calls
    assert ("add_button", ("Update Helper", update_offers.Gtk.ResponseType.OK)) in calls


def test_the_update_runs_off_gtk_s_thread_and_answers_on_it(monkeypatch):
    """pkexec waits for the password for up to five minutes: on GTK's thread
    the tray (or Preferences) froze, and the answer must come back there."""
    import threading
    from tuxtalk import secure_input
    started, may_finish, queued, answers = threading.Event(), threading.Event(), [], []

    def install_with_pkexec():
        started.set()
        assert may_finish.wait(5)
        return True, ""
    monkeypatch.setattr(secure_input, "install_with_pkexec", install_with_pkexec)
    monkeypatch.setattr(offers, "GLib", types.SimpleNamespace(idle_add=queued.append))
    offers.update_helper(lambda ok, message: answers.append(ok))
    assert started.wait(5)                  # it returned while the update waits
    may_finish.set()
    for _ in range(500):
        if queued:
            break
        threading.Event().wait(0.01)
    assert answers == [] and len(queued) == 1      # handed to GTK's loop, not run there
    assert queued[0]() is False and answers == [True]
