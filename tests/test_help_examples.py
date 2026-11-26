"""Help's "you say → you get" examples (src/tuxtalk/help/examples.toml) are
promises: each one is run through the same text path a dictation takes, with
the setting it names, and has to come out as Help shows it. A change to how
TuxTalk types can't leave Help saying something it no longer does."""
import os

import pytest

from tuxtalk import help_text
from tuxtalk import live_settings as live
from tuxtalk.config import Settings, load_toml_file
from tuxtalk.dictation_text import prepare_text
from tuxtalk.key_commands import split_key_command
from tuxtalk.normalize import LINE_BREAK

EXAMPLES = load_toml_file(os.path.join(help_text._PAGES, "examples.toml"))["example"]
EXACT = [e for e in EXAMPLES if not e.get("ai")]
AI = [e for e in EXAMPLES if e.get("ai")]


def _typed(example):
    """(what TuxTalk types for the example, the key it presses after)."""
    lang = example.get("lang", "en")
    live.apply(Settings(command_language=lang, **example.get("settings", {})))
    words, key = split_key_command(example["say"], lang)
    text = prepare_text(words, live.settings.smart_quotes, True, False,
                        app_kind=example.get("app", ""), ai=False, settings=live)
    # A line break is Shift+Enter; the space before one is never seen.
    lines = text.replace(LINE_BREAK, "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines), key


@pytest.mark.parametrize("example", EXACT, ids=[e["say"] for e in EXACT])
def test_every_example_types_what_help_says(example, monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    typed, key = _typed(example)
    assert typed == example["get"]
    assert key == example.get("press", "").upper()


@pytest.mark.parametrize("example", AI, ids=[e["say"] for e in AI])
def test_an_ai_example_is_recognised_as_its_feature(example):
    """The AI's own wording varies, so Help says "something like"; what it's
    asked to do must still be what the example shows."""
    if example["ai"] == "tone":
        from tuxtalk.voice_edit_rules import tone_at_end
        assert tone_at_end(example["say"]) is not None
    else:
        from tuxtalk.ai_cleanup import has_correction
        assert has_correction(example["say"])


def test_every_group_is_shown_on_some_page():
    """A group no page names is a promise nobody reads."""
    pages = [help_text.page(name) for _title, name in help_text._TABS]
    pages.append(help_text.page("commands_examples"))
    for group in {e["group"] for e in EXAMPLES}:
        assert any(f"{{examples_{group}}}" in text for text in pages), group


def test_a_setting_help_names_is_one_the_test_turns_on_and_the_other_way_round():
    """"With Numbers set to digits" and no setting to turn on would test
    the example without it; a setting turned on and not named would show
    as what TuxTalk does by default."""
    for example in EXACT:
        assert bool(example.get("when")) == bool(example.get("settings") or
                                                  example.get("lang")), example["say"]


def test_the_key_pressed_after_an_example_is_shown_as_text():
    """A key's name is text in Help's markup, like what's said and typed."""
    from gi.repository import Pango
    line = help_text._example_line({"say": "ok", "get": "OK.", "press": "<Enter>"})
    assert Pango.parse_markup(line, -1, "\0")[2].endswith("then <Enter>")
