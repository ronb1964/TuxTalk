"""Voice commands: which dictations are a command, and which command."""
import functools

import pytest

from tuxtalk import voice_navigation
from tuxtalk.app_commands import OpenRequest
from tuxtalk.command_phrases import (
    DESKTOP_ACTIONS, FRONT_WINDOW_ACTIONS, AddressRequest, DesktopRequest, KeyRequest,
    MusicRequest, SearchRequest, SoundRequest, SystemRequest, WindowRequest,
    literal_words, parse)

P = functools.partial(parse, navigation=True, actions=True)

@pytest.mark.parametrize("said", ["select the last word", "go to the end", "go to the top",
    "go to the bottom", "select all", "go to the start of the line", "go back 3 words",
    "next word", "unselect", "select the line"])
def test_select_and_move_keeps_its_phrases(said):
    assert isinstance(P(said), voice_navigation.Command)

def test_with_select_and_move_off_go_to_the_top_is_a_key():
    assert parse("Go to the top.", navigation=False, actions=True) == KeyRequest("go to the top")

@pytest.mark.parametrize("said", ["go to the end", "select the last word", "go back 3 words"])
def test_with_select_and_move_off_its_other_phrases_stay_dictation(said):
    assert parse(said, navigation=False, actions=True) is None

def test_actions_off_only_select_and_move_is_read():
    assert parse("new tab", navigation=True, actions=False) is None
    assert isinstance(parse("go to the end", navigation=True, actions=False),
                      voice_navigation.Command)

@pytest.mark.parametrize("said, name", [("New tab.", "new tab"), ("open a new tab", "new tab"),
    ("reopen closed tab", "reopen tab"), ("Refresh the page.", "reload"),
    ("please copy the selection", "copy"),
    ("press escape", "escape"), ("find on page", "find")])
def test_key_phrases(said, name):
    assert P(said) == KeyRequest(name)

@pytest.mark.parametrize("said, name", [("Copy selection.", "copy"), ("paste it", "paste"),
    ("Paste that.", "paste"), ("paste the clipboard", "paste"), ("Save the file.", "save"),
    ("save file", "save"), ("reload page", "reload")])
def test_key_phrases_with_a_noun(said, name):
    assert P(said) == KeyRequest(name)

@pytest.mark.parametrize("said", ["Copy.", "Paste.", "Save.", "Find.", "Reload.", "Refresh.",
                                  "Escape.", "copy that", "Copy that.", "Go back.",
                                  "Go forward.", "go back"])
def test_bare_key_words_are_dictation(said):
    assert P(said) is None

@pytest.mark.parametrize("said, name", [("Go back a page.", "go back"),
                                        ("go back one page", "go back"),
                                        ("Go forward a page.", "go forward")])
def test_the_page_commands_say_a_page(said, name):
    """"Go back." alone answers a question in a chat: the page needs saying."""
    assert P(said) == KeyRequest(name)

def test_scroll_counts():
    assert P("scroll down") == KeyRequest("scroll down")
    assert P("Scroll down 3.") == KeyRequest("scroll down", 3)
    assert P("scroll up three") == KeyRequest("scroll up", 3)
    assert P("scroll down 11") is None

def test_window_requests():
    assert P("Close Firefox.") == WindowRequest("close", OpenRequest("firefox"))
    assert P("close all Firefox") == WindowRequest("close", OpenRequest("firefox"), every=True)
    assert P("close all the Firefox") == WindowRequest("close", OpenRequest("the firefox"),
                                                       every=True)
    assert P("close Firefox Flatpak") == WindowRequest(
        "close", OpenRequest("firefox", install="flatpak"))
    assert P("close this window") == P("close window") == WindowRequest("close")
    assert P("Minimise the window.") == WindowRequest("minimize")
    assert P("maximize window") == WindowRequest("maximize")
    assert P("restore window") == P("unmaximize window") == WindowRequest("unmaximize")
    assert P("switch to Firefox") == WindowRequest("switch", OpenRequest("firefox"))
    assert P("close tab") == KeyRequest("close tab")


def test_minimize_maximize_and_restore_by_name():
    assert P("Minimize Firefox.") == WindowRequest("minimize", OpenRequest("firefox"))
    assert P("maximise Firefox") == WindowRequest("maximize", OpenRequest("firefox"))
    assert P("restore Firefox") == WindowRequest("restore", OpenRequest("firefox"))
    assert P("restore window") == WindowRequest("unmaximize")


@pytest.mark.parametrize("said", ["Close all.", "close every", "close all the"])
def test_close_all_naming_no_app_is_dictation(said):
    assert P(said) is None

