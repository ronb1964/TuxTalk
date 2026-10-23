"""Ending a dictation with "press enter" sends a real Enter (key_commands.py).

Ron, 2026-10-17: "I really like the press enter, so you can just speak."
A spoken "new line" stays Shift+Enter, which never sends a chat message.
"""
import numpy as np
import pytest

from tuxtalk import app, config, key_commands
from tuxtalk.key_commands import split_key_command


@pytest.mark.parametrize("raw, expected", [
    ("See you at noon, press enter", ("See you at noon", True)),
    ("See you at noon. Press Enter.", ("See you at noon.", True)),
    ("What time is it? Press enter", ("What time is it?", True)),
    ("Thanks! Press return.", ("Thanks!", True)),
    ("git status press enter", ("git status", True)),
    ("Hit the enter key!", ("", True)),
    ("Press enter.", ("", True)),
    # Only the very end counts.
    ("press enter to continue the install", ("press enter to continue the install", False)),
    ("I like it", ("I like it", False)),
    ("", ("", False)),
    # "literal" types the words, like "literal new line".
    ("Type literal press enter", ("Type press enter", False)),
])
def test_split_press_enter(raw, expected):
    words, said = expected
    assert split_key_command(raw) == (words, "ENTER" if said else "")


def _dictate(monkeypatch, raw, prepared=None, outcome="ok", window="brave-browser"):
    """Run one dictation through _transcribe_and_inject; what was typed and
    whether Enter was pressed, in order."""
    events = []
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, language: raw)
    if prepared is not None:
        monkeypatch.setattr(app, "_prepare_text", lambda r, *a, **k: prepared(r))
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_handle_undo", lambda *a, **k: False)
    monkeypatch.setattr(app, "_handle_navigation", lambda *a, **k: False)
    monkeypatch.setattr(app, "_handle_fix_word", lambda *a, **k: False)
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: window)
    monkeypatch.setattr(app, "_report_undelivered", lambda *a, **k: events.append("not delivered"))

    def inject(text, mode, t0):
        events.append(("typed", text))
        return outcome
    monkeypatch.setattr(app, "_inject_text", inject)
    monkeypatch.setattr(app, "_press_key", lambda key, why: events.append((key.lower(), why)))
    frames = [(np.ones(app.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, beeps_on=False,
                               smart_quotes=False, notify_on=False)
    return events


def test_press_enter_types_the_words_then_presses_enter(monkeypatch):
    monkeypatch.setattr(app, "_press_enter_after", False)
    events = _dictate(monkeypatch, "See you at noon, press enter",
                      prepared=lambda r: r + ". ")
    # No auto-space before Enter: it would be sent with the message.
    assert events == [("typed", "See you at noon."), ("enter", 'said "press enter"')]


def test_press_enter_alone_only_presses_enter(monkeypatch):
    monkeypatch.setattr(app, "_press_enter_after", False)
    events = _dictate(monkeypatch, "Press enter.")
    assert events == [("enter", 'said "press enter"')]


def test_no_enter_without_the_command(monkeypatch):
    monkeypatch.setattr(app, "_press_enter_after", False)
    events = _dictate(monkeypatch, "Hello there", prepared=lambda r: r + ". ")
    assert events == [("typed", "Hello there. ")]


def test_press_enter_after_each_dictation(monkeypatch):
    monkeypatch.setattr(app, "_press_enter_after", True)
    events = _dictate(monkeypatch, "Hello there", prepared=lambda r: r + ". ")
    assert events == [("typed", "Hello there."),
                      ("enter", "Send each dictation automatically is on")]


def test_a_cut_off_dictation_is_typed_but_not_sent(monkeypatch):
    """Letting go of the key after "and" means the sentence wasn't finished;
    sending half a message can't be taken back."""
    monkeypatch.setattr(app, "_press_enter_after", True)
    events = _dictate(monkeypatch, "You can go ahead and add that. And",
                      prepared=lambda r: r + ". ")
    # No period after "And" either: the sentence isn't over, and what's said
    # next carries it on in lowercase.
    assert events == [("typed", "You can go ahead and add that. And ")]
    assert app.state.continue_mid_sentence is True
    app.state.continue_mid_sentence = False


def test_a_cut_off_dictation_gets_no_period_in_a_text_editor_either(monkeypatch):
    """Ron, 2026-10-20: KWrite got "Meet me at noon and." The text-editor rule
    turned the send off before the "and" was looked at."""
    monkeypatch.setattr(app, "_press_enter_after", True)
    events = _dictate(monkeypatch, "Meet me at noon and", prepared=lambda r: r + ". ",
                      window="org.kde.kwrite")
    assert events == [("typed", "Meet me at noon and ")]
    assert app.state.continue_mid_sentence is True
    app.state.continue_mid_sentence = False


@pytest.mark.parametrize("window, typed", [
    ("org.kde.kwrite", "this is where it goes on."),
    ("com.anthropic.Claude", "This is where it goes on."),
])
def test_a_cut_off_sentence_goes_on_in_lowercase_only_in_its_own_window(monkeypatch, window,
                                                                         typed):
    """Ron, 2026-10-20: "meet me at noon and" in KWrite, then a message in
    another window began with a small letter."""
    monkeypatch.setattr(app, "_press_enter_after", False)
    monkeypatch.setattr(app.state, "continue_mid_sentence", True)
    monkeypatch.setattr(app.state, "last_inserted_window", "org.kde.kwrite")
    events = _dictate(monkeypatch, "This is where it goes on", window=window)
    assert events[0][1].strip() == typed


def test_saying_press_enter_sends_even_after_and(monkeypatch):
    monkeypatch.setattr(app, "_press_enter_after", True)
    events = _dictate(monkeypatch, "Bring chips and, press enter",
                      prepared=lambda r: r + ".")
    assert ("enter", 'said "press enter"') in events


@pytest.mark.parametrize("text, word", [
    ("You can add that. And.", "and"),
    ("Grab the", "the"),
    ("Meet me with, um", "um"),
    ("\u201cI said and\u201d", "and"),
])
def test_cut_off_endings(text, word):
    assert key_commands.ends_unfinished(text) == word


@pytest.mark.parametrize("text", [
    "I think so.", "I'd love to.", "I like that.", "See you then.",
    "Sounds good!", "", "Command and conquer is a game.",
])
def test_whole_sentences_are_not_cut_off(text):
    assert key_commands.ends_unfinished(text) == ""


def test_no_enter_when_the_text_did_not_arrive(monkeypatch):
    """Enter on a half-typed message would send the wrong thing."""
    monkeypatch.setattr(app, "_press_enter_after", False)
    events = _dictate(monkeypatch, "See you at noon, press enter",
                      prepared=lambda r: r + ".", outcome="partial")
    assert ("enter", 'said "press enter"') not in events
    assert "not delivered" in events


def test_press_enter_key_uses_the_output_backend_and_forgets_undo(monkeypatch):
    pressed = []

    class Backend:
        def press_keys(self, chords):
            pressed.append(chords)
            return True
    monkeypatch.setattr(app, "_output_backend", lambda: Backend())
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    app.state.last_inserted_text = "See you at noon."
    assert app._press_key("ENTER", "test") is True
    assert pressed == [[((), "ENTER")]]
    # A sent message can't be backspaced away.
    assert app.state.last_inserted_text == ""


def test_both_typing_paths_know_the_enter_key():
    from tuxtalk import libei_ctypes
    assert app._nav_keycode("ENTER") == 28
    assert libei_ctypes._CHORD_KEYS["ENTER"] == 28


def test_setting_is_off_by_default_and_applies_live():
    assert config.Settings().press_enter_after is False
    assert "press_enter_after" in config.LIVE_APPLIED_KEYS
    cfg = config.Settings(press_enter_after=True)
    app._apply_cleanup_settings(cfg)
    try:
        assert app._press_enter_after is True
    finally:
        app._apply_cleanup_settings(config.Settings())
    assert app._press_enter_after is False


# --- The wait before an automatic send, and holding it (Ron, 2026-10-18) ---

class FakeTimer:
    """threading.Timer that fires only when the test says so."""
    made = []

    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args, self.cancelled = delay, fn, args, False
        FakeTimer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn(*self.args)


TYPED = []   # what the countdown fixture's _inject_text was given


@pytest.fixture
def countdown(monkeypatch):
    FakeTimer.made = []
    monkeypatch.setattr(app.threading, "Timer", FakeTimer)
    monkeypatch.setattr(app, "_press_enter_delay", 2.0)
    pressed, labels = [], []
    monkeypatch.setattr(app, "_press_key", lambda key, why: pressed.append(why) or True)
    from tuxtalk import screen_message
    monkeypatch.setattr(screen_message, "show_over_active_window",
                        lambda text, **kw: labels.append(text))
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "brave-browser")
    monkeypatch.setattr(app.state, "last_inserted_window", "brave-browser")
    TYPED.clear()
    monkeypatch.setattr(app, "_inject_text", lambda text, mode, t0: TYPED.append(text) or "ok")
    return pressed, labels


def test_a_send_waits_and_says_how_to_hold_it(countdown, monkeypatch):
    pressed, labels = countdown
    monkeypatch.setattr(app, "_hold_key_name", "F8")
    app._send_automatically("test")
    assert pressed == []
    assert labels == ["Sending in 2 s. Tap F8 to hold it."]
    assert sends()[0].delay == 2.0
    sends()[0].fire()
    assert pressed == ["test"]
    assert app.state.pending_send is None


def test_right_away_sends_at_once(countdown, monkeypatch):
    pressed, labels = countdown
    monkeypatch.setattr(app, "_press_enter_delay", 0.0)
    app._send_automatically("test")
    assert pressed == ["test"] and labels == [] and FakeTimer.made == []


def test_a_tap_of_the_toggle_key_holds_the_send(countdown):
    pressed, labels = countdown
    app._send_automatically("test")
    assert app._hold_pending_send(announce=True) is True
    sends()[0].fire()
    assert pressed == []
    assert labels[-1] == app.HELD_SEND_NOTICE


def test_nothing_to_hold_without_a_countdown(countdown):
    assert app._hold_pending_send(announce=True) is False


def test_no_enter_into_another_window(countdown, monkeypatch):
    """Enter in whatever the user switched to could send something else."""
    pressed, _ = countdown
    app._send_automatically("test")
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "konsole")
    sends()[0].fire()
    assert pressed == []


