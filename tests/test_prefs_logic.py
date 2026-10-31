"""Tests for prefs.py pure logic (no GTK windows are created).

These cover the non-widget parts of the Preferences window: the
update_config None guard, the settings it opens with, and that the
window's own writer produces a file config.py can read back.
"""
try:
    import tomllib
except ImportError:     # Python 3.10, the AppImage's (the CI runs it)
    import toml as tomllib
from types import SimpleNamespace

import pytest

from tuxtalk import prefs as P
from tuxtalk.prefs import PreferencesWindow
from tuxtalk.prefs_general import GeneralTab
from tuxtalk.prefs_updates import UpdatesTab


def _dummy(config=None):
    """Bare stand-in for a PreferencesWindow instance."""
    return SimpleNamespace(config=config if config is not None else {})


# --- update_config: combo rebuilds emit changed with active_id=None ---

def test_update_config_ignores_none_value():
    """A GTK combo emits 'changed' with get_active_id()=None during
    remove_all(); writing that None to config would produce 'device = None'
    in the TOML file, which is unparseable and wipes all settings."""
    d = _dummy({"device": "cpu"})
    PreferencesWindow.update_config(d, "device", None)
    assert d.config["device"] == "cpu"  # unchanged


def test_update_config_sets_real_values():
    d = _dummy({"device": "cpu"})
    PreferencesWindow.update_config(d, "model", "medium")
    assert d.config["model"] == "medium"


# --- the settings Preferences opens with: read as the service reads them ---

@pytest.fixture
def opened(tmp_path, monkeypatch):
    """The settings a Preferences window opens with, from config.toml holding
    *text* (and its .bak holding *backup*, when given)."""
    from tuxtalk import config as C
    path = tmp_path / "config.toml"
    for name, value in (("CONFIG_PATH", str(path)), ("_config_cache", None),
                        ("_config_mtime", 0.0), ("_repair_attempted", False),
                        ("_config_read_failed", False)):
        monkeypatch.setattr(C, name, value)
    monkeypatch.setattr(P, "CONFIG_PATH", str(path))

    def open_with(text, backup=None):
        path.write_text(text)
        if backup is not None:
            path.with_name("config.toml.bak").write_text(backup)
        return PreferencesWindow.load_config(SimpleNamespace())
    return open_with


def test_numbers_and_switches_written_as_text_open_as_numbers_and_switches(opened):
    """Configs written by the old hand-rolled parser can hold '5' for 5, and
    "1.0" or "false" as text: Gtk.Adjustment(value='5') crashed the window."""
    out = opened('auto_timeout_minutes = "5"\nindicator_offset_x = "0"\nmodel = "small"\n'
                 'indicator_sensitivity = "1.5"\nauto_stop_seconds = 3\n'
                 'beeps = "false"\nnotify = "true"\n')
    assert out["auto_timeout_minutes"] == 5 and out["indicator_offset_x"] == 0
    assert out["model"] == "small"
    assert out["indicator_sensitivity"] == 1.5
    assert isinstance(out["auto_stop_seconds"], float)
    assert out["beeps"] is False and out["notify"] is True


def test_a_setting_that_isnt_a_number_gets_its_default_so_preferences_opens(opened):
    """auto_stop_seconds = "oops" in a hand-edited config stayed a string,
    and building the spin button for it raised: Preferences didn't open."""
    from gi.repository import Gtk
    from tuxtalk.config import Settings
    out = opened('auto_stop_seconds = "oops"\nauto_timeout_minutes = "five"\nbeeps = true\n')
    assert out["auto_stop_seconds"] == Settings().auto_stop_seconds
    assert out["auto_timeout_minutes"] == Settings().auto_timeout_minutes
    assert out["beeps"] is True
    Gtk.Adjustment(value=float(out["auto_stop_seconds"]), lower=0.5, upper=10.0)


def test_a_number_that_isnt_finite_gets_its_default_so_preferences_opens(opened):
    """press_enter_delay = nan is valid TOML and a float, and building the
    wait's dropdown raised on round(nan): Preferences didn't open."""
    from tuxtalk.config import Settings
    for value in ("nan", "inf"):
        out = opened(f"press_enter_delay = {value}\nauto_stop_seconds = {value}\n")
        assert out["press_enter_delay"] == Settings().press_enter_delay
        assert out["auto_stop_seconds"] == Settings().auto_stop_seconds
        int(round(float(out["press_enter_delay"])))


