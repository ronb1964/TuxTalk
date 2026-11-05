"""Voice commands: which dictations are a command, and which command."""
import functools

import pytest

from tuxtalk import voice_navigation
from tuxtalk.app_commands import OpenRequest
from tuxtalk.command_phrases import (
    AddressRequest, KeyRequest, SearchRequest, WindowRequest,
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
    ("reopen closed tab", "reopen tab"), ("Refresh.", "reload"), ("please copy", "copy"),
    ("press escape", "escape"), ("find on page", "find")])
def test_key_phrases(said, name):
    assert P(said) == KeyRequest(name)

def test_scroll_counts():
    assert P("scroll down") == KeyRequest("scroll down")
    assert P("Scroll down 3.") == KeyRequest("scroll down", 3)
    assert P("scroll up three") == KeyRequest("scroll up", 3)
    assert P("scroll down 11") is None

def test_window_requests():
    assert P("Close Firefox.") == WindowRequest("close", OpenRequest("firefox"))
    assert P("close all Firefox") == WindowRequest("close", OpenRequest("firefox"), every=True)
    assert P("close Firefox Flatpak") == WindowRequest(
        "close", OpenRequest("firefox", install="flatpak"))
    assert P("close this window") == P("close window") == WindowRequest("close")
    assert P("Minimise the window.") == WindowRequest("minimize")
    assert P("maximize window") == WindowRequest("maximize")
    assert P("restore window") == P("unmaximize window") == WindowRequest("unmaximize")
    assert P("switch to Firefox") == WindowRequest("switch", OpenRequest("firefox"))
    assert P("close tab") == KeyRequest("close tab")

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

EVERY_COMMAND = ["new tab", "close tab", "next tab", "previous tab", "reopen tab", "go back",
    "go forward", "reload", "refresh", "scroll down", "page up", "page down", "find", "save",
    "copy", "paste", "escape", "close Firefox", "close all Firefox", "close window",
    "switch to Firefox", "minimize window", "maximize window", "restore window",
    "go to youtube dot com", "web search cheap tires", "open Firefox"]

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
