"""The VM checks for desktop voice commands read the desktop's own answers
(xprop, wpctl, pactl, the sound server's streams). A misread answer would
pass or fail a check for the wrong reason, so each reading is pinned here.
"""
from collections import namedtuple

import pytest
from conftest import import_vm_tool

from tuxtalk import command_phrases

pytest.importorskip("tomllib", reason="the VM harness needs Python 3.11 or newer")
desktop = import_vm_tool("checks_desktop_commands")
run = import_vm_tool("run")
voices = import_vm_tool("voices")


def test_a_window_is_found_on_its_desktop_by_its_name():
    """xprop numbers desktops from 0; the checks say desktop two for index 1."""
    text = ('_NET_WM_NAME(UTF8_STRING) = "Files"\n_NET_WM_DESKTOP(CARDINAL) = 0\n'
            '_NET_WM_NAME(UTF8_STRING) = "TT Sink"\n_NET_WM_DESKTOP(CARDINAL) = 1\n')
    assert desktop.parse_x11_desktops(text) == {"Files": 1, "TT Sink": 2}


def test_a_window_on_every_desktop_has_no_desktop_of_its_own():
    text = ('_NET_WM_NAME(UTF8_STRING) = "TT Sink"\n'
            '_NET_WM_DESKTOP(CARDINAL) = 4294967295\n')
    assert desktop.parse_x11_desktops(text) == {}


@pytest.mark.parametrize("text, expected", [
    ("Volume: 0.45\n", (45, False)),
    ("Volume: 0.55 [MUTED]\n", (55, True)),
    ("nothing\n", None),
])
def test_wpctl_volume_is_read_as_percent_and_mute(text, expected):
    assert desktop.parse_output_volume(text) == expected


def test_pactl_volume_and_mute_are_read_together():
    volume = "Volume: front-left: 29491 /  45% / -20.82 dB,   front-right: 29491 /  45%\n"
    assert desktop.parse_output_volume(volume, "Mute: no\n") == (45, False)
    assert desktop.parse_output_volume(volume, "Mute: yes\n") == (45, True)


def test_a_window_is_half_the_screen_only_within_its_range():
    assert desktop.within(960, 1920, 0.4, 0.6)
    assert not desktop.within(1920, 1920, 0.4, 0.6)


SCREEN = namedtuple("Spec", "width height")(1280, 800)


@pytest.mark.parametrize("box, fits", [
    (desktop.Box(0, 0, 640, 800), True),
    (desktop.Box(640, 0, 640, 800), False),           # the right half isn't "snap left"
    (desktop.Box(None, None, 640, 800), True),        # where the place can't be read
])
def test_snap_left_is_judged_by_its_place_too(box, fits):
    assert desktop.placed_at(box, SCREEN, ((-0.02, 0.1), (-0.02, 0.12))) is fits


@pytest.mark.parametrize("answer, desktop_name, open_", [
    ("(<['blur', 'overview']>,)\n", "kde", True),
    ("(<['blur']>,)\n", "kde", False),
    ("(<true>,)\n", "gnome", True),
    ("(<false>,)\n", "gnome", False),
    ("Error: GDBus.Error:org.freedesktop.DBus.Error.AccessDenied\n", "gnome", None),
])
def test_the_overview_is_judged_by_the_desktops_answer(answer, desktop_name, open_):
    assert desktop.overview_open(answer, desktop_name) is open_


SAVED = "Voice command: take a screenshot (saved, with the portal)"


@pytest.mark.parametrize("files, sizes, log, problem", [
    (["a.png"], ["120"], SAVED, None),
    (["a.png", "b.png"], ["120", "120"], SAVED, "2 pictures"),
    ([], [], SAVED, "no new picture"),
    (["a.png"], ["120"], "Voice command not done: screenshot_unconfirmed", "no new picture"),
    (["a.png"], ["0"], SAVED, "empty"),
])
def test_a_screenshot_passes_only_as_one_saved_picture(files, sizes, log, problem):
    found = desktop.screenshot_problem(files, sizes, log)
    assert (found is None) if problem is None else (problem in found)


@pytest.mark.parametrize("log, settled", [
    ("Voice command: take a screenshot (saved, with spectacle)", True),
    ("Voice command: take a screenshot of the window (saved, with gnome-screenshot)", True),
    ("Voice command: take a screenshot of the window (unconfirmed)", False),
    ("Voice command not done: no_screenshot_tool", True),
    ("Text injected", True),
    ("Voice command: take a screenshot (unconfirmed)\n"
     "Voice command not done: screenshot_unconfirmed", False),
    ("", False),
])
def test_an_unconfirmed_screenshot_is_waited_for(log, settled):
    """The portal's request stays open: a picture can still land later."""
    assert desktop.screenshot_settled(log) is settled


