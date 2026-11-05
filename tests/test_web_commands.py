"""Addresses and web searches by voice (TuxTalk Labs): "go to youtube.com"
and "web search cheap tires" go into the browser's address bar, or to the
user's default browser. The window, the browser and the clock are stand-ins
and the keyboard records: no test opens an address, starts a browser or
presses a real key."""
import logging
import types

import pytest

from conftest import FakeClock
from tuxtalk import (app_finder, app_focus, app_launcher, focused_window, keyboard_output,
                     screen_message, voice_actions, web_commands, window_classes)
from tuxtalk.app_finder import AppEntry
from tuxtalk.command_phrases import AddressRequest, SearchRequest

FIREFOX = ("firefox", ("tray", "1"))
KWRITE = ("org.kde.kwrite", ("tray", "2"))
FIREFOX_ENTRY = AppEntry("firefox.desktop", "Firefox", "Firefox", "Web Browser", "firefox", "")
BRAVE = ("brave-browser", ("tray", "3"))
# A Flatpak browser on no list of TuxTalk's: known as the default browser only.
VIVALDI = ("com.vivaldi.Vivaldi", ("tray", "4"))
VIVALDI_ENTRY = AppEntry("com.vivaldi.Vivaldi.desktop", "Vivaldi", "Vivaldi", "Web Browser",
                         "vivaldi", "flatpak")
KWRITE_ENTRY = AppEntry("org.kde.kwrite.desktop", "KWrite", "KWrite", "Text Editor", "kwrite", "")

CTRL, ENTER, DELETE = 29, 28, 111
CTRL_L = [CTRL, keyboard_output.nav_keycode("L")]
CTRL_T = [CTRL, keyboard_output.nav_keycode("T")]


class BoundedClock(FakeClock):
    """FakeClock that fails the test once far more time has passed than any
    wait may take, so a wait that never ends fails instead of hanging."""

    def sleep(self, seconds):
        super().sleep(seconds)
        if self.now > 5 * web_commands.BROWSER_WAIT_S:
            pytest.fail("the wait for the browser never ended")


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """web_commands' time: its waits pass at once."""
    fake = BoundedClock()
    monkeypatch.setattr(web_commands, "time", fake)
    return fake


@pytest.fixture(autouse=True)
def menu(monkeypatch):
    """Firefox and KWrite in the menu, Firefox the default browser: never the
    computer's own, which would make a test depend on the user's browser."""
    monkeypatch.setattr(app_finder, "installed_apps", lambda: [FIREFOX_ENTRY, KWRITE_ENTRY])
    monkeypatch.setattr(app_finder, "default_apps", lambda: {"browser": "firefox.desktop"})


@pytest.fixture
def focus(monkeypatch):
    """The looks the focused window gives, in turn: the last one stays."""
    looks = []

    def look(*_args, **_kwargs):
        return looks.pop(0) if len(looks) > 1 else looks[0]
    monkeypatch.setattr(focused_window, "look", look)
    return looks


@pytest.fixture
def at_firefox(focus):
    focus[:] = [FIREFOX]


@pytest.fixture
def at_kwrite(focus):
    focus[:] = [KWRITE]


@pytest.fixture
def typed(monkeypatch):
    """The texts typed, in order."""
    texts = []
    monkeypatch.setattr(keyboard_output, "type_text", lambda text: texts.append(text) or True)
    return texts


@pytest.fixture
def opened_uris(monkeypatch):
    """The addresses handed to the default browser."""
    uris = []
    monkeypatch.setattr(app_launcher, "open_uri", lambda url: uris.append(url) or True)
    return uris


@pytest.fixture
def floated(monkeypatch):
    """The labels floated over the active window, in order."""
    labels = []
    monkeypatch.setattr(screen_message, "show_over_active_window",
                        lambda text, *a, **k: labels.append(text))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    return labels


@pytest.fixture
def browser(monkeypatch, focus):
    """Firefox, the default browser (menu). *raised* (focus_running's answer) and
    *started* (each launch) say what was done to it; *arrives* is the looks
    the focus goes through once it was raised or started, FIREFOX by default."""
    state = types.SimpleNamespace(running=True, starts=True, started=[], arrives=[FIREFOX])

    def focus_running(entry, *_args, **_kwargs):
        if state.running:
            focus[:] = state.arrives
        return state.running

    def launch(entry):
        state.started.append(entry.app_id)
        if state.starts:
            focus[:] = state.arrives
        return state.starts
    monkeypatch.setattr(app_focus, "focus_running", focus_running)
    monkeypatch.setattr(app_launcher, "launch", launch)
    return state


