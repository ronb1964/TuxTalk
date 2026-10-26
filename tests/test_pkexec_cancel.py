"""Cancelling the pkexec password prompt is a cancel, not a failure.

Some polkit agents report a cancelled prompt as dismissed (exit 126), others
as a refusal: exit 127 with "Not authorized ... This incident has been
reported." Fix Typing in Preferences showed that refusal as "Setup Failed".
"""
from types import SimpleNamespace

import pytest

from tuxtalk import uinput_helper

NOT_AUTHORIZED = ("Error executing command as another user: Not authorized\n\n"
                  "This incident has been reported.\n")


def _fake_run(returncode, stderr=""):
    def run(*args, **kwargs):
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)
    return run


@pytest.mark.parametrize("returncode, stderr", [
    (126, "Error executing command as another user: Request dismissed\n"),
    (127, NOT_AUTHORIZED),
])
def test_a_declined_prompt_reports_cancelled(monkeypatch, returncode, stderr):
    monkeypatch.setattr(uinput_helper.subprocess, "run", _fake_run(returncode, stderr))
    ok, message = uinput_helper.install_udev_rule_with_pkexec()
    assert not ok
    assert "cancelled" in message.lower()
    assert "incident" not in message


def test_a_real_failure_still_reports_the_error(monkeypatch):
    monkeypatch.setattr(uinput_helper.subprocess, "run",
                        _fake_run(1, "usermod: group 'input' does not exist"))
    ok, message = uinput_helper.install_udev_rule_with_pkexec()
    assert not ok
    assert "cancelled" not in message.lower()
    assert "usermod" in message


class _Recorder:
    def __init__(self):
        self.label, self.sensitive, self.markup = None, None, None

    def set_label(self, text):
        self.label = text

    def get_label(self):
        return self.label

    def set_sensitive(self, value):
        self.sensitive = value

    def set_markup(self, markup):
        self.markup = markup


def test_prefs_says_cancelled_instead_of_setup_failed(monkeypatch):
    pytest.importorskip("gi")
    from tuxtalk import prefs_system_check as P

    dialogs = []
    monkeypatch.setattr(P, "message_dialog", lambda **k: dialogs.append(k))
    monkeypatch.setattr("tuxtalk.ui_style.message_dialog", lambda **k: dialogs.append(k))
    monkeypatch.setattr(uinput_helper.subprocess, "run", _fake_run(127, NOT_AUTHORIZED))
    _, message = uinput_helper.install_udev_rule_with_pkexec()

    d, button = _system_check_offering(P.FIX_TYPING_LABEL)
    P.SystemCheck._finish_fix_typing(d, button, False, message)

    assert dialogs == [], "a cancel must not open the Setup Failed dialog"
    assert "cancelled" in d.typing_status_label.markup.lower()
    assert button.label == P.FIX_TYPING_LABEL and button.sensitive


def _system_check_offering(label):
    """System check, its button and what checking the state offers (*label*)."""
    button = _Recorder()
    d = SimpleNamespace(window=None, typing_status_label=_Recorder())
    d._check_typing_status = lambda: (button.set_label(label), button.set_sensitive(True))
    return d, button


def test_a_cancelled_switch_to_the_helper_offers_the_switch_again():
    """The button read Fix Typing Permissions after a cancelled password
    prompt, so the next click ran the plain fix, which leaves the user in
    the input group."""
    pytest.importorskip("gi")
    from tuxtalk import prefs_system_check as P
    d, button = _system_check_offering(P.SWITCH_TO_HELPER_LABEL)
    P.SystemCheck._finish_fix_typing(d, button, False, "Authentication cancelled", switching=True)
    assert button.label == P.SWITCH_TO_HELPER_LABEL and button.sensitive
    assert P.SWITCH_TO_HELPER_LABEL in d.typing_status_label.markup
