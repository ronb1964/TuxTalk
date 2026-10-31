"""An error while building the welcome dialog must not hang onboarding.

show_welcome_dialog runs a nested Gtk.main() that only the welcome dialog's
callback quits. An exception there would leave the loop running for good:
nothing on screen, onboarding never finishing, and the service never starting.
"""
import pytest

pytest.importorskip("gi")
from tuxtalk import welcome_dialog as wd  # noqa: E402


class _InstantSplash:
    def show_and_fade(self, callback):
        self.callback = callback


def test_a_broken_welcome_dialog_quits_the_loop(monkeypatch):
    splash = _InstantSplash()
    quits = []

    def broken(*a, **k):
        raise RuntimeError("missing icon")

    monkeypatch.setattr(wd, "SplashScreen", lambda: splash)
    monkeypatch.setattr(wd, "WelcomeDialog", broken)
    # Gtk.main() here runs the splash's callback, as the real loop would.
    monkeypatch.setattr(wd.Gtk, "main", lambda: splash.callback())
    monkeypatch.setattr(wd.Gtk, "main_quit", lambda: quits.append(True))

    assert wd.show_welcome_dialog() is None
    assert quits == [True]


def test_the_tray_starts_the_service_after_an_onboarding_error(tray_main):
    """The welcome comes back next launch, and TuxTalk runs meanwhile."""
    def broken():
        raise RuntimeError("missing icon")
    started = tray_main(first_run=True, welcome=broken)
    started.run_timers()
    assert "starts the service" in started
    assert "marks setup done" not in started


def test_closing_the_welcome_quits_and_says_how_to_finish(tray_main, monkeypatch):
    """No hotkey is listened for before setup is done: closed with the X or
    Escape, TuxTalk used to keep running with dead hotkeys and no word why,
    and opening it again showed its menu, not the welcome."""
    from tuxtalk import notify
    told = []
    monkeypatch.setattr(notify, "notify", lambda title, body: told.append((title, body)))
    started = tray_main(first_run=True, welcome=lambda: {"continue": False, "quit": False})
    started.run_timers()
    assert "quits" in started
    assert "starts the service" not in started and "marks setup done" not in started
    assert told == [("TuxTalk setup isn't finished",
                     "Open TuxTalk again to finish setting it up.")]