def test_in_a_browser_go_to_types_the_address_in_its_bar(at_firefox, typed, keyboard):
    assert web_commands.go_to(AddressRequest("https://youtube.com"), FIREFOX, False) is True
    assert typed == ["https://youtube.com"]
    assert keyboard.typed_keys() == CTRL_L + [DELETE, ENTER]


def test_go_to_presses_delete_before_enter(at_firefox, typed, keyboard):
    """The browser's inline autofill can stretch a whole address to a longer
    one it remembers: Delete takes that away, and does nothing after a
    complete one."""
    web_commands.go_to(AddressRequest("https://youtube.com/watch"), FIREFOX, False)
    assert keyboard.typed_keys()[-2:] == [DELETE, ENTER]


def test_outside_a_browser_go_to_opens_the_default_browser(at_kwrite, opened_uris, keyboard):
    assert web_commands.go_to(AddressRequest("https://youtube.com"), KWRITE, False) is True
    assert opened_uris == ["https://youtube.com"] and keyboard.events == []


def test_an_address_the_desktop_couldnt_open_says_so(at_kwrite, floated, keyboard,
                                                    monkeypatch):
    monkeypatch.setattr(app_launcher, "open_uri", lambda url: False)
    assert web_commands.go_to(AddressRequest("https://youtube.com"), KWRITE, False) is False
    assert floated == ["Couldn't open that address"] and keyboard.events == []


def test_the_address_and_the_words_never_reach_the_log(focus, typed, opened_uris, browser,
                                                      floated, caplog):
    with caplog.at_level(logging.DEBUG):
        focus[:] = [FIREFOX]
        web_commands.go_to(AddressRequest("https://youtube.com"), FIREFOX, False)
        web_commands.search(SearchRequest("cheap tires"), FIREFOX, False)
        focus[:] = [KWRITE]
        web_commands.go_to(AddressRequest("https://youtube.com"), KWRITE, False)
        web_commands.search(SearchRequest("cheap tires"), KWRITE, False)
    assert "Voice command: go to an address" in caplog.text
    assert "Voice command: web search" in caplog.text
    assert "youtube" not in caplog.text.lower() and "tires" not in caplog.text.lower()


def test_web_search_in_a_browser_types_the_words_in_its_bar(at_firefox, typed, keyboard):
    assert web_commands.search(SearchRequest("cheap tires"), FIREFOX, False) is True
    assert typed == ["cheap tires"]
    assert keyboard.typed_keys() == CTRL_L + [DELETE, ENTER]   # Delete: the browser's autofill


def test_web_search_elsewhere_brings_the_browser_forward_then_opens_a_tab(
        at_kwrite, browser, typed, keyboard):
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is True
    assert browser.started == []
    assert keyboard.typed_keys() == CTRL_T + [DELETE, ENTER]
    assert typed == ["cheap tires"]


def test_web_search_starts_the_browser_when_it_isnt_running(at_kwrite, browser, typed,
                                                           keyboard, clock):
    browser.running = False
    browser.arrives = [KWRITE, KWRITE, FIREFOX]    # the focus arrives on the 3rd poll
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is True
    assert browser.started == ["firefox.desktop"]
    # Two polls slept through, then the pause for the new tab.
    assert clock.now == pytest.approx(2 * web_commands.POLL_S + web_commands.OPENER_SETTLE_S)
    assert keyboard.typed_keys() == CTRL_T + [DELETE, ENTER] and typed == ["cheap tires"]


def test_a_browser_that_never_comes_forward_types_nothing(at_kwrite, browser, typed, keyboard,
                                                         floated, clock):
    browser.arrives = [KWRITE]
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is False
    assert clock.now >= web_commands.BROWSER_WAIT_S
    assert typed == [] and keyboard.events == []
    assert floated == ["The browser didn't come forward"]


def test_another_browser_taking_the_focus_gets_no_search(at_kwrite, browser, typed, keyboard,
                                                         floated):
    """Firefox is the default: Brave coming forward meanwhile isn't it, and
    the words never go into Brave."""
    browser.arrives = [BRAVE]
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is False
    assert typed == [] and keyboard.events == []
    assert floated == ["The browser didn't come forward"]


