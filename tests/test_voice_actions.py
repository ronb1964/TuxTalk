"""Voice commands (TuxTalk Labs) through the dispatcher: "new tab" said alone
presses Ctrl+T and types nothing, while a sentence with the words in it is
typed. The keyboard records, so no test presses a real key. The actions_on and
say fixtures are voice_command_rig.py's."""
import logging

import pytest

from conftest import esc
from type_at_pause_rig import service_tick_runs
from tuxtalk import everyday_keys, focused_window, keyboard_output, voice_actions
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs

ENTER = 28      # the chat's own automatic send
CTRL_T = [29, keyboard_output.nav_keycode("T")]


def typed(chat):
    return [text for text, _ in chat[2]]


def test_new_tab_said_alone_presses_ctrl_t_and_types_nothing(actions_on, chat, say, keyboard):
    say("New tab.")
    assert chat[2] == [] and keyboard.typed_keys() == CTRL_T


def test_a_sentence_with_new_tab_in_it_is_typed(actions_on, chat, say, keyboard):
    say("I opened a new tab yesterday.")
    assert typed(chat) == ["I opened a new tab yesterday."]
    assert keyboard.typed_keys() == [ENTER]


def test_off_new_tab_is_typed(chat, say, keyboard, monkeypatch):
    monkeypatch.setattr(live, "open_apps", False)
    say("New tab.")
    assert typed(chat) == ["New tab."] and keyboard.typed_keys() == [ENTER]


def test_literal_new_tab_types_the_words(actions_on, chat, say, keyboard):
    say("Literal new tab.")
    assert typed(chat) == ["New tab."] and keyboard.typed_keys() == [ENTER]


def test_literal_before_a_select_and_move_phrase_types_the_words(actions_on, chat, say,
                                                                  keyboard, monkeypatch):
    """"literal go to the end" with select and move on: the words, no key."""
    monkeypatch.setattr(live, "voice_navigation", True)
    say("Literal go to the end.")
    assert typed(chat) == ["Go to the end."] and keyboard.typed_keys() == [ENTER]


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
    keys = []
    monkeypatch.setattr(everyday_keys, "press", lambda *a: keys.append(a))
    monkeypatch.setattr(live, "voice_navigation", True)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        say("Go to the top.")
    assert keyboard.typed_keys() == [29, 102] and keys == []     # by select and move
    assert "Voice command: go to the top" in caplog.text


@pytest.mark.parametrize("command", ["Paste it.", "Select all.", "Go back."])
def test_a_key_command_forgets_the_last_dictation_for_undo(actions_on, chat, say, keyboard,
                                                           monkeypatch, command):
    """What the keys did leaves nothing of the dictation to erase."""
    monkeypatch.setattr(live, "press_enter_after", False)
    say("Hello there.")
    say(command)
    say("Delete last sentence.")
    assert 14 not in keyboard.typed_keys()               # no Backspace
    assert typed(chat) == ["Hello there. "]


def test_a_key_command_mid_recording_ends_it(actions_on, recording_on, chat, keyboard):
    rec, phrase = recording_on
    phrase("new tab")
    assert keyboard.typed_keys() == CTRL_T
    service_tick_runs()
    assert rec.message.cancelled and rs.state.is_recording is False


@pytest.mark.parametrize("said", ["Paste.", "Copy.", "Save."])
def test_a_bare_key_word_is_typed(actions_on, chat, say, keyboard, said):
    say(said)
    assert typed(chat) == [said] and 29 not in keyboard.typed_keys()


def test_a_refused_command_mid_recording_keeps_it_going(actions_on, recording_on, chat,
                                                        keyboard):
    """"save" in a terminal has no key: the note is shown, the recording goes on."""
    rec, phrase = recording_on
    chat[0][0] = "org.kde.konsole"
    phrase("save the file")
    service_tick_runs()
    assert keyboard.typed_keys() == [] and chat[2] == []
    assert actions_on and not rec.message.cancelled and rs.state.is_recording is True


def test_a_command_that_names_nothing_is_typed(actions_on):
    assert voice_actions.run("Close the door.", False) is None