def test_pressing_the_hold_key_holds_the_send_and_records(countdown, monkeypatch):
    """Holding the key dictates more onto the unsent message."""
    pressed, _ = countdown
    app._send_automatically("test")
    monkeypatch.setattr(app, "_open_input_stream", lambda idx: None)
    for name in ("_beep", "_notify", "_notify_tray_recording_state",
                 "_arm_chunker", "_pause_media_players"):
        monkeypatch.setattr(app, name, lambda *a, **k: None)
    monkeypatch.setattr(app, "recording_indicator", None)
    try:
        assert app.start_recording(False, False, 0) is True
        assert app.state.held_send is True
        sends()[0].fire()
        assert pressed == []
    finally:
        app.state.is_recording = False
        app.state.stream = None


def test_a_tap_too_short_to_dictate_says_not_sent(countdown, monkeypatch):
    _, labels = countdown
    reasons = []
    monkeypatch.setattr(app, "cancel_recording",
                        lambda beeps, notify, reason="": reasons.append(reason))
    monkeypatch.setattr(app.state, "held_send", True)
    monkeypatch.setattr(app.state, "silence", None)
    monkeypatch.setattr(app.state, "press_t0", app.time.time())
    assert app._finish_capture(False, False) is None
    assert reasons == [app.HELD_SEND_NOTICE]
    assert labels == [app.HELD_SEND_NOTICE]