def test_a_browser_that_had_to_start_is_given_longer(at_kwrite, browser, typed, keyboard,
                                                     floated, clock):
    browser.running = False
    browser.arrives = [KWRITE]
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is False
    assert clock.now >= web_commands.BROWSER_START_WAIT_S > web_commands.BROWSER_WAIT_S
    assert typed == [] and floated == ["The browser didn't come forward"]


@pytest.fixture
def vivaldi_default(browser, monkeypatch):
    """The Flatpak Vivaldi as the default browser."""
    monkeypatch.setattr(app_finder, "installed_apps", lambda: [VIVALDI_ENTRY, KWRITE_ENTRY])
    monkeypatch.setattr(app_finder, "default_apps",
                        lambda: {"browser": "com.vivaldi.Vivaldi.desktop"})
    browser.arrives = [VIVALDI]
    return browser


def test_a_default_browser_on_no_list_is_searched_in(at_kwrite, vivaldi_default, typed,
                                                     keyboard):
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is True
    assert keyboard.typed_keys() == CTRL_T + [DELETE, ENTER] and typed == ["cheap tires"]


def test_go_to_in_a_default_browser_on_no_list_uses_its_bar(focus, vivaldi_default, typed,
                                                            opened_uris, keyboard):
    focus[:] = [VIVALDI]
    assert web_commands.go_to(AddressRequest("https://youtube.com"), VIVALDI, False) is True
    assert opened_uris == [] and typed == ["https://youtube.com"]
    assert keyboard.typed_keys()[:2] == CTRL_L


# Firefox installed twice: Flathub's entry says its windows' class is
# "firefox", the distro's own windows' class too.
FLATPAK_FIREFOX = AppEntry("org.mozilla.firefox.desktop", "Firefox", "Firefox", "Web Browser",
                           "firefox", "flatpak", wm_class="firefox")
SNAP_FIREFOX = AppEntry("firefox_firefox.desktop", "Firefox", "Firefox", "Web Browser",
                        "firefox", "snap", wm_class="firefox_firefox")


def test_two_copies_whose_windows_look_alike_get_no_search(at_kwrite, browser, typed, keyboard,
                                                          floated, monkeypatch):
    """The distro's Firefox is the default and Flathub's is installed too:
    a window of class "firefox" may be either, so the words never go into
    one, and the note says so at once instead of after a wait."""
    monkeypatch.setattr(app_finder, "installed_apps",
                        lambda: [FIREFOX_ENTRY, FLATPAK_FIREFOX, KWRITE_ENTRY])
    raised = []
    monkeypatch.setattr(app_focus, "focus_running", lambda *a, **k: raised.append(1))
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is False
    assert typed == [] and keyboard.events == [] and raised == []
    assert floated == ["Two copies of your browser"]


def test_the_default_copys_own_window_class_is_waited_for(at_kwrite, browser, typed, keyboard,
                                                         monkeypatch):
    """Flathub's Firefox is the default, the Snap is installed too: on X11
    the Flatpak's windows have the class its entry names, "firefox"."""
    monkeypatch.setattr(app_finder, "installed_apps",
                        lambda: [FLATPAK_FIREFOX, SNAP_FIREFOX, KWRITE_ENTRY])
    monkeypatch.setattr(app_finder, "default_apps",
                        lambda: {"browser": "org.mozilla.firefox.desktop"})
    browser.arrives = [("firefox", ("x11", "7"))]
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is True
    assert typed == ["cheap tires"]


def test_another_copys_window_isnt_the_default(at_kwrite, browser, typed, keyboard, floated,
                                              monkeypatch):
    """The Snap's window comes forward while the Flatpak is the default."""
    monkeypatch.setattr(app_finder, "installed_apps",
                        lambda: [FLATPAK_FIREFOX, SNAP_FIREFOX, KWRITE_ENTRY])
    monkeypatch.setattr(app_finder, "default_apps",
                        lambda: {"browser": "org.mozilla.firefox.desktop"})
    browser.arrives = [("firefox_firefox", ("x11", "8"))]
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is False
    assert typed == [] and floated == ["The browser didn't come forward"]


