"""Who else uses Ctrl+Space is found, and the service says so in its log."""
import logging

import pytest
from evdev import ecodes

from tuxtalk import ctrl_space_clashes as csc
from tuxtalk import desktop_detect, hotkeys
from tuxtalk.hotkey_keys import Hotkeys


@pytest.fixture
def desktop(monkeypatch):
    """Set what is running, what gsettings answers (by schema) and the Fcitx 5
    config, instead of the real computer's."""
    def set_up(running=(), gsettings=None, fcitx5=None, gnome=False, xkb=None):
        answers = gsettings or {}
        monkeypatch.setattr(csc, "_running", lambda name: name in running)
        monkeypatch.setattr(csc, "_gsettings", lambda *args: answers.get(args[1], ""))
        monkeypatch.setattr(csc, "_fcitx5_config", lambda: fcitx5)
        monkeypatch.setattr(csc, "_xkb_options", lambda: xkb)
        monkeypatch.setattr(desktop_detect, "get_desktop_environment",
                            lambda: "gnome" if gnome else "kde")
    return set_up


def test_ibus_with_ctrl_space_among_its_triggers(desktop):
    desktop(running=["ibus-daemon"], gsettings={
        "org.freedesktop.ibus.general.hotkey": "['<Super>space', '<Control>space']"})
    assert csc.clashes() == [csc.IBUS]


@pytest.mark.parametrize("value,expected", [
    ("['<Control>space']", True), ("['<Primary>space']", True), ("@as []", False),
    ("['<Control><Shift>space']", False), ("['<Super>space']", False)])
def test_which_ibus_trigger_lists_mean_ctrl_space(value, expected):
    assert csc.ibus_triggers_ctrl_space(value) is expected


def test_fcitx5_without_a_config_uses_its_default(tmp_path, monkeypatch):
    monkeypatch.setattr(csc, "_running", lambda name: name == "fcitx5")
    monkeypatch.setattr(csc, "fcitx5_config_path", lambda: str(tmp_path / "missing"))
    monkeypatch.setattr(csc, "_xkb_options", lambda: None)
    monkeypatch.setattr(desktop_detect, "get_desktop_environment", lambda: "kde")
    assert [c.who for c in csc.clashes()] == ["Fcitx 5"]


def test_fcitx5_with_other_trigger_keys_is_no_clash(desktop):
    desktop(running=["fcitx5"], fcitx5="[Hotkey/TriggerKeys]\n0=Super+space\n1=Zenkaku_Hankaku\n")
    assert csc.clashes() == []


def test_fcitx5_with_ctrl_space_listed_is_a_clash(desktop):
    desktop(running=["fcitx5"], fcitx5="[Hotkey]\nX=1\n\n[Hotkey/TriggerKeys]\n0=Control+space\n")
    assert csc.clashes() == [csc.FCITX5]


def test_fcitx5_config_without_a_trigger_section_uses_its_default():
    assert csc.fcitx5_triggers_ctrl_space("[Behavior]\nActiveByDefault=False\n")


def test_only_a_running_input_method_with_ctrl_space_counts(desktop):
    desktop(gsettings={"org.freedesktop.ibus.general.hotkey": "['<Control>space']"},
            fcitx5="")
    assert csc.clashes() == []


def test_gsettings_failing_is_no_clash_and_no_error(desktop):
    desktop(running=["ibus-daemon"], gnome=True)
    assert csc.clashes() == []


def test_gnome_counts_only_on_gnome_with_a_ctrl_space_binding(desktop):
    listing = ("org.gnome.desktop.wm.keybindings switch-input-source ['<Control>space']\n"
               "org.gnome.desktop.wm.keybindings switch-input-source-backward "
               "['<Shift><Super>space']")
    answers = {"org.gnome.desktop.wm.keybindings": listing}
    desktop(gsettings=answers, gnome=True)
    assert csc.clashes() == [csc.GNOME]
    desktop(gsettings=answers, gnome=False)
    assert csc.clashes() == []


@pytest.mark.parametrize("option", ["compose:lctrl", "grp:ctrl_select", "grp:lctrl_toggle"])
def test_a_keyboard_option_giving_left_ctrl_another_job_is_a_clash(desktop, option):
    desktop(xkb=f"caps:escape,{option}")
    assert csc.clashes() == [csc.KEYBOARD_OPTIONS]


def test_other_keyboard_options_are_no_clash(desktop):
    desktop(xkb="caps:escape,compose:rctrl")
    assert csc.clashes() == []


def test_running_finds_this_users_process_by_name(tmp_path):
    (tmp_path / "123").mkdir()
    (tmp_path / "123" / "comm").write_text("ibus-daemon\n")
    (tmp_path / "self").mkdir()
    assert csc._running("ibus-daemon", str(tmp_path))
    assert not csc._running("fcitx5", str(tmp_path))


@pytest.fixture
def watch(monkeypatch):
    """hotkeys._watch with the desktop, the helper and the options left out."""
    monkeypatch.setattr(hotkeys, "_claim_desktop_hotkeys", lambda cfg: None)
    monkeypatch.setattr(hotkeys, "_helper_watch", lambda *a: None)
    monkeypatch.setattr(hotkeys.modifier_hotkeys, "note", lambda *a: None)
    monkeypatch.setattr(hotkeys.modifier_hotkeys, "read_compose_keys", lambda: None)

    def run(hold):
        hotkeys._watch(None, [], Hotkeys(hold, ecodes.KEY_F9, "", None, None))
    return run


def test_the_service_logs_who_also_uses_ctrl_space(watch, monkeypatch, caplog):
    monkeypatch.setattr(csc, "clashes", lambda: [csc.IBUS])
    with caplog.at_level(logging.INFO):
        watch(ecodes.KEY_SPACE)
    assert ("IBus also uses Ctrl+Space to switch input methods; "
            "Left Ctrl+Space may do that instead of dictating") in caplog.text


def test_another_hold_key_asks_nobody(watch, monkeypatch):
    def asked():
        raise AssertionError("looked for Ctrl+Space users")
    monkeypatch.setattr(csc, "clashes", asked)
    watch(ecodes.KEY_F8)