@pytest.mark.parametrize("answer, absent", [
    ("('done',)\n", False),
    ("Error: GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: no such method\n", True),
    ("Error: GDBus.Error:org.freedesktop.DBus.Error.NoReply: Timeout was reached\n", False),
    ("Error: GDBus.Error:org.gnome.gjs.JSError.TypeError: x is null\n", False),
])
def test_only_a_missing_extension_lets_the_workspace_reset_go_unchecked(answer, absent):
    assert desktop.extension_absent(answer) is absent


def test_every_desktop_command_phrase_has_its_entry():
    """A check names its phrase by id; without an entry the run stops."""
    ids = set(voices.load_phrases())
    used = {c.id for c in run.CHECKS if c.section == desktop.SECTION}
    assert used
    assert set(COMMANDS) | {"minimize_text_editor", "restore_text_editor"} <= ids
    assert {phrase for phrase, _said in desktop.SENTENCES} <= ids


# The phrases the checks say as desktop commands, each worded so the
# computer voice is heard right ("Snap left." came out "Snap lift.").
COMMANDS = ("snap_left", "snap_top_right", "centre_window", "show_desktop",
            "go_to_desktop_two", "go_to_desktop_one", "move_to_desktop_two",
            "move_to_desktop_one", "other_screen", "show_all_windows", "volume_down",
            "volume_up_three", "mute_sound", "unmute_sound", "pause_music", "play_music",
            "next_track", "lock_screen", "take_screenshot", "take_window_screenshot",
            "what_can_i_say")


@pytest.mark.parametrize("phrase", COMMANDS)
def test_each_command_the_checks_say_is_one_tuxtalk_takes(phrase):
    """A rewording for the computer voice must still be a command, or the
    check would fail on TuxTalk typing it."""
    said = voices.load_phrases()[phrase]["say"]
    assert command_phrases.parse(said, navigation=False, actions=True) is not None


@pytest.mark.parametrize("phrase", [phrase for phrase, _said in desktop.SENTENCES])
def test_each_sentence_the_checks_say_is_typed_as_words(phrase):
    said = voices.load_phrases()[phrase]["say"]
    assert command_phrases.parse(said, navigation=False, actions=True) is None


class _LockedVM:
    """A VM whose lock screen goes down only at the *opens_at*-th unlock."""

    def __init__(self, opens_at, tags=("gnome",)):
        self.vm = self
        self.spec = namedtuple("Spec", "tags")(tags)
        self.opens_at, self.unlocks, self.session_calls = opens_at, 0, []

    def ssh(self, command, check=True):
        if command.startswith("loginctl unlock-session"):
            self.unlocks += 1
        return type("Done", (), {"stdout": "", "returncode": 0})()

    def session(self, *command, check=True):
        self.session_calls.append(command)
        return type("Done", (), {"stdout": ""})()

    def click_centre(self):
        pass

    def unlocked(self):
        return self.unlocks >= self.opens_at


@pytest.fixture
def lock_screen_vm(monkeypatch):
    """_unlock with no waiting, the lock screen as the fake VM has it."""
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)
    monkeypatch.setattr(desktop, "_until", lambda _s, test: test())
    monkeypatch.setattr(desktop, "_sink", lambda _run: None)
    monkeypatch.setattr(desktop, "_sink_state", lambda _sink: None)

    def make(opens_at, tags=("gnome",)):
        vm = _LockedVM(opens_at, tags)
        monkeypatch.setattr(desktop, "_locked", lambda run, _s: "no" if run.unlocked() else "yes")
        monkeypatch.setattr(desktop, "_screen_locked", lambda run, _s: not run.unlocked())
        return vm
    return make


def test_a_lock_screen_that_stays_up_once_is_asked_again_before_the_run_stops(lock_screen_vm):
    """One ignored unlock on Zorin stopped the run and skipped 70 checks."""
    vm = lock_screen_vm(opens_at=2)
    assert desktop._unlock(vm, "3") is True
    assert vm.unlocks == 2
    assert desktop.GNOME_UNLOCK in vm.session_calls


def test_a_lock_screen_that_never_goes_down_is_reported(lock_screen_vm):
    vm = lock_screen_vm(opens_at=99, tags=("kde",))
    assert desktop._unlock(vm, "3") is False
    assert vm.unlocks == desktop.UNLOCK_TRIES
    assert vm.session_calls == []