def sends():
    """The timers that press Enter, not the countdown's ticks."""
    return [t for t in FakeTimer.made if t.fn is app._send_when_due]


def test_a_new_send_replaces_one_still_counting(countdown):
    pressed, _ = countdown
    app._send_automatically("first")
    app._send_automatically("second")
    assert sends()[0].cancelled
    sends()[1].fire()
    assert pressed == ["second"]


def test_the_label_counts_down_each_second(countdown, monkeypatch):
    """Ron, 2026-10-18: "Sending in 4 s", then 3, 2, 1."""
    _, labels = countdown
    from tuxtalk import screen_message
    relabels = []
    monkeypatch.setattr(screen_message, "relabel", lambda tag, text: relabels.append(text))
    monkeypatch.setattr(app, "_press_enter_delay", 4.0)
    monkeypatch.setattr(app, "_hold_key_name", "F8")
    app._send_automatically("test")
    ticks = [t for t in FakeTimer.made if t.fn is app._count_down]
    assert [t.delay for t in ticks] == [1.0, 2.0, 3.0]
    for tick in ticks:
        tick.fire()
    assert labels == ["Sending in 4 s. Tap F8 to hold it."]
    assert relabels == ["Sending in 3 s. Tap F8 to hold it.",
                        "Sending in 2 s. Tap F8 to hold it.",
                        "Sending in 1 s. Tap F8 to hold it."]