def test_an_address_keeps_the_case_of_its_path():
    """A path or query may be case-sensitive (a reset link's token): only
    the scheme and the host are lowercased."""
    assert P("go to https://example.com/ResetTokenABC") == AddressRequest(
        "https://example.com/ResetTokenABC")
    assert P("Go to HTTPS://Example.COM/a?Key=XY") == AddressRequest(
        "https://example.com/a?Key=XY")

@pytest.mark.parametrize("said, url", [("go to youtube dot com", "https://youtube.com"),
    ("Go to YouTube.com.", "https://youtube.com"),
    ("go to www youtube dot com", "https://www.youtube.com"),
    ("open bbc dot co dot uk", "https://bbc.co.uk"),
    ("go to https://example.org", "https://example.org")])
def test_addresses(said, url):
    assert P(said) == AddressRequest(url)

@pytest.mark.parametrize("said", ["go to the store", "go to bed", "go to my.site",
    "go to version 2.0", "go to 2.0", "open source dot"])
def test_not_addresses(said):
    assert not isinstance(P(said), AddressRequest)

def test_web_search_keeps_the_words_as_said():
    assert P("web search cheap tires") == SearchRequest("cheap tires")
    assert P("Web search: Cheap tires near Hamilton.") == SearchRequest("Cheap tires near Hamilton")
    assert P("web search") is None and P("search for cheap tires") is None
    assert P("do a web search for tires") is None

def test_open_still_opens():
    assert P("open Firefox") == OpenRequest("firefox")
    assert P("open youtube.com") == AddressRequest("https://youtube.com")

EVERY_COMMAND = ["new tab", "close tab", "next tab", "previous tab", "reopen tab",
    "go back a page", "go forward a page", "reload the page", "refresh page", "scroll down",
    "page up", "page down",
    "find on page", "save the file", "copy the selection", "paste it", "press escape",
    "close Firefox", "close all Firefox",
    "close window",
    "switch to Firefox", "minimize window", "maximize window", "restore window",
    "go to youtube dot com", "web search cheap tires", "open Firefox",
    "snap left", "centre window", "show desktop", "next desktop", "switch to desktop two",
    "move window to the other screen", "volume up", "mute sound", "pause music",
    "next track", "lock screen", "take a screenshot", "take a screenshot of this window",
    "what can I say"]

@pytest.mark.parametrize("phrase", EVERY_COMMAND)
def test_every_command_inside_a_sentence_is_dictation(phrase):
    assert P(f"I said {phrase} yesterday") is None
    if not phrase.startswith("web search"):      # "web search" takes any words after it
        assert P(f"{phrase} and then we all went home early") is None

@pytest.mark.parametrize("said", ["Copy that.", "Save the date.", "Find out more.",
    "Go back home.", "Go to the store.", "Search for cheap tires.", "Paste it here.",
    "New tabs everywhere.", "Escape room tonight.", "Scroll down the page slowly please."])
def test_near_miss_sentences_are_dictation(said):
    assert P(said) is None

@pytest.mark.parametrize("said, name", [("Close the door.", "the door"),
                                        ("Switch to decaf.", "decaf")])
def test_close_and_switch_naming_no_app_are_left_to_app_matching(said, name):
    # The parser can't know the menu: these are window requests here, and
    # window_actions.run returns None when no app fits, so they're typed (Task 7).
    assert P(said).app == OpenRequest(name)

@pytest.mark.parametrize("said", ["close quote", "Close quote.", "close parenthesis",
    "close bracket", "close brace", "close paren"])
def test_spoken_punctuation_is_never_a_close(said):
    assert P(said) is None

def test_literal_types_the_words():
    lw = functools.partial(literal_words, navigation=True, actions=True)
    assert lw("literal new tab") == "new tab"
    assert lw("Literal open Firefox") == "open Firefox"
    assert lw("literal translations") == "literal translations"
    assert literal_words("literal new tab", navigation=True, actions=False) == "literal new tab"


