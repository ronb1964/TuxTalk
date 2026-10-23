"""The Open Preferences button belongs on the FINAL screen, not mid-onboarding.

Onboarding order (show_welcome_and_install):

    welcome (chooses the setup) → optional downloads → hotkey test
        → the model actually downloads
        → show_setup_complete_dialog      ("Ready!", the last thing shown)

Before the model is downloaded, sending the user into Preferences would
interrupt setup halfway. The last screen is where they are done and genuinely
choosing what to do next — start dictating, or go and adjust settings.
"""

import pytest


def test_final_screen_offers_preferences(setup_complete, monkeypatch):
    """The one moment the user is finished and choosing what to do next. It
    starts Preferences the way the tray does, in every install."""
    from tuxtalk import service_launcher
    opened = []
    monkeypatch.setattr(service_launcher, "launch_preferences", lambda *a: opened.append(True))
    setup_complete(click="Open Preferences")
    assert opened == [True]


def test_nothing_says_ready_before_the_model_is_downloaded(first_run):
    """"You're All Set!" came before the download, so the first dictation
    couldn't work yet (Codex, 0.16 simplicity review). Setup Complete, after
    the download, is the one finish screen."""
    steps = [step for step in first_run() if isinstance(step, str)]
    assert steps == ["welcome", "tests the hotkeys", "downloads the speech model",
                     "setup complete"]


@pytest.mark.parametrize("needs_restart, label", [(False, "Start Using TuxTalk!"),
                                                  (True, "Restart computer now")])
def test_setup_complete_opens_at_the_top_with_its_main_button_focused(
        monkeypatch, needs_restart, label):
    """Pop!_OS VM, 2026-10-21: the focus went to the Discussions link inside
    the scrolling part, and the window opened scrolled down with "Setup
    Complete!" cut off."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from tuxtalk import welcome_dialog

    focused = []
    # Built in full, but never shown on the real screen.
    monkeypatch.setattr(Gtk.Dialog, "show_all", lambda self: None)
    monkeypatch.setattr(Gtk.Dialog, "run",
                        lambda self: focused.append(self.get_focus()) or Gtk.ResponseType.OK)
    welcome_dialog.show_setup_complete_dialog(needs_restart=needs_restart)
    assert [w.get_label() for w in focused] == [label]
