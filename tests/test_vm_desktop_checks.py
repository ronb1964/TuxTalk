"""The VM checks for desktop voice commands read the desktop's own answers
(xprop, wpctl, pactl, the sound server's streams). A misread answer would
pass or fail a check for the wrong reason, so each reading is pinned here.
"""
import pytest
from conftest import import_vm_tool

pytest.importorskip("tomllib", reason="the VM harness needs Python 3.11 or newer")
desktop = import_vm_tool("checks_desktop_commands")
run = import_vm_tool("run")


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


def test_every_desktop_command_phrase_has_its_entry():
    """A check names its phrase by id; without an entry the run stops."""
    ids = set(desktop.voices.load_phrases())
    used = {c.id for c in run.CHECKS if c.section == desktop.SECTION}
    assert len(used) == 14
    assert {"snap_left", "snap_top_right", "centre_window", "show_desktop",
            "switch_desktop_two", "switch_desktop_one", "move_to_desktop_two",
            "move_to_desktop_one", "other_screen", "show_all_windows", "volume_down",
            "volume_up_two", "mute_sound", "unmute_sound", "pause_music", "play_music",
            "next_track", "lock_screen", "take_screenshot", "what_can_i_say",
            "minimize_text_editor", "restore_text_editor", "bare_pause", "bare_next",
            "bare_mute", "bare_copy", "sentence_lock_it", "sentence_show_me_desktop",
            "sentence_volume_loud"} <= ids
    assert {phrase for phrase, _said in desktop.SENTENCES} <= ids
