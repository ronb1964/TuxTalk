"""Tests for prefs.py pure logic (no GTK windows are created).

These cover the non-widget parts of the Preferences window: the
update_config None guard, on-disk value type coercion, and that the
window's own writer produces a file config.py can read back.
"""
try:
    import tomllib
except ImportError:     # Python 3.10, the AppImage's (the CI runs it)
    import toml as tomllib
from types import SimpleNamespace

import pytest

from tuxtalk import prefs as P
from tuxtalk.prefs import PreferencesWindow, _coerce_config_types
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


# --- type coercion of on-disk values against Settings field types ---

def test_coerce_config_types_fixes_string_numbers():
    """Configs written by the old hand-rolled parser can contain '5' instead
    of 5, which would crash the window at build time."""
    d = {"auto_timeout_minutes": "5", "indicator_offset_x": "0", "model": "small"}
    out = _coerce_config_types(d)
    assert out["auto_timeout_minutes"] == 5
    assert out["indicator_offset_x"] == 0
    assert out["model"] == "small"


def test_coerce_config_types_fixes_string_floats():
    """indicator_sensitivity = "1.0" must not reach Scale.set_value() as a
    string (TypeError while the window is built), and "5.0" for an int field
    must not stay a string."""
    d = {"indicator_sensitivity": "1.5", "auto_stop_seconds": 3,
         "auto_timeout_minutes": "5.0"}
    out = _coerce_config_types(d)
    assert out["indicator_sensitivity"] == 1.5
    assert isinstance(out["auto_stop_seconds"], float)
    assert out["auto_timeout_minutes"] == 5


def test_coerce_config_types_fixes_string_bools():
    d = {"beeps": "false", "notify": "true"}
    out = _coerce_config_types(d)
    assert out["beeps"] is False
    assert out["notify"] is True


def test_coerce_config_types_leaves_correct_types_alone():
    d = {"beeps": True, "auto_timeout_minutes": 7, "language": "es", "custom_key": [1]}
    out = _coerce_config_types(d)
    assert out["beeps"] is True
    assert out["auto_timeout_minutes"] == 7
    assert out["language"] == "es"
    assert out["custom_key"] == [1]  # unknown keys untouched


def test_a_setting_that_isnt_a_number_gets_its_default_so_preferences_opens():
    """auto_stop_seconds = "oops" in a hand-edited config stayed a string,
    and building the spin button for it raised: Preferences didn't open."""
    from gi.repository import Gtk
    from tuxtalk.config import Settings
    out = _coerce_config_types({"auto_stop_seconds": "oops", "auto_timeout_minutes": "five",
                                "beeps": True})
    assert out["auto_stop_seconds"] == Settings().auto_stop_seconds
    assert out["auto_timeout_minutes"] == Settings().auto_timeout_minutes
    assert out["beeps"] is True
    Gtk.Adjustment(value=float(out["auto_stop_seconds"]), lower=0.5, upper=10.0)


def test_a_number_that_isnt_finite_gets_its_default_so_preferences_opens():
    """press_enter_delay = nan is valid TOML and a float, and building the
    wait's dropdown raised on round(nan): Preferences didn't open."""
    from tuxtalk.config import Settings
    for value in (float("nan"), float("inf")):
        out = _coerce_config_types({"press_enter_delay": value, "auto_stop_seconds": value})
        assert out["press_enter_delay"] == Settings().press_enter_delay
        assert out["auto_stop_seconds"] == Settings().auto_stop_seconds
        int(round(float(out["press_enter_delay"])))


# --- save_config must emit TOML that can be read back ---

def _saveable(tmp_path, monkeypatch, config, at_open=None):
    """A stand-in window whose save_config() writes to a throwaway path."""
    monkeypatch.setattr(P, "CONFIG_PATH", str(tmp_path / "config.toml"))
    return SimpleNamespace(config=dict(config),
                           _config_at_open=dict(at_open if at_open is not None else {}),
                           _handle_autostart=lambda v: None)


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
                        words_store=P.Gtk.ListStore(str, str))
    for name in ("_commands_from_store", "_fill_commands_store", "save",
                 "_split_commands", "_fill_words_store"):
        setattr(d, name, types.MethodType(getattr(WordsTab, name), d))
    return d


