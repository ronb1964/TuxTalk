"""COSMIC: TuxTalk's hotkeys become COSMIC shortcuts while dictation is on, so
F8 stops typing "^[[19~" into a terminal on Pop!_OS.

These work on a private config folder. That cosmic-comp reads the entries
and swallows the key was checked in the Pop!_OS test VM (cosmic_hotkeys'
docstring).
"""
import os
import types

import pytest

from tuxtalk import cosmic_hotkeys as ch

# Written by COSMIC Settings -> Keyboard -> Custom shortcuts, laid out as it
# writes the file.
USERS_OWN = """{
    (
        modifiers: [
            Super,
        ],
        key: "t",
        description: Some("Terminal"),
    ): Spawn("cosmic-term"),
}
"""


def _cfg(hotkey="F8", toggle="F9", vc="Ctrl+Alt+V", voice_editing=False):
    return types.SimpleNamespace(hotkey=hotkey, toggle_hotkey=toggle,
                                 voice_commands_hotkey=vc, paste_last_hotkey="",
                                 voice_editing=voice_editing)


@pytest.fixture
def cosmic(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "COSMIC")
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "share"))
    return tmp_path


def _custom():
    with open(ch.custom_path()) as f:
        return f.read()


def _write_custom(text):
    os.makedirs(os.path.dirname(ch.custom_path()), exist_ok=True)
    with open(ch.custom_path(), "w") as f:
        f.write(text)


def _held(text):
    """The keys TuxTalk holds in a shortcuts file, as (modifiers, key)."""
    return sorted((tuple(sorted(mods)), key) for mods, key in ch._taken(text)
                  if (mods, key) not in ch._taken(ch._OURS.sub("", text)))


def test_the_dictation_keys_become_cosmic_shortcuts(cosmic):
    assert ch.claim(_cfg())
    text = _custom()
    assert _held(text) == [((), "f8"), ((), "f9"), (("Alt", "Ctrl"), "v")]
    assert text.startswith("{\n") and text.endswith("}\n")
    assert '(modifiers: [], key: "F8", description: Some("' + ch.DESCRIPTION + '")): ' \
           'Spawn("true"),' in text


def test_voice_editing_holds_shift_with_the_key_too(cosmic):
    """cosmic-comp matches modifiers exactly: a plain F8 shortcut lets
    Shift+F8 through."""
    ch.claim(_cfg(vc="", voice_editing=True))
    assert _held(_custom()) == [((), "f8"), ((), "f9"), (("Shift",), "f8"), (("Shift",), "f9")]


def test_the_users_own_shortcuts_are_kept(cosmic):
    _write_custom(USERS_OWN)
    ch.claim(_cfg())
    text = _custom()
    assert text.startswith(USERS_OWN[:-2])       # all but the closing brace
    assert ch._OURS.sub("", text) == USERS_OWN


def test_a_last_entry_without_a_comma_gets_one(cosmic):
    """RON needs a comma between entries; COSMIC drops every custom shortcut
    when the file doesn't parse."""
    _write_custom('{\n    (modifiers: [Super], key: "t"): Spawn("cosmic-term")\n}\n')
    ch.claim(_cfg())
    assert 'Spawn("cosmic-term"),\n    (modifiers: [], key: "F8"' in _custom()


def test_stopping_dictation_gives_the_keys_back(cosmic):
    _write_custom(USERS_OWN)
    ch.claim(_cfg())
    assert ch.release()
    assert _custom() == USERS_OWN


def test_entries_cosmic_settings_has_laid_out_again_are_still_found(cosmic):
    """COSMIC Settings rewrites the whole file when the user changes any
    shortcut, one field per line."""
    _write_custom(USERS_OWN[:-2] + f"""    (
        modifiers: [],
        key: "F8",
        description: Some("{ch.DESCRIPTION}"),
    ): Spawn("true"),
}}
""")
    ch.release()
    assert _custom() == USERS_OWN


def test_an_action_laid_out_over_several_lines_is_still_found(cosmic):
    entry = f'(modifiers: [], key: "F8", description: Some("{ch.DESCRIPTION}"))'
    _write_custom(USERS_OWN[:-2] + f"""    {entry}: Spawn(
        "true",
    ),
}}
""")
    ch.release()
    assert _custom() == USERS_OWN


def test_entries_left_by_a_crash_go_when_uninstalling_from_another_desktop(cosmic,
                                                                         monkeypatch):
    _write_custom(USERS_OWN)
    ch.claim(_cfg())
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert ch.release()
    assert _custom() == USERS_OWN


def test_a_changed_hotkey_replaces_the_old_one(cosmic):
    ch.claim(_cfg())
    ch.claim(_cfg(hotkey="F6"))
    assert _held(_custom()) == [((), "f6"), ((), "f9"), (("Alt", "Ctrl"), "v")]


def test_a_key_the_user_already_has_a_shortcut_on_stays_theirs(cosmic, caplog):
    _write_custom('{\n    (modifiers: [], key: "F9", description: Some("Mine")): '
                  'Spawn("my-script"),\n}\n')
    ch.claim(_cfg())
    text = _custom()
    assert _held(text) == [((), "f8"), (("Alt", "Ctrl"), "v")]
    assert 'Spawn("my-script")' in text
    assert "COSMIC already has a shortcut on F9" in caplog.text