@pytest.mark.parametrize("said, request_", [
    ("Snap left.", DesktopRequest("snap left")),
    ("snap window right", DesktopRequest("snap right")),
    ("snap the window to the top left", DesktopRequest("snap top left")),
    ("Move window to the bottom right.", DesktopRequest("snap bottom right")),
    ("move this window to the left", DesktopRequest("snap left")),
    ("Snap window to top.", DesktopRequest("snap top")),
    ("snap to left", DesktopRequest("snap left")),
    ("Move window to left.", DesktopRequest("snap left")),
    ("Centre window.", DesktopRequest("center")),
    ("center the window", DesktopRequest("center")),
    ("Show desktop.", DesktopRequest("show desktop")),
    ("show the desktop", DesktopRequest("show desktop")),
    ("Show all windows.", DesktopRequest("overview")),
    ("show windows", DesktopRequest("overview")),
    ("Next desktop.", DesktopRequest("next desktop")),
    ("go to the previous desktop", DesktopRequest("previous desktop")),
    ("Switch to desktop two.", DesktopRequest("desktop", 2)),
    ("go to desktop 3", DesktopRequest("desktop", 3)),
    ("Move window to desktop two.", DesktopRequest("to desktop", 2)),
    ("move window to the next desktop", DesktopRequest("to next desktop")),
    ("move the window to the other screen", DesktopRequest("to next screen")),
    ("Volume up.", SoundRequest("volume up")),
    ("volume down three", SoundRequest("volume down", 3)),
    ("turn the volume up 2", SoundRequest("volume up", 2)),
    ("turn down the volume", SoundRequest("volume down")),
    ("Mute the sound.", SoundRequest("mute")),
    ("unmute sound", SoundRequest("unmute")),
    ("Pause music.", MusicRequest("pause")),
    ("resume the music", MusicRequest("play")),
    ("play music", MusicRequest("play")),
    ("Next song.", MusicRequest("next")),
    ("previous track", MusicRequest("previous")),
    ("Lock screen.", SystemRequest("lock")),
    ("lock the computer", SystemRequest("lock")),
    ("Take a screenshot.", SystemRequest("screenshot")),
    ("take screen shot", SystemRequest("screenshot")),
    ("Take a screenshot of this window.", SystemRequest("window screenshot")),
    ("take a screenshot of the window", SystemRequest("window screenshot")),
    ("Screenshot this window.", SystemRequest("window screenshot")),
    ("take a window screenshot", SystemRequest("window screenshot")),
    ("What can I say?", SystemRequest("commands")),
    ("show voice commands", SystemRequest("commands"))])
def test_desktop_sound_music_and_system_phrases(said, request_):
    assert P(said) == request_

@pytest.mark.parametrize("said", ["switch to desktop 10", "go to desktop zero",
    "Switch to desktop ten.", "move window to desktop 12", "volume up 11",
    "switch to desktop eleven", "switch to desktop twenty", "go to desktop twelve",
    "move window to desktop eleven"])
def test_switch_to_desktop_with_another_number_is_never_an_app(said):
    assert P(said) is None

@pytest.mark.parametrize("said", ["Pause.", "Play.", "Stop.", "Next.", "Back.", "Mute.", "Up.",
    "Down.", "Left.", "Right.", "Lock.", "Lock it.", "Show me the desktop.", "Volume is too loud.",
    "Snap.", "Take a break.", "Show me the windows.", "Move the window later.",
    "Next song is mine.", "Pause the movie.", "Lock the door.", "Play some music."])
def test_near_miss_sentences_stay_dictation(said):
    assert P(said) is None

@pytest.mark.parametrize("said", ["Go to desktop.com.", "go to desktop dot com"])
def test_an_address_that_starts_with_desktop_is_an_address(said):
    assert P(said) == AddressRequest("https://desktop.com")

def test_select_and_move_keeps_its_go_to_phrases():
    assert isinstance(P("go to the top"), voice_navigation.Command)
    assert P("go to desktop two") == DesktopRequest("desktop", 2)


def test_desktop_too_is_desktop_two():
    """Parakeet often writes a "two" said alone as "too" (heard in every VM)."""
    assert P("go to desktop too") == DesktopRequest("desktop", 2)
    assert P("move window to desktop too") == DesktopRequest("to desktop", 2)


@pytest.mark.parametrize("said", [
    "I want to go to desktop too", "move the window to the desktop too", "I like that too",
    "go to desktop too and stay there", "volume up too", "scroll down too"])
def test_too_in_a_sentence_stays_words(said):
    assert P(said) is None


def test_every_desktop_action_the_parser_makes_is_listed():
    made = {P(s).action for s in ("snap left", "snap top right", "centre window", "show desktop",
            "show all windows", "next desktop", "previous desktop", "switch to desktop 2",
            "move window to desktop 2", "move window to the next desktop",
            "move window to the previous desktop", "move window to the other screen")}
    assert made <= DESKTOP_ACTIONS and FRONT_WINDOW_ACTIONS < DESKTOP_ACTIONS

def test_literal_types_a_desktop_command():
    assert literal_words("literal snap left", navigation=True, actions=True) == "snap left"