def test_a_held_send_stops_counting(countdown, monkeypatch):
    from tuxtalk import screen_message
    relabels = []
    monkeypatch.setattr(screen_message, "relabel", lambda tag, text: relabels.append(text))
    monkeypatch.setattr(app, "_press_enter_delay", 3.0)
    app._send_automatically("test")
    app._hold_pending_send(announce=True)
    assert all(t.cancelled for t in FakeTimer.made)
    for tick in [t for t in FakeTimer.made if t.fn is app._count_down]:
        tick.fn(*tick.args)          # even a tick already on its way
    assert relabels == []


def test_saying_press_enter_never_waits(monkeypatch):
    monkeypatch.setattr(app, "_press_enter_after", True)
    monkeypatch.setattr(app, "_press_enter_delay", 3.0)
    events = _dictate(monkeypatch, "See you at noon, press enter",
                      prepared=lambda r: r + ". ")
    assert events[-1] == ("enter", 'said "press enter"')


def test_the_wait_is_a_setting_applied_live():
    assert config.Settings().press_enter_delay == 2.0
    assert "press_enter_delay" in config.LIVE_APPLIED_KEYS
    app._apply_cleanup_settings(config.Settings(press_enter_delay=3.0, hotkey="F7"))
    try:
        assert app._press_enter_delay == 3.0 and app._hold_key_name == "F7"
    finally:
        app._apply_cleanup_settings(config.Settings())


def test_a_held_send_types_its_space_once_the_key_is_up(countdown, monkeypatch):
    """Ron, 2026-10-18: held a send, kept talking, got "delay.All right". The
    space waits for the hotkey to come up: typed while it's down, GNOME
    without the extension swallowed it (VM release test, 2026-10-18)."""
    monkeypatch.setattr(app.state, "last_inserted_text", "Testing the delay.")
    monkeypatch.setattr(app.state, "owed_space", None)
    app._send_automatically("test", " ", "type")
    app._hold_pending_send(announce=True)
    assert TYPED == []                     # the key is still down
    app._type_owed_space()                 # ...and now it's up
    assert TYPED == [" "]
    # Undo still sees the whole dictation, space included.
    assert app.state.last_inserted_text == "Testing the delay. "
    assert app.state.owed_space is None


def test_a_dictation_added_to_a_held_send_starts_with_its_space(countdown, monkeypatch):
    monkeypatch.setattr(app.state, "owed_space", None)
    app._send_automatically("test", " ", "type")
    app._hold_pending_send(announce=False)
    assert app._with_owed_space("And this part was added. ") == " And this part was added. "
    assert app._with_owed_space("Next one. ") == "Next one. "    # only once


def test_a_short_tap_types_the_space_when_released(countdown, monkeypatch):
    reasons = []
    monkeypatch.setattr(app, "cancel_recording",
                        lambda beeps, notify, reason="": reasons.append(reason))
    monkeypatch.setattr(app.state, "owed_space", None)
    app._send_automatically("test", " ", "type")
    monkeypatch.setattr(app.state, "held_send", app._hold_pending_send(announce=False))
    monkeypatch.setattr(app.state, "silence", None)
    monkeypatch.setattr(app.state, "chunks_submitted", 0)
    monkeypatch.setattr(app.state, "press_t0", app.time.time())
    assert app._finish_capture(False, False) is None
    assert TYPED == [" "] and reasons == [app.HELD_SEND_NOTICE]


