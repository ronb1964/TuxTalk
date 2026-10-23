"""The language pickers show each language's own name (Ron, 2026-10-13):
"Spanish (Español)", so someone who doesn't read English finds theirs. No
flags: a language isn't a country."""
import unicodedata

import pytest

from tuxtalk import recommend as rec


@pytest.mark.parametrize("code, label", [
    ("en", "English"),
    ("es", "Spanish (Español)"),
    ("ja", "Japanese (日本語)"),
    ("de", "German (Deutsch)"),
    ("ar", "Arabic (العربية)"),
    ("xx", "xx"),                      # unknown: just the code, as before
])
def test_labels(code, label):
    assert rec.language_label(code) == label


def test_every_language_but_english_has_its_own_name():
    missing = [c for c, _ in rec.LANGUAGES if c != "en" and c not in rec._NATIVE]
    assert missing == []


def test_the_english_name_comes_first_so_the_order_holds():
    for code, name in rec.LANGUAGES:
        assert rec.language_label(code).startswith(name)


def test_no_flags():
    for code, _ in rec.LANGUAGES:
        label = rec.language_label(code)
        assert not any(unicodedata.name(ch, "").startswith("REGIONAL INDICATOR")
                       for ch in label), label


def test_preferences_and_first_run_list_the_same_languages(prefs_window, welcome_window):
    """Preferences had its own flagged list, which also lacked Maltese."""
    every = [rec.language_label(code) for code, _ in rec.LANGUAGES]
    preferences = [row[0] for row in prefs_window(model="small").lang_combo.get_model()]
    first_run = [row[0] for row in welcome_window.lang_combo.get_model()]
    assert preferences[1:1 + len(every)] == every       # after "Detect automatically"
    assert first_run == every