def test_an_empty_settings_file_opens_with_the_backups_settings(opened):
    """An empty config.toml parsed as no settings at all: Preferences showed
    defaults, and Apply wrote them over the file and its backup."""
    out = opened("", backup='hotkey = "F6"\ntoggle_hotkey = "F7"\nmodel = "small"\n')
    assert (out["hotkey"], out["toggle_hotkey"], out["model"]) == ("F6", "F7", "small")


def test_a_damaged_settings_file_opens_with_the_backups_settings(opened):
    """The file is repaired before anything is shown, so the hotkey fallback
    can't put F8 over the user's own key."""
    out = opened('hotkey = "F6\n', backup='hotkey = "F6"\nmodel = "small"\n')
    assert out["hotkey"] == "F6"


def test_apply_over_an_emptied_file_keeps_its_backup(tmp_path, monkeypatch):
    """The file was emptied after Preferences opened: the save writes the
    window's settings, and the backup keeps the last good copy."""
    cfg, bak = tmp_path / "config.toml", tmp_path / "config.toml.bak"
    cfg.write_text("")
    bak.write_text('hotkey = "F6"\n')
    monkeypatch.setattr(P, "CONFIG_PATH", str(cfg))
    d = SimpleNamespace(config={"hotkey": "F6", "beeps": False, "language_mode": "auto"},
                        _config_at_open={"hotkey": "F6", "beeps": True, "language_mode": "auto"})

    assert PreferencesWindow.save_config(d) is True

    assert tomllib.loads(cfg.read_text())["hotkey"] == "F6"
    assert bak.read_text() == 'hotkey = "F6"\n'


# --- save_config must emit TOML that can be read back ---

def _saveable(tmp_path, monkeypatch, config, at_open=None):
    """A stand-in window whose save_config() writes to a throwaway path, and
    whose start-at-login reaches autostart.set_autostart (_autostart)."""
    monkeypatch.setattr(P, "CONFIG_PATH", str(tmp_path / "config.toml"))
    d = SimpleNamespace(config=dict(config), window=None,
                        _config_at_open=dict(at_open if at_open is not None else {}))
    d._apply_launch_at_login = lambda before: PreferencesWindow._apply_launch_at_login(d, before)
    return d


def _autostart(monkeypatch, works=True):
    """autostart.set_autostart recording each call, done or failed (*works*,
    changeable later as .works); the messages shown go to .told."""
    from tuxtalk import autostart
    calls = SimpleNamespace(applied=[], told=[], works=works)

    def set_autostart(enable):
        calls.applied.append(enable)
        if isinstance(calls.works, Exception):
            raise calls.works
        return calls.works
    monkeypatch.setattr(autostart, "set_autostart", set_autostart)
    monkeypatch.setattr(P, "show_message", lambda parent, kind, title, text="", **k:
                        calls.told.append(title))
    return calls


def test_save_config_escapes_double_quotes(tmp_path, monkeypatch):
    """A microphone name containing a double quote must survive Apply.

    The Preferences window must escape them like config.py's _toml_string:
    `mic = "Blue Yeti "Nano" USB"` is invalid TOML, so the next load would
    quarantine the file and reset every setting — including the hotkey,
    which defaults to blank and silently kills dictation.
    """
    d = _saveable(tmp_path, monkeypatch,
                  {"mic": 'Blue Yeti "Nano" USB', "hotkey": "F8", "language_mode": "auto"})

    assert PreferencesWindow.save_config(d) is True

    data = tomllib.loads((tmp_path / "config.toml").read_text())
    assert data["mic"] == 'Blue Yeti "Nano" USB'
    assert data["hotkey"] == "F8"


def test_save_config_escapes_backslashes(tmp_path, monkeypatch):
    d = _saveable(tmp_path, monkeypatch,
                  {"mic": r"Mic\Device", "hotkey": "F8", "language_mode": "auto"})

    assert PreferencesWindow.save_config(d) is True

    data = tomllib.loads((tmp_path / "config.toml").read_text())
    assert data["mic"] == r"Mic\Device"


