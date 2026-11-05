"""Voice commands (TuxTalk Labs) through the dispatcher: "new tab" said alone
presses Ctrl+T and types nothing, while a sentence with the words in it is
typed. The keyboard records, so no test presses a real key."""
import pytest

from conftest import esc
from tuxtalk import focused_window, keyboard_output, notify, screen_message, voice_actions
from tuxtalk import live_settings as live

CTRL_T = [29, keyboard_output.nav_keycode("T")]


@pytest.fixture
def actions_on(chat, monkeypatch):
    """Voice commands on, with notices kept off the real desktop."""
    monkeypatch.setattr(live, "open_apps", True)
    monkeypatch.setattr(screen_message, "show_over_active_window", lambda *a, **k: None)
    monkeypatch.setattr(notify, "notify", lambda *a, **k: None)


@pytest.fixture
def say(chat, let_go):
    """say(words): a whole dictation of *words*, through the real pipeline."""
    def said(words):
        chat[1].append(words)
        return let_go()
    return said


def typed(chat):
    return [text for text, _ in chat[2]]


def test_new_tab_said_alone_presses_ctrl_t_and_types_nothing(actions_on, chat, say, keyboard):
    say("New tab.")
    assert chat[2] == [] and keyboard.typed_keys() == CTRL_T


def test_a_sentence_with_new_tab_in_it_is_typed(actions_on, chat, say, keyboard):
    say("I opened a new tab yesterday.")
    assert typed(chat) == ["I opened a new tab yesterday."]
    assert 29 not in keyboard.typed_keys()      # only the chat's own Enter


def test_off_new_tab_is_typed(chat, say, keyboard, monkeypatch):
    monkeypatch.setattr(live, "open_apps", False)
    say("New tab.")
    assert typed(chat) == ["New tab."] and 29 not in keyboard.typed_keys()


def test_literal_new_tab_types_the_words(actions_on, chat, say, keyboard):
    say("Literal new tab.")
    assert typed(chat) == ["New tab."] and 29 not in keyboard.typed_keys()


def test_close_quote_said_alone_is_still_punctuation(actions_on, monkeypatch):
    monkeypatch.setattr(live, "voice_navigation", True)
    assert voice_actions.run("close quote", False) is None     # left to the typing path


def test_esc_after_letting_go_presses_nothing(actions_on, chat, say, keyboard, monkeypatch):
    """Esc once the command is recognised, before its keys: nothing is pressed."""
    look = focused_window.look

    def look_then_esc():
        where = look()
        esc()
        return where
    monkeypatch.setattr(focused_window, "look", look_then_esc)
    say("New tab.")
    assert keyboard.events == []


def test_select_and_move_still_runs_before_new_commands(actions_on, chat, say, keyboard,
                                                        monkeypatch, caplog):
    import logging
    monkeypatch.setattr(live, "voice_navigation", True)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        say("Go to the top.")
    assert keyboard.typed_keys() == [29, 102]            # once, by select and move
    assert "Voice command: go to the top" in caplog.text


def test_a_key_command_forgets_the_last_dictation_for_undo(actions_on, chat, say, keyboard):
    say("Hello there.")
    say("Paste.")
    keyboard.events.clear()
    say("Delete that.")
    assert 14 not in keyboard.typed_keys()               # no Backspace


def test_a_key_command_mid_recording_ends_it(actions_on, recording_on, chat, keyboard):
    rec, phrase = recording_on
    phrase("new tab")
    assert keyboard.typed_keys() == CTRL_T
    assert rec.message.cancelled


def test_a_refused_command_mid_recording_keeps_it_going(actions_on, recording_on, chat,
                                                        keyboard):
    """"save" in a terminal has no key: the note is shown, the recording goes on."""
    rec, phrase = recording_on
    chat[0][0] = "org.kde.konsole"
    phrase("save")
    assert keyboard.typed_keys() == [] and not rec.message.cancelled


def test_a_command_that_names_nothing_is_typed(actions_on):
    assert voice_actions.run("Close the door.", False) is None