def test_a_key_cosmic_itself_uses_is_left_alone(cosmic):
    defaults = cosmic / "share" / ch.SHORTCUTS_DIR / "defaults"
    defaults.parent.mkdir(parents=True)
    defaults.write_text('{\n    (modifiers: [Super], key: "F8"): Fullscreen,\n}\n')
    ch.claim(_cfg(hotkey="Super+F8"))
    assert _held(_custom()) == [((), "f9"), (("Alt", "Ctrl"), "v")]


def test_a_file_that_isnt_a_shortcut_map_is_never_touched(cosmic, caplog):
    _write_custom("this is not RON\n")
    assert not ch.claim(_cfg())
    assert _custom() == "this is not RON\n"
    assert "Could not make sense of COSMIC" in caplog.text


def test_the_file_keeps_its_permissions(cosmic):
    _write_custom(USERS_OWN)
    os.chmod(ch.custom_path(), 0o664)
    ch.claim(_cfg())
    assert os.stat(ch.custom_path()).st_mode & 0o777 == 0o664


def test_a_file_that_may_end_in_a_comment_is_never_touched(cosmic):
    """A comma added after "// terminal" would be part of the comment, and
    COSMIC would drop every custom shortcut."""
    text = '{\n    (modifiers: [Super], key: "t"): Spawn("cosmic-term") // terminal\n}\n'
    _write_custom(text)
    assert not ch.claim(_cfg())
    assert _custom() == text


def test_a_comment_ending_in_a_comma_still_counts(cosmic):
    text = '{\n    (modifiers: [Super], key: "t"): Spawn("cosmic-term") // a, b,\n}\n'
    _write_custom(text)
    assert not ch.claim(_cfg())
    assert _custom() == text


def test_a_raw_string_that_could_hide_a_comment_is_never_touched(cosmic):
    text = '{\n    (modifiers: [Super], key: "t"): Spawn(r"a\\" // b")\n}\n'
    _write_custom(text)
    assert not ch.claim(_cfg())
    assert _custom() == text


def test_slashes_inside_a_command_are_not_a_comment(cosmic):
    _write_custom('{\n    (modifiers: [Super], key: "b"): Spawn("xdg-open https://tuxtalk.app")\n}\n')
    assert ch.claim(_cfg())
    assert 'Spawn("xdg-open https://tuxtalk.app"),\n' in _custom()


def test_a_shortcut_saved_in_cosmic_settings_meanwhile_is_kept(cosmic, monkeypatch):
    """COSMIC Settings writes the whole file; TuxTalk mustn't put back the
    copy it read before that save."""
    _write_custom(USERS_OWN)
    read = ch._read
    reads = []

    def read_while_settings_saves(path):
        if path == ch.custom_path():
            reads.append(path)
        if len(reads) == 2 and path == ch.custom_path():   # the check before the rename
            with open(path, "w") as f:
                f.write(USERS_OWN.replace('"t"', '"e"'))
        return read(path)

    monkeypatch.setattr(ch, "_read", read_while_settings_saves)
    assert ch.claim(_cfg())
    text = _custom()
    assert 'key: "e"' in text and 'key: "t"' not in text
    assert _held(text) == [((), "f8"), ((), "f9"), (("Alt", "Ctrl"), "v")]


def test_a_failed_write_leaves_the_file_as_it_was(cosmic, monkeypatch):
    _write_custom(USERS_OWN)

    def full_disk(src, dst):
        raise OSError("No space left on device")

    monkeypatch.setattr(ch.os, "replace", full_disk)
    assert not ch.claim(_cfg())
    assert _custom() == USERS_OWN
    assert os.listdir(os.path.dirname(ch.custom_path())) == ["custom"]


def test_other_desktops_get_no_cosmic_shortcuts(cosmic, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert not ch.claim(_cfg())
    assert not os.path.exists(ch.custom_path())


def test_stopping_the_service_gives_the_keys_back_on_cosmic(cosmic, monkeypatch):
    """A stop or a SIGKILL skips the service's exit hook, so the launcher
    releases them, or F8 stays dead with dictation off."""
    from tuxtalk import service_launcher
    _write_custom(USERS_OWN)
    ch.claim(_cfg())
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [])
    service_launcher.stop_dictation_service()
    assert _custom() == USERS_OWN


def _tray_noticing(service_running):
    """The tray's check after it saw the service stop (a crash or a SIGKILL
    skips the service's own exit hook), with the service *service_running*
    again by then or not."""
    from tuxtalk import tray
    stub = types.SimpleNamespace(is_service_running=lambda: service_running)
    tray.DictationTray._release_desktop_hotkeys(stub)


def test_a_crashed_service_gives_the_keys_back_on_cosmic(cosmic):
    """The service's exit hook never ran, so the tray hands the keys back,
    or F8 stays swallowed with dictation off."""
    _write_custom(USERS_OWN)
    ch.claim(_cfg())
    _tray_noticing(service_running=False)
    assert _custom() == USERS_OWN


def test_a_service_started_meanwhile_keeps_its_keys_on_cosmic(cosmic):
    """Dictation turned on again before the tray looked: the new service
    holds the keys, and releasing them would let F8 through to the app."""
    _write_custom(USERS_OWN)
    ch.claim(_cfg())
    _tray_noticing(service_running=True)
    assert _held(_custom()) == [((), "f8"), ((), "f9"), (("Alt", "Ctrl"), "v")]