def test_save_config_roundtrips_through_config_loader(tmp_path, monkeypatch):
    """The file Preferences writes must be readable by config.load_config,
    not merely by tomllib — the two must agree on the same escaping."""
    from tuxtalk import config as C

    d = _saveable(tmp_path, monkeypatch,
                  {"mic": 'Blue Yeti "Nano" USB', "hotkey": "F8",
                   "model": "large-v3", "language_mode": "auto"})
    assert PreferencesWindow.save_config(d) is True

    monkeypatch.setattr(C, "CONFIG_PATH", str(tmp_path / "config.toml"))
    monkeypatch.setattr(C, "_config_cache", None)
    monkeypatch.setattr(C, "_config_mtime", None)
    s = C.load_config()
    assert s.mic == 'Blue Yeti "Nano" USB'
    assert s.hotkey == "F8"          # not reset to the blank default
    assert s.model == "large-v3"


# --- a damaged commands file must not wedge Apply/OK -----------------------

def _commands_window(rows, loaded=None):
    """A stand-in Words tab with just the snippets list."""
    import types
    from tuxtalk.prefs_words import WordsTab
    d = SimpleNamespace(commands_store=rows, _commands_loaded=loaded or {},
                        words_store=P.Gtk.ListStore(str, str), window=None,
                        read_problem=P.Gtk.Label())
    for name in ("_commands_from_store", "_fill_commands_store", "save", "changed",
                 "_split_commands", "_fill_words_store", "_show_read_problem"):
        setattr(d, name, types.MethodType(getattr(WordsTab, name), d))
    return d


def test_saving_commands_over_a_damaged_file_does_not_raise(tmp_path, monkeypatch):
    """save_custom_commands refuses to overwrite a file it could not
    read. The Words tab's save runs FIRST in both on_apply and on_ok, so
    letting that propagate would make clicking OK do nothing at all — the
    window would not even close. It must degrade, not wedge.
    """
    from tuxtalk import config as C
    from tuxtalk import prefs_words
    monkeypatch.setattr(prefs_words, "show_message", lambda *a, **k: None)

    path = tmp_path / "custom_commands.toml"
    path.write_text('[commands]\nbroken {{{\n')
    monkeypatch.setattr(C, "CUSTOM_COMMANDS_PATH", str(path))
    monkeypatch.setattr(C, "_commands_read_failed", False, raising=False)

    C.load_custom_commands()                     # fails -> flag set
    damaged = path.read_text()

    d = _commands_window([["talk type", "TalkType"]])
    assert d.save() is False

    assert path.read_text() == damaged, "damaged commands file was overwritten"


def test_saving_commands_normally_reports_success(tmp_path, monkeypatch):
    from tuxtalk import config as C

    path = tmp_path / "custom_commands.toml"
    monkeypatch.setattr(C, "CUSTOM_COMMANDS_PATH", str(path))
    monkeypatch.setattr(C, "_commands_read_failed", False, raising=False)

    d = _commands_window([["talk type", "TalkType"]])
    assert d.save() is True
    assert C.load_custom_commands() == {"talk type": "TalkType"}


def test_apply_keeps_a_word_fixed_from_the_tray_while_prefs_was_open(tmp_path, monkeypatch):
    """Writing back the list loaded at open would delete a Fix a Word fix
    saved from the tray in the meantime."""
    from tuxtalk import config as C

    path = tmp_path / "custom_commands.toml"
    monkeypatch.setattr(C, "CUSTOM_COMMANDS_PATH", str(path))
    monkeypatch.setattr(C, "_commands_read_failed", False, raising=False)
    C.save_custom_commands({"talk type": "TalkType"})

    # Preferences opens and shows the one command...
    d = _commands_window([["talk type", "TalkType"]], loaded={"talk type": "TalkType"})
    # ...then Fix a Word saves a new one from the tray...
    C.save_custom_commands({"talk type": "TalkType", "bamboo studio": "BambuStudio"})
    # ...and the user clicks Apply without touching the list.
    assert d.save() is True

    assert C.load_custom_commands() == {"talk type": "TalkType", "bamboo studio": "BambuStudio"}
    assert ["bamboo studio", "BambuStudio"] in d.commands_store   # and it now shows