def test_saving_commands_over_a_damaged_file_does_not_raise(tmp_path, monkeypatch):
    """save_custom_commands refuses to overwrite a file it could not
    read. The Words tab's save runs FIRST in both on_apply and on_ok, so
    letting that propagate would make clicking OK do nothing at all — the
    window would not even close. It must degrade, not wedge.
    """
    from tuxtalk import config as C

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

    def __init__(self):
        self.active_id = None
        self._handlers = []

    def set_active_id(self, value):
        self.active_id = value

    def get_active_id(self):
        return self.active_id

    def connect(self, signal, handler):
        self._handlers.append((signal, handler))


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


# --- "Launch at login" must follow the save, not the click ------------------

def test_toggling_launch_at_login_does_not_take_effect_immediately():
    """Writing the autostart .desktop file the moment the box is ticked would
    survive a Cancel that discards the config change, so the checkbox and
    reality would disagree from then on: the next open reads the checkbox
    state from the config."""
    applied = []
    d = SimpleNamespace(config={"launch_at_login": False},
                        _handle_autostart=lambda v: applied.append(v))

    PreferencesWindow.update_config(d, "launch_at_login", True)

    assert d.config["launch_at_login"] is True      # staged
    assert applied == [], "autostart was changed before the user saved"


def test_saving_applies_launch_at_login(tmp_path, monkeypatch):
    applied = []
    d = _saveable(tmp_path, monkeypatch,
                  {"launch_at_login": True, "language_mode": "auto"},
                  {"launch_at_login": False, "language_mode": "auto"})
    d._handle_autostart = lambda v: applied.append(v)

    assert PreferencesWindow.save_config(d) is True

    assert applied == [True], "autostart was not applied when the user saved"


def test_saving_with_it_off_applies_removal(tmp_path, monkeypatch):
    applied = []
    d = _saveable(tmp_path, monkeypatch,
                  {"launch_at_login": False, "language_mode": "auto"},
                  {"launch_at_login": True, "language_mode": "auto"})
    d._handle_autostart = lambda v: applied.append(v)

    assert PreferencesWindow.save_config(d) is True

    assert applied == [False]


def test_a_failing_autostart_does_not_break_saving(tmp_path, monkeypatch):
    """Writing the .desktop file can fail (read-only home). The settings the
    user just saved must still be saved."""
    def boom(v):
        raise OSError("read-only file system")

    d = _saveable(tmp_path, monkeypatch,
                  {"launch_at_login": True, "language_mode": "auto"},
                  {"launch_at_login": False, "language_mode": "auto"})
    d._handle_autostart = boom

    assert PreferencesWindow.save_config(d) is True
    assert tomllib.loads((tmp_path / "config.toml").read_text())["launch_at_login"] is True


def test_saving_an_unrelated_change_leaves_autostart_alone(tmp_path, monkeypatch):
    """Rewriting the autostart entry on every Apply would undo turning TuxTalk
    off in the desktop's own Autostart settings at the next unrelated change."""
    for launch in (True, False):
        applied = []
        d = _saveable(tmp_path, monkeypatch,
                      {"launch_at_login": launch, "beeps": False, "language_mode": "auto"},
                      {"launch_at_login": launch, "beeps": True, "language_mode": "auto"})
        d._handle_autostart = applied.append

        assert PreferencesWindow.save_config(d) is True
        assert PreferencesWindow.save_config(d) is True    # a second Apply

        assert applied == [], "autostart was rewritten though launch at login did not change"


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
    win = SimpleNamespace(**parts)
    win.save_before_restart = lambda: PreferencesWindow.save_before_restart(win)
    return win


def test_an_update_doesnt_start_when_the_changes_couldnt_be_saved(monkeypatch, message_boxes):
    """"Any unsaved preferences changes will be saved first": with the disk
    full or the file unreadable the save failed, the update went ahead, and
    its restart lost the changes."""
    from tuxtalk import installation, update_install
    shown = []
    message_boxes.answer = P.Gtk.ResponseType.YES
    monkeypatch.setattr(installation, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(update_install, "get_update_guidance",
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
    from tuxtalk import installation, update_install
    saved = []
    message_boxes.answer = P.Gtk.ResponseType.YES
    monkeypatch.setattr(installation, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(update_install, "get_update_guidance",
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
