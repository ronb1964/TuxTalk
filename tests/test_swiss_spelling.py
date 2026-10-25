"""Swiss German spelling: ß becomes ss (GitHub issue #10).

Swiss Standard German never writes ß, but Parakeet and Whisper do. The option
swaps it in the finished text, so it works the same with every engine.
"""
import pytest

from tuxtalk import app, config
from tuxtalk import live_settings as live
from tuxtalk.normalize import swiss_spelling


@pytest.mark.parametrize("text, expected", [
    ("Die Straße ist groß.", "Die Strasse ist gross."),
    ("Fuß", "Fuss"),
    ("ß", "ss"),
    ("STRAßE", "STRASSE"),            # an all-caps word keeps its capitals
    ("STRAẞE", "STRASSE"),            # the capital sharp s
    ("ẞ", "SS"),
    ("Grüße aus Zürich", "Grüsse aus Zürich"),
    ("no sharp s here", "no sharp s here"),
])
def test_sharp_s_becomes_ss(text, expected):
    assert swiss_spelling(text) == expected


@pytest.mark.parametrize("env, expected", [
    ({"LANG": "de_CH.UTF-8"}, True),
    ({"LC_ALL": "de_CH.UTF-8", "LANG": "en_US.UTF-8"}, True),
    ({"LANGUAGE": "de_CH:de", "LANG": "en_US.UTF-8"}, True),
    ({"LANG": "gsw_CH.UTF-8"}, True),             # Swiss German's own locale
    ({"LANG": "fr_CH.UTF-8"}, False),             # Swiss, but French
    ({"LANG": "de_DE.UTF-8"}, False),
    ({"LANG": "en_US.UTF-8"}, False),
    ({}, False),
])
def test_on_by_default_only_on_swiss_german_systems(env, expected):
    assert config.swiss_german_locale(env) is expected


def test_the_default_follows_the_system_locale(monkeypatch):
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "de_CH.UTF-8")
    assert config.Settings().swiss_spelling is True
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert config.Settings().swiss_spelling is False


def test_it_applies_without_a_restart():
    assert "swiss_spelling" in config.LIVE_APPLIED_KEYS


def _prepare(monkeypatch, on):
    monkeypatch.setattr(live, "swiss_spelling", on)
    monkeypatch.setattr(live, "words", [])
    monkeypatch.setattr(live, "remove_fillers", False)
    monkeypatch.setattr(live, "ai_corrections", False)
    return app._prepare_text("die straße ist groß", smart_quotes=True,
                             auto_period=True, auto_space=False)


def test_dictation_uses_ss_when_on(monkeypatch):
    assert _prepare(monkeypatch, True) == "Die strasse ist gross."


def test_dictation_keeps_sharp_s_when_off(monkeypatch):
    assert _prepare(monkeypatch, False) == "Die straße ist groß."


def test_the_setting_is_read_on_each_reload(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    live.apply(config.Settings(swiss_spelling=True))
    assert live.swiss_spelling is True
    live.apply(config.Settings(swiss_spelling=False))
    assert live.swiss_spelling is False


def test_preferences_has_the_checkbox_bound_to_the_setting():
    changes = []

    class Stub:
        config = {"swiss_spelling": True}

        def update_config(self, key, value):
            changes.append((key, value))

    from tuxtalk.prefs_advanced import AdvancedTab
    check = AdvancedTab._swiss_spelling_check(Stub())
    assert check.get_active() is True and "ß" in check.get_label()
    check.set_active(False)
    assert changes == [("swiss_spelling", False)]


def test_preferences_offers_it_only_when_german_is_in_play():
    """Swiss German spelling sat second in Text Formatting for everyone; now
    it shows only where it can matter (0.16 simplicity review)."""
    from tuxtalk.prefs_advanced import swiss_spelling_offered
    english = {"LANG": "en_US.UTF-8"}
    assert not swiss_spelling_offered({}, env=english)
    assert swiss_spelling_offered({}, env={"LANG": "de_DE.UTF-8"})
    assert swiss_spelling_offered({"dictation_language": "de"}, env=english)
    assert swiss_spelling_offered({"language_mode": "manual", "language": "de"}, env=english)
    assert swiss_spelling_offered({"auto_languages": "en,de"}, env=english)
    # A language picked by hand that isn't in use any more doesn't count...
    assert not swiss_spelling_offered({"language_mode": "auto", "language": "de"}, env=english)
    # ...but a box someone ticked is never hidden from them.
    assert swiss_spelling_offered({"swiss_spelling": True}, env=english)