def test_typing_the_space_doesnt_turn_a_tap_into_a_dictation(countdown, monkeypatch):
    """Zorin VM, 2026-10-20: a 183 ms tap plus the time ydotool took to type
    the space came to over MIN_HOLD_MS, so the held send got no "Not sent"
    notice. How long the key was down is what counts."""
    reasons = []
    monkeypatch.setattr(app, "cancel_recording",
                        lambda beeps, notify, reason="": reasons.append(reason))
    monkeypatch.setattr(app, "_inject_text",
                        lambda text, mode, t0: app.time.sleep(0.1) or TYPED.append(text) or "ok")
    monkeypatch.setattr(app.state, "owed_space", None)
    app._send_automatically("test", " ", "type")
    monkeypatch.setattr(app.state, "held_send", app._hold_pending_send(announce=False))
    monkeypatch.setattr(app.state, "silence", None)
    monkeypatch.setattr(app.state, "chunks_submitted", 0)
    monkeypatch.setattr(app.state, "press_t0", app.time.time() - 0.15)
    assert app._finish_capture(False, False) is None
    assert TYPED == [" "] and reasons == [app.HELD_SEND_NOTICE]


class _Keyboard:
    """A keyboard for _handle_key_event that says which keys are down."""
    name, path = "Keyboard", "/dev/input/event3"

    def __init__(self, down):
        self.down = down

    def capabilities(self, absinfo=True):
        from evdev import ecodes
        return {ecodes.EV_KEY: [ecodes.KEY_A, ecodes.KEY_M, ecodes.KEY_Z]}

    def active_keys(self):
        return list(self.down)

    def write(self, *event):
        pass

    def grab(self):
        pass

    def ungrab(self):
        pass


class _Clock:
    """app's time module, with a clock that only moves when told to."""

    def __init__(self):
        import time
        self._time, self.now = time, 1000.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def __getattr__(self, name):
        return getattr(self._time, name)


@pytest.mark.parametrize("start_s, still_down, outcome", [
    (0.25, False, "held"),          # the Pop VM: a quick tap, a slow start
    (0.45, False, "dictated"),      # too slow to tell: the hold time decides
    (0.25, True, "dictated"),       # still down: dictating more onto it
])
def test_a_quick_tap_holds_the_send_even_when_recording_starts_slowly(
        countdown, monkeypatch, start_s, still_down, outcome):
    """Pop!_OS VM, 2026-10-21: starting the recording took about 0.2 s, so a
    0.08 s tap of F8 was read as a 0.2 s hold and recorded instead of holding
    the send. A key already up after a start that quick was a tap; after a
    slower start, or still down, it's a dictation added to the message."""
    from types import SimpleNamespace
    from evdev import ecodes
    F8, F9 = ecodes.KEY_F8, ecodes.KEY_F9
    clock = _Clock()
    monkeypatch.setattr(app, "time", clock)
    reasons, dictated = [], []
    monkeypatch.setattr(app, "cancel_recording",
                        lambda beeps, notify, reason="": reasons.append(reason))
    monkeypatch.setattr(app, "_transcribe_and_inject", lambda *a, **k: dictated.append(1))
    monkeypatch.setattr(app, "_open_input_stream", lambda idx: clock.sleep(start_s))
    for name in ("_beep", "_notify", "_notify_tray_recording_state", "_arm_chunker",
                 "_pause_media_players", "_resume_media_players", "_stop_stream_safely"):
        monkeypatch.setattr(app, name, lambda *a, **k: None)
    monkeypatch.setattr(app, "recording_indicator", None)
    monkeypatch.setattr(app.state, "owed_space", None)
    monkeypatch.setattr(app.state, "silence", None)
    monkeypatch.setattr(app.state, "chunks_submitted", 0)
    monkeypatch.setattr(app.state, "frames", [])
    cfg = SimpleNamespace(beeps=False, notify=False, smart_quotes=False, language=None,
                          auto_space=True, auto_period=True, injection_mode="type")
    keyboard = _Keyboard([F8] if still_down else [])
    app._send_automatically("test", " ", "type")
    try:
        for value in (1, 0):        # the release was waiting while recording started
            app._handle_key_event(SimpleNamespace(type=ecodes.EV_KEY, code=F8, value=value),
                                  "hold", F8, F9, None, set(), [keyboard], cfg, 0)
    finally:
        app._release_all_grabs()
        app.state.is_recording = False
        app.state.stream = None
    if outcome == "held":
        assert reasons == [app.HELD_SEND_NOTICE] and dictated == []
    else:
        assert reasons == [] and dictated == [1]