def test_esc_while_waiting_for_the_browser_types_nothing(at_kwrite, browser, typed, keyboard,
                                                        floated, monkeypatch, focus):
    browser.arrives = [KWRITE]
    message = types.SimpleNamespace(cancelled=False)
    polls = []

    def look(*_args, **_kwargs):
        polls.append(1)
        if len(polls) == 2:
            message.cancelled = True        # Esc on the 2nd poll
        return KWRITE
    monkeypatch.setattr(focused_window, "look", look)
    with keyboard_output.until_cancelled(message):
        assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is False
    assert len(polls) == 2
    assert typed == [] and keyboard.events == [] and floated == []


def test_a_browser_that_doesnt_start_says_so(at_kwrite, browser, typed, keyboard, floated,
                                            clock):
    browser.running = browser.starts = False
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is False
    assert floated == ["Your browser didn't start"] and clock.now == 0
    assert typed == [] and keyboard.events == []


def test_a_search_where_the_desktop_names_no_window_says_so_at_once(focus, browser, typed,
                                                                   keyboard, floated, clock):
    unknown = (None, None)
    focus[:] = [unknown]
    assert web_commands.search(SearchRequest("cheap tires"), unknown, False) is False
    assert floated == ["Couldn't tell which window"] and clock.now == 0
    assert browser.started == [] and typed == [] and keyboard.events == []


def test_with_no_default_browser_says_so(at_kwrite, browser, typed, keyboard, floated,
                                         monkeypatch):
    monkeypatch.setattr(app_finder, "default_apps", lambda: {})
    assert web_commands.search(SearchRequest("cheap tires"), KWRITE, False) is False
    assert floated == ["No web browser is set up"]
    assert typed == [] and keyboard.events == [] and browser.started == []


def test_focus_moving_away_during_the_address_stops_the_typing(focus, at_firefox, keyboard,
                                                              floated, monkeypatch):
    def type_then_focus_moves(text):
        focus[:] = [KWRITE]
        return True
    monkeypatch.setattr(keyboard_output, "type_text", type_then_focus_moves)
    assert web_commands.go_to(AddressRequest("https://youtube.com"), FIREFOX, False) is False
    assert keyboard.typed_keys() == CTRL_L          # no Enter into KWrite
    assert floated == ["Window changed, nothing done"]


def test_a_partial_address_typed_before_the_focus_moved_says_so(at_firefox, keyboard, floated,
                                                                monkeypatch):
    """The keyboard stopped partway through the address: no Enter after it."""
    from tuxtalk import virtual_keyboard

    def stopped_partway(text):
        raise virtual_keyboard.StoppedPartway("5 chars", "moved")
    monkeypatch.setattr(keyboard_output, "type_text", stopped_partway)
    assert web_commands.go_to(AddressRequest("https://youtube.com"), FIREFOX, False) is False
    assert keyboard.typed_keys() == CTRL_L
    assert floated == ["Window changed, nothing done"]


@pytest.mark.parametrize("cls, browser", [("firefox", True), ("Firefox", True),
                                          ("brave-browser", True), ("org.mozilla.firefox", True),
                                          ("org.kde.kwrite", False), (None, False)])
def test_browsers_are_known_by_their_class(cls, browser):
    assert window_classes.is_browser_class(cls) is browser


def test_go_to_an_address_said_alone_types_nothing_else(actions_on, chat, say, keyboard):
    say("Go to youtube dot com.")
    keys = keyboard.typed_keys()
    assert chat[2] == []                        # nothing typed as dictation
    assert keys[:2] == CTRL_L and keys[-1] == ENTER and keys.count(ENTER) == 1


def test_an_address_said_without_https_is_typed_with_it(actions_on, chat, say, keyboard,
                                                      monkeypatch):
    texts = []
    monkeypatch.setattr(keyboard_output, "type_text", lambda text: texts.append(text) or True)
    say("Go to youtube dot com.")
    assert texts == ["https://youtube.com"]


@pytest.mark.parametrize("done", [True, False])
def test_only_a_search_that_was_done_ends_the_type_at_pause_recording(actions_on, monkeypatch,
                                                                       done):
    monkeypatch.setattr(web_commands, "search", lambda *a: done)
    ongoing = types.SimpleNamespace(cancelled=False)
    assert voice_actions.run("web search cheap tires", False, ongoing) is done
    assert ongoing.cancelled is done