# --- Preferences must not write over a config it could not read ------------

def test_save_config_refuses_when_the_file_on_disk_is_damaged(tmp_path, monkeypatch):
    """Opening Preferences over an unparseable config shows bare defaults
    (hotkey blank, model small). Clicking OK must not write those defaults
    AND copy the damaged file over config.toml.bak, destroying the only
    surviving copy of the user's real settings in one click.
    """
    cfg = tmp_path / "config.toml"
    bak = tmp_path / "config.toml.bak"
    bak.write_text('# TalkType config\nmodel = "large-v3"\nhotkey = "F8"\n')
    good_backup = bak.read_text()
    cfg.write_text('# TalkType config\nmic = "Blue Yeti "Nano" USB"\n')
    damaged = cfg.read_text()

    monkeypatch.setattr(P, "CONFIG_PATH", str(cfg))
    d = SimpleNamespace(config={"hotkey": "", "model": "small", "language_mode": "auto"},
                        _config_at_open={"hotkey": "", "model": "small",
                                         "language_mode": "auto"})

    assert PreferencesWindow.save_config(d) is False

    assert cfg.read_text() == damaged, "damaged config was overwritten with defaults"
    assert bak.read_text() == good_backup, "good backup was destroyed"


def test_save_config_still_writes_when_the_file_is_readable(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('# TalkType config\nmodel = "large-v3"\n')
    monkeypatch.setattr(P, "CONFIG_PATH", str(cfg))

    d = SimpleNamespace(config={"model": "medium", "language_mode": "auto"},
                        _config_at_open={"model": "large-v3", "language_mode": "auto"})

    assert PreferencesWindow.save_config(d) is True
    assert tomllib.loads(cfg.read_text())["model"] == "medium"


# --- the hotkey dropdown must not display one value and save another -------

class FakeCombo:
    """Minimal ComboBoxText stand-in: records the id set on it."""

    def __init__(self, ids=("F8", "F9", "F10")):
        self.active_id = None
        self.ids = list(ids)
        self._handlers = []
        self.closed = 0

    def append(self, key, text):
        self.ids.append(key)

    def set_active_id(self, value):
        if value not in self.ids:
            return False
        self.active_id = value
        for signal, handler, *args in list(self._handlers):
            if signal == "changed":
                handler(self, *args)
        return True

    def popdown(self):
        self.closed += 1

    def get_active_id(self):
        return self.active_id

    def connect(self, signal, handler, *args):
        self._handlers.append((signal, handler, *args))


def test_hotkey_fallback_is_written_into_the_config(monkeypatch):
    """When no hotkey is configured (Settings default is ""), the dropdown
    falls back to displaying F8, and self.config must say F8 too.

    Otherwise OK saves a blank hotkey while the screen says F8, and
    re-selecting F8 emits no 'changed' signal because it is already active.
    A user whose onboarding was interrupted would have dead dictation, and
    the one screen they would use to fix it could not fix it.
    """
    d = SimpleNamespace(config={"hotkey": "", "toggle_hotkey": ""},
                        hotkey_combo=FakeCombo(),
                        toggle_combo=FakeCombo())

    GeneralTab._apply_hotkey_fallbacks(d, ["F8", "F9", "F10"])

    assert d.hotkey_combo.get_active_id() == "F8"
    assert d.config["hotkey"] == "F8", "screen shows F8 but a blank hotkey would be saved"
    assert d.toggle_combo.get_active_id() == "F9"
    assert d.config["toggle_hotkey"] == "F9"


def test_a_configured_hotkey_is_left_alone(monkeypatch):
    d = SimpleNamespace(config={"hotkey": "F10", "toggle_hotkey": "F9"},
                        hotkey_combo=FakeCombo(),
                        toggle_combo=FakeCombo())

    GeneralTab._apply_hotkey_fallbacks(d, ["F8", "F9", "F10"])

    assert d.hotkey_combo.get_active_id() == "F10"
    assert d.config["hotkey"] == "F10"


def test_a_hand_edited_hotkey_is_shown_and_kept_not_replaced_by_f8():
    """A saved key the list doesn't have was switched to F8, and the next
    Apply saved F8 over it."""
    d = SimpleNamespace(config={"hotkey": "F13", "toggle_hotkey": "F9"},
                        hotkey_combo=FakeCombo(), toggle_combo=FakeCombo())

    GeneralTab._apply_hotkey_fallbacks(d, ["F8", "F9", "F10"])

    assert d.hotkey_combo.get_active_id() == "F13"
    assert d.config["hotkey"] == "F13"


def test_on_the_flatpak_a_blank_hotkey_is_not_a_change(monkeypatch):
    """The desktop holds the Flatpak's keys and the dropdowns are hidden:
    recording F8 made every close ask "Apply your changes before closing?"."""
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    d = SimpleNamespace(config={"hotkey": "", "toggle_hotkey": ""},
                        hotkey_combo=FakeCombo(), toggle_combo=FakeCombo())

    GeneralTab._apply_hotkey_fallbacks(d, ["F8", "F9", "F10"])

    assert d.config == {"hotkey": "", "toggle_hotkey": ""}


def _keys_tab(monkeypatch, hold="F8", toggle="F9"):
    """The two dictation key dropdowns, connected as the General tab connects
    them; the warnings shown go to .told."""
    from tuxtalk import prefs_general
    told = []
    monkeypatch.setattr(prefs_general, "show_message",
                        lambda parent, kind, title, text="", **k: told.append(title))
    d = SimpleNamespace(config={"hotkey": hold, "toggle_hotkey": toggle}, window=None,
                        hotkey_combo=FakeCombo(), toggle_combo=FakeCombo(), told=told)
    d.update_config = d.config.__setitem__
    d._on_key_picked = lambda *a: GeneralTab._on_key_picked(d, *a)
    GeneralTab._apply_hotkey_fallbacks(d, ["F8", "F9", "F10"])     # as the tab is built
    d.hotkey_combo.connect("changed", d._on_key_picked, "hotkey", d.toggle_combo)
    d.toggle_combo.connect("changed", d._on_key_picked, "toggle_hotkey", d.hotkey_combo)
    return d


def test_the_hold_key_cant_also_be_the_toggle_key(monkeypatch):
    """F8 for both was saved, and only one of them could ever start."""
    d = _keys_tab(monkeypatch)

    d.toggle_combo.set_active_id("F8")

    assert d.told == ["F8 is already your hold key"]
    assert d.toggle_combo.get_active_id() == "F9"
    assert d.config == {"hotkey": "F8", "toggle_hotkey": "F9"}


def test_another_key_for_the_toggle_is_taken(monkeypatch):
    d = _keys_tab(monkeypatch)

    d.toggle_combo.set_active_id("F10")
    d.hotkey_combo.set_active_id("F10")

    assert d.config == {"hotkey": "F8", "toggle_hotkey": "F10"}
    assert d.told == ["F10 is already your toggle key"]


def test_on_the_flatpak_a_refused_key_goes_back_to_the_key_shown(monkeypatch):
    """Both keys blank, shown as F8 and F9: picking F8 for the toggle was
    refused, but its dropdown kept showing F8, since a blank key matches no
    entry to go back to. It shows F9 again, and the blank stays blank."""
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    d = _keys_tab(monkeypatch, hold="", toggle="")

    d.toggle_combo.set_active_id("F8")

    assert d.told == ["F8 is already your hold key"]
    assert d.toggle_combo.get_active_id() == "F9"
    assert d.config == {"hotkey": "", "toggle_hotkey": ""}


# --- "Launch at login" must follow the save, not the click ------------------

def test_toggling_launch_at_login_does_not_take_effect_immediately(monkeypatch):
    """Writing the autostart .desktop file the moment the box is ticked would
    survive a Cancel that discards the config change, so the checkbox and
    reality would disagree from then on: the next open reads the checkbox
    state from the config."""
    calls = _autostart(monkeypatch)
    d = SimpleNamespace(config={"launch_at_login": False})

    PreferencesWindow.update_config(d, "launch_at_login", True)

    assert d.config["launch_at_login"] is True      # staged
    assert calls.applied == [], "autostart was changed before the user saved"


def test_saving_applies_launch_at_login(tmp_path, monkeypatch):
    calls = _autostart(monkeypatch)
    d = _saveable(tmp_path, monkeypatch,
                  {"launch_at_login": True, "language_mode": "auto"},
                  {"launch_at_login": False, "language_mode": "auto"})

    assert PreferencesWindow.save_config(d) is True

    assert calls.applied == [True], "autostart was not applied when the user saved"
    assert calls.told == []


def test_saving_with_it_off_applies_removal(tmp_path, monkeypatch):
    calls = _autostart(monkeypatch)
    d = _saveable(tmp_path, monkeypatch,
                  {"launch_at_login": False, "language_mode": "auto"},
                  {"launch_at_login": True, "language_mode": "auto"})

    assert PreferencesWindow.save_config(d) is True

    assert calls.applied == [False]


@pytest.mark.parametrize("failure", [False, OSError("read-only file system")],
                         ids=["refused", "raised"])
def test_a_failing_autostart_keeps_the_settings_says_so_and_is_tried_again(
        tmp_path, monkeypatch, failure):
    """A read-only autostart folder, or the Flatpak's background request
    denied: the settings just saved stay saved, the user hears that start at
    login didn't change, and it's still a change, so Apply tries again
    (before, it was called applied and Apply found nothing to do)."""
    calls = _autostart(monkeypatch, works=failure)
    d = _saveable(tmp_path, monkeypatch,
                  {"launch_at_login": True, "language_mode": "auto"},
                  {"launch_at_login": False, "language_mode": "auto"})

    assert PreferencesWindow.save_config(d) is True
    assert tomllib.loads((tmp_path / "config.toml").read_text())["launch_at_login"] is True
    assert calls.told == ["Start at login wasn't turned on"]
    assert PreferencesWindow.has_unapplied_changes(
        SimpleNamespace(**vars(d), words_tab=SimpleNamespace(changed=lambda: False)))

    _autostart(monkeypatch, works=True)
    assert PreferencesWindow.save_config(d) is True     # Apply again
    assert d._config_at_open["launch_at_login"] is True


@pytest.mark.parametrize("turning_on", [True, False], ids=["turning on", "turning off"])
def test_start_at_login_stays_a_change_until_it_works(tmp_path, monkeypatch, turning_on):
    """The second failed Apply took what the first had already written to the
    settings file as done, so the third found nothing to do and start at
    login never changed."""
    calls = _autostart(monkeypatch, works=False)
    d = _saveable(tmp_path, monkeypatch,
                  {"launch_at_login": turning_on, "language_mode": "auto"},
                  {"launch_at_login": not turning_on, "language_mode": "auto"})

    for _apply in range(2):
        assert PreferencesWindow.save_config(d) is True
    calls.works = True
    assert PreferencesWindow.save_config(d) is True

    assert calls.applied == [turning_on] * 3
    assert d._config_at_open["launch_at_login"] is turning_on


def test_ok_stays_open_when_start_at_login_failed(tmp_path, monkeypatch):
    """Its warning says Apply tries again, and OK closed the window: the
    next Preferences opened with nothing to retry. The other settings are
    still put in force."""
    _autostart(monkeypatch, works=False)
    d = _saveable(tmp_path, monkeypatch,
                  {"launch_at_login": True, "beeps": False, "language_mode": "auto"},
                  {"launch_at_login": False, "beeps": True, "language_mode": "auto"})
    steps = []
    vars(d).update(
        _restart_owed=set(), words_tab=SimpleNamespace(save=lambda: True),
        general_tab=SimpleNamespace(
            speech=SimpleNamespace(download_selected_model=lambda: True),
            show_saved=lambda: None, after_hotkey_change=lambda changed, since: False),
        put_in_force=lambda changed: steps.append(("in force", set(changed))) or True,
        _apply_or_restart=lambda changed: steps.append("closing restart") or True,
        _release=lambda: steps.append("released"))
    for name in ("save_config", "_save_and_download", "_changed_since_open",
                 "_autostart_pending"):
        setattr(d, name, getattr(PreferencesWindow, name).__get__(d))
    monkeypatch.setattr(P.Gtk, "main_quit", lambda: steps.append("quit"))

    PreferencesWindow.on_ok(d, None)

    assert steps == [("in force", {"beeps", "launch_at_login"})]


def test_saving_an_unrelated_change_leaves_autostart_alone(tmp_path, monkeypatch):
    """Rewriting the autostart entry on every Apply would undo turning TuxTalk
    off in the desktop's own Autostart settings at the next unrelated change."""
    calls = _autostart(monkeypatch)
    for launch in (True, False):
        d = _saveable(tmp_path, monkeypatch,
                      {"launch_at_login": launch, "beeps": False, "language_mode": "auto"},
                      {"launch_at_login": launch, "beeps": True, "language_mode": "auto"})

        assert PreferencesWindow.save_config(d) is True
        assert PreferencesWindow.save_config(d) is True    # a second Apply

    assert calls.applied == [], "autostart was rewritten though launch at login did not change"


def test_a_failed_save_says_why(tmp_path, monkeypatch):
    """"Failed to save settings!" gave no reason, so a full disk or read-only
    folder looked like a bug in TuxTalk."""
    def full_disk(path, text, keep_backup=True):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(P, "write_text_atomic", full_disk)
    d = _saveable(tmp_path, monkeypatch, {"beeps": False, "language_mode": "auto"},
                  {"beeps": True, "language_mode": "auto"})

    assert PreferencesWindow.save_config(d) is False
    assert "No space left on device" in d._save_error


def _window(**parts):
    """A stand-in window whose save_before_restart is the real one."""
    win = SimpleNamespace(**{"_restart_owed": set(), "_changed_since_open": set,
                             "_config_at_open": {}, "config": {}, **parts})
    win.save_before_restart = lambda: PreferencesWindow.save_before_restart(win)
    return win


def test_an_update_doesnt_start_when_the_changes_couldnt_be_saved(monkeypatch, message_boxes):
    """"Any unsaved preferences changes will be saved first": with the disk
    full or the file unreadable the save failed, the update went ahead, and
    its restart lost the changes."""
    from tuxtalk import installation, update_ui
    shown = []
    message_boxes.answer = P.Gtk.ResponseType.YES
    monkeypatch.setattr(installation, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(update_ui, "get_update_guidance",
                        lambda kind, version: {"update_method": "appimage"})
    monkeypatch.setattr(P.Gtk, "Dialog", lambda **kw: pytest.fail("the update started"))
    tab = SimpleNamespace(
        _current_release={"appimage_url": "https://example.invalid/TuxTalk.AppImage"},
        window=None, win=_window(
            save_config=lambda: False, words_tab=SimpleNamespace(save=lambda: True),
            _show_save_error=lambda: shown.append("save error")))
    UpdatesTab._on_download_update_clicked(tab, None)
    assert shown == ["save error"]


def test_an_update_saves_the_snippets_first(monkeypatch, message_boxes):
    """Snippets are saved apart from the settings: an update after an edited
    snippet, with no Apply, restarted TuxTalk and lost the edit. A failed
    snippet save stops the update, as a failed settings save does."""
    from tuxtalk import installation, update_ui
    saved = []
    message_boxes.answer = P.Gtk.ResponseType.YES
    monkeypatch.setattr(installation, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(update_ui, "get_update_guidance",
                        lambda kind, version: {"update_method": "appimage"})
    monkeypatch.setattr(P.Gtk, "Dialog", lambda **kw: pytest.fail("the update started"))
    tab = SimpleNamespace(
        _current_release={"appimage_url": "https://example.invalid/TuxTalk.AppImage"},
        window=None, win=_window(
            words_tab=SimpleNamespace(save=lambda: saved.append("snippets") and None),
            save_config=lambda: saved.append("settings") or True,
            _show_save_error=lambda: saved.append("save error")))
    UpdatesTab._on_download_update_clicked(tab, None)
    assert saved == ["snippets"]