def test_a_sent_message_never_gets_the_space(countdown):
    pressed, _ = countdown
    app._send_automatically("test", " ", "type")
    sends()[0].fire()
    assert pressed == ["test"] and TYPED == []


def test_no_space_into_another_window(countdown, monkeypatch):
    monkeypatch.setattr(app.state, "owed_space", None)
    app._send_automatically("test", " ", "type")
    app._hold_pending_send(announce=True)
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "konsole")
    app._type_owed_space()
    assert TYPED == []


def test_the_dictation_hands_its_left_off_space_to_the_send(monkeypatch):
    monkeypatch.setattr(app, "_press_enter_after", True)
    sends = []
    monkeypatch.setattr(app, "_send_automatically",
                        lambda why, space="", mode="type": sends.append(space))
    events = _dictate(monkeypatch, "Hello there", prepared=lambda r: r + ". ")
    assert ("typed", "Hello there.") in events
    assert sends == [" "]


def test_the_wait_goes_up_to_four_seconds():
    assert not any(k == "press_enter_delay" for k, _ in config._validation_problems(config.Settings(press_enter_delay=4.0)))
    assert any(k == "press_enter_delay" for k, _ in config._validation_problems(config.Settings(press_enter_delay=5.0)))


def test_switching_windows_stops_the_countdown_at_once(countdown, monkeypatch):
    """Ron, 2026-10-18: clicked another window; nothing was sent, but the
    label kept counting down to a send that wasn't going to happen."""
    pressed, _ = countdown
    from tuxtalk import screen_message
    relabels = []
    monkeypatch.setattr(screen_message, "relabel", lambda tag, text: relabels.append(text))
    monkeypatch.setattr(app, "_press_enter_delay", 4.0)
    app._send_automatically("test")
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "konsole")
    ticks = [t for t in FakeTimer.made if t.fn is app._count_down]
    ticks[0].fire()
    assert relabels == [app.SWITCHED_WINDOW_NOTICE]
    assert app.state.pending_send is None
    assert all(t.cancelled for t in FakeTimer.made)
    sends()[0].fire()
    assert pressed == []


def test_switching_in_the_last_second_says_not_sent(countdown, monkeypatch):
    pressed, labels = countdown
    app._send_automatically("test")
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "konsole")
    sends()[0].fire()
    assert pressed == [] and labels[-1] == app.SWITCHED_WINDOW_NOTICE


def test_no_automatic_send_in_a_text_editor(monkeypatch):
    """Ron, 2026-10-18: in KWrite every dictation ended with a new line."""
    monkeypatch.setattr(app, "_press_enter_after", True)
    events = _dictate(monkeypatch, "Hello there", prepared=lambda r: r + ". ",
                      window="org.kde.kwrite")
    assert events == [("typed", "Hello there. ")]     # its space kept, no Enter


def test_saying_press_enter_still_works_in_a_text_editor(monkeypatch):
    monkeypatch.setattr(app, "_press_enter_after", True)
    events = _dictate(monkeypatch, "See you at noon, press enter",
                      prepared=lambda r: r + ". ", window="org.kde.kwrite")
    assert events[-1] == ("enter", 'said "press enter"')


@pytest.mark.parametrize("window, document", [
    ("org.kde.kwrite", True), ("libreoffice-writer", True), ("obsidian", True),
    ("code", True), ("Gedit", True),
    ("brave-browser", False), ("com.anthropic.Claude", False),
    ("org.kde.konsole", False), (None, False),
])
def test_which_windows_are_documents(window, document):
    assert app.is_document_window(window) is document
