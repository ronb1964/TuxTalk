"""Which dictation keys every desktop is asked to hold back (hotkey_claims),
in Qt's spelling: F-keys, Scroll Lock and modifier combos, never a bare
letter, which would stop the user typing it anywhere."""
import types

import pytest

from tuxtalk import hotkey_claims


@pytest.mark.parametrize("name,qt", [
    ("F8", "F8"), ("f12", "F12"), (" F1 ", "F1"),
    ("Ctrl+Alt+V", "Ctrl+Alt+V"), ("ctrl+shift+h", "Ctrl+Shift+H"),
    ("Super+F5", "Meta+F5"),
])
def test_a_hotkey_is_named_as_qt_names_it(name, qt):
    assert hotkey_claims.qt_key_sequence(name) == qt


@pytest.mark.parametrize("name", ["", "   ", None, "A", "q", "F13", "Hyper+V",
                                  "Ctrl+", "Ctrl+Esc"])
def test_a_bare_letter_or_an_unknown_key_is_never_claimed(name):
    assert hotkey_claims.qt_key_sequence(name) is None


@pytest.mark.parametrize("name,code", [
    ("F6", 0x01000035), ("F1", 0x01000030), ("F12", 0x0100003B),
    ("Shift+F8", 0x02000000 | 0x01000037), ("Ctrl+Alt+V", 0x04000000 | 0x08000000 | 0x56),
    ("Super+F9", 0x10000000 | 0x01000038), ("", None), ("Space", None),
])
def test_kde_reports_a_key_by_its_qt_code(name, code):
    assert hotkey_claims.qt_key_code(hotkey_claims.qt_key_sequence(name)) == code


def test_a_key_used_twice_is_claimed_once():
    cfg = types.SimpleNamespace(hotkey="F8", toggle_hotkey="F8", voice_commands_hotkey="")
    assert [key for _role, _what, key in hotkey_claims.keys_to_claim(cfg)] == ["F8"]


def test_voice_editing_claims_shift_with_the_dictation_keys():
    cfg = types.SimpleNamespace(hotkey="F8", toggle_hotkey="F9", voice_editing=True)
    assert [key for _role, _what, key in hotkey_claims.keys_to_claim(cfg)] == [
        "F8", "F9", "Shift+F8", "Shift+F9"]
