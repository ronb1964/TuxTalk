"""Live text (TuxTalk Labs): the words so far, shown while the user talks."""
import logging
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from tuxtalk import dictation_worker, engine, focused_window, live_text, x11_desktop
from tuxtalk import live_settings as live
from tuxtalk import logger as tt_logger
from tuxtalk import recording_state as rs


class Window:
    """Stands in for live_text_window.LiveTextWindow: what it was asked to
    show, and whether it is up now."""

    def __init__(self):
        self.shown, self.hidden, self.up = [], 0, False

    def show(self, words, rec):
        self.shown.append(words)
        self.up = True

    def hide(self):
        self.hidden += 1
        self.up = False


# The real check, before the talking fixture stands in for it.
ON_GRAPHICS_CARD = engine.on_graphics_card


class Model:
    """Stands in for the loaded speech model; only its identity matters here."""


def _speech(seconds, rate=16000):
    """*seconds* of int16 audio blocks, as the audio callback keeps them."""
    samples = (np.sin(np.arange(int(rate * seconds)) / 9) * 8000).astype(np.int16)
    return [samples.tobytes()]


def _next_dictation(monkeypatch, frames=None):
    """A new recording running, the next dictation, holding *frames*."""
    rec = rs.Recording()
    rec.frames = frames if frames is not None else _speech(3)
    monkeypatch.setattr(rs.state, "current", rec)
    return rec


@pytest.fixture
def floated(monkeypatch):
    """The messages shown over the window being dictated into."""
    shown = []
    monkeypatch.setattr(live_text.screen_message, "show_over_active_window",
                        lambda text, **k: shown.append(text))
    return shown


@pytest.fixture
def talking(monkeypatch, floated):
    """A recording running with live text on; returns (recording, window,
    the length of each piece of audio transcribed)."""
    window, heard = Window(), []
    rec = rs.Recording()
    rec.frames = _speech(3)
    monkeypatch.setattr(live_text, "window", window)
    monkeypatch.setattr(live_text, "_last_read", 0.0)
    monkeypatch.setattr(live_text, "_skip", None)
    monkeypatch.setattr(live_text, "_slow", (None, 0, 0.0))
    monkeypatch.setattr(live_text, "_seen", None)
    monkeypatch.setattr(live_text, "_settled", None)
    monkeypatch.setattr(live_text, "STOP_AFTER_S", LIMIT)
    monkeypatch.setattr(engine, "model", Model())
    monkeypatch.setattr(engine, "_running_engine", ("parakeet", "vulkan"))
    monkeypatch.setattr(engine, "on_graphics_card", lambda: True)
    monkeypatch.setattr(live, "live_text", True)
    monkeypatch.setattr(live, "remove_fillers", False)
    monkeypatch.setattr(rs.state, "current", rec)
    monkeypatch.setattr(rs.state, "is_recording", True)
    monkeypatch.setattr(x11_desktop, "popup_ends_show_desktop", lambda: False)

    def transcribe(audio, language):
        heard.append(len(audio))
        return "so far so good"
    monkeypatch.setattr(engine, "transcribe", transcribe)
    return rec, window, heard


def test_the_words_so_far_are_shown_while_recording(talking):
    _rec, window, heard = talking
    live_text.tick(now=100.0)
    assert window.shown == ["so far so good"]
    assert heard == [3 * 16000]


def test_they_are_read_again_about_twice_a_second(talking):
    """Often enough that words keep up with the talking, never on every tick."""
    _rec, window, _heard = talking
    for now in (100.0, 100.2, 100.35, 100.4, 100.6):
        live_text.tick(now=now)
    assert len(window.shown) == 2           # at 100.0 and 100.4


@pytest.mark.parametrize("why", ["off", "not recording", "voice edit", "a read running"])
def test_no_read_when_it_isnt_wanted(talking, monkeypatch, why):
    rec, window, _heard = talking
    if why == "off":
        monkeypatch.setattr(live, "live_text", False)
    elif why == "not recording":
        monkeypatch.setattr(rs.state, "is_recording", False)
    elif why == "voice edit":
        rec.edit_mode = True
    else:
        live_text._reading.set()
    try:
        live_text.tick(now=100.0)
    finally:
        live_text._reading.clear()
    assert window.shown == []


def test_a_password_field_never_shows_the_words(talking, monkeypatch):
    """Said into a password field, the words aren't typed; they mustn't
    show on screen either, and what was up before goes."""
    _rec, window, heard = talking
    live_text.tick(now=100.0)
    assert window.up
    monkeypatch.setattr(focused_window, "is_password", lambda budget=None: True)
    live_text.tick(now=101.0)
    assert window.shown == ["so far so good"] and not window.up
    assert heard == [3 * 16000]             # the password audio was never read


def test_cinnamons_show_desktop_isnt_ended_by_the_box(talking, monkeypatch):
    _rec, window, _heard = talking
    monkeypatch.setattr(x11_desktop, "popup_ends_show_desktop", lambda: True)
    live_text.tick(now=100.0)
    assert window.shown == []


def test_a_recording_typed_at_its_pauses_isnt_read(talking):
    """Type at pause switched on during it cuts its audio under a read: it's
    one or the other."""
    rec, window, heard = talking
    rec.chunker = object()
    live_text.tick(now=100.0)
    assert heard == [] and window.shown == []


def test_a_read_after_the_stop_shows_nothing(talking, monkeypatch):
    rec, window, _heard = talking
    monkeypatch.setattr(rs.state, "is_recording", False)
    live_text._read(rec, _speech(2), 16000)
    assert window.shown == []


def test_when_nothing_is_heard_old_words_dont_stay_up(talking, monkeypatch):
    _rec, window, _heard = talking
    live_text.tick(now=100.0)
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: None)
    live_text.tick(now=101.0)
    assert not window.up


def test_fillers_left_out_of_the_typed_text_are_left_out_here_too(talking, monkeypatch):
    _rec, window, _heard = talking
    monkeypatch.setattr(live, "remove_fillers", True)
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: "so um we should go")
    live_text.tick(now=100.0)
    assert window.shown == ["so we should go"]


def _taking(seconds, words="so far so good"):
    """A transcribe that really takes *seconds*, as a slow graphics chip would."""
    def transcribe(audio, language):
        time.sleep(seconds)
        return words
    return transcribe


# The talking fixture's limit for a read, lower than the real one so the
# tests that really take that long stay quick; and a read clearly over it.
LIMIT = 0.2
SLOW = LIMIT + 0.1


def test_a_slow_read_ends_live_text_for_that_dictation_only(talking, monkeypatch):
    """A read takes the worker the typed text needs, and the next is a
    second longer: once one is slow the dictation isn't read again."""
    rec, window, _heard = talking
    monkeypatch.setattr(engine, "transcribe", _taking(SLOW))
    live_text.tick(now=100.0)
    assert window.shown == [] and not window.up
    monkeypatch.setattr(engine, "transcribe", _taking(0.0, "next"))
    live_text.tick(now=101.0)
    assert window.shown == []               # no more reads for this dictation
    rec = _next_dictation(monkeypatch, rec.frames)
    live_text.tick(now=102.0)
    assert window.shown == ["next"]


def test_a_read_just_under_the_limit_is_shown(talking, monkeypatch):
    """On a clock the read itself moves on, so a busy computer can't tip it over."""
    _rec, window, _heard = talking
    now = [0.0]

    def transcribe(audio, language):
        now[0] += LIMIT * 0.9
        return "just in time"
    monkeypatch.setattr(live_text, "time", SimpleNamespace(monotonic=lambda: now[0],
                                                           time=time.time))
    monkeypatch.setattr(engine, "transcribe", transcribe)
    live_text.tick(now=100.0)
    assert window.shown == ["just in time"]


def test_a_chip_too_slow_twice_ends_live_text_while_it_runs(talking, monkeypatch, floated):
    """A built-in graphics chip can be no faster than the processor, and the
    first read of every dictation would hold up the typing again. The user is
    told, once: a box that goes away for good looks broken."""
    rec, window, _heard = talking
    reads = []

    def slow(audio, language):
        reads.append(1)
        return _taking(SLOW)(audio, language)
    monkeypatch.setattr(engine, "transcribe", slow)
    for n in range(3):
        rec = _next_dictation(monkeypatch, rec.frames)
        live_text.tick(now=100.0 + n)
    assert len(reads) == live_text.SLOW_READS and window.shown == []
    assert floated == [live_text.TOO_SLOW]
    monkeypatch.setattr(engine, "model", Model())       # Power Saving let it go, and back
    rec = _next_dictation(monkeypatch, rec.frames)
    live_text.tick(now=110.0)
    assert len(reads) == live_text.SLOW_READS           # still the same slow chip
    monkeypatch.setattr(engine, "_running_engine", ("small", "cuda"))   # another engine
    monkeypatch.setattr(engine, "transcribe", _taking(0.0))
    rec = _next_dictation(monkeypatch, rec.frames)
    live_text.tick(now=111.0)
    assert window.shown == ["so far so good"]


def test_long_dictations_slow_to_read_dont_end_it_for_good(talking, monkeypatch):
    """The audio grows with the dictation: a minute of it can be slow on
    any card. That ends reads of that dictation only."""
    rec, window, _heard = talking
    short, slow_reads = int(live_text.SHORT_AUDIO_S * 16000), []

    def slow_when_long(audio, language):
        if len(audio) > short:
            slow_reads.append(len(audio))
            time.sleep(SLOW)
        return "so far"
    monkeypatch.setattr(engine, "transcribe", slow_when_long)
    for dictation in range(3):
        rec.frames = _speech(live_text.SHORT_AUDIO_S + 25)
        rec = _next_dictation(monkeypatch, rec.frames)
        for second in range(4):
            live_text.tick(now=100.0 + 10 * dictation + second)
    assert len(slow_reads) == 3             # one per dictation, then it stopped
    monkeypatch.setattr(engine, "transcribe", _taking(0.0, "still on"))
    rec = _next_dictation(monkeypatch, rec.frames)
    live_text.tick(now=200.0)
    assert window.shown[-1] == "still on"


def test_a_first_read_held_back_by_typing_still_tells_a_slow_chip(talking, monkeypatch):
    """The worker can be busy typing the dictation before until the next
    one is seconds long: its first read sees a few seconds
    of it at most, and is counted when the chip is slow."""
    rec, _window, heard = talking
    read = []

    def slow(audio, language):
        read.append(len(audio))
        return _taking(SLOW)(audio, language)
    monkeypatch.setattr(engine, "transcribe", slow)
    for n in range(live_text.SLOW_READS):
        rec.frames = _speech(live_text.SHORT_AUDIO_S + 3)
        rec = _next_dictation(monkeypatch, rec.frames)
        live_text.tick(now=100.0 + n)
    assert read == [int(live_text.GROWTH_S * 16000)] * live_text.SLOW_READS
    _off_while_that_engine_runs(rec, _window, monkeypatch)


def _off_while_that_engine_runs(rec, window, monkeypatch):
    """Assert a new dictation isn't read at all: live text is off."""
    reads, shown = [], len(window.shown)
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: reads.append(1) or "x")
    rec = _next_dictation(monkeypatch, rec.frames)
    live_text.tick(now=500.0)
    assert reads == [] and len(window.shown) == shown


def test_a_slow_read_counts_even_when_the_dictation_ended_meanwhile(talking, monkeypatch):
    """Those are the reads the typed text waited on."""
    rec, window, _heard = talking

    read = []

    def slow_then_typed(audio, language):
        read.append(1)
        time.sleep(SLOW)
        _next_dictation(monkeypatch)        # the next one started while it read
        return "late"
    monkeypatch.setattr(engine, "transcribe", slow_then_typed)
    for second in range(live_text.SLOW_READS):
        live_text.tick(now=100.0 + second)
    assert len(read) == live_text.SLOW_READS
    _off_while_that_engine_runs(rec, window, monkeypatch)


def test_a_slow_chip_isnt_excused_by_each_dictations_short_first_reads(talking, monkeypatch):
    """Every dictation starts with reads shorter than the one that turned
    out slow; those mustn't wipe the count, or every dictation pays a slow
    read."""
    rec, window, _heard = talking
    two_seconds = 2 * 16000

    def slow_from_two_seconds(audio, language):
        if len(audio) >= two_seconds:
            time.sleep(SLOW)
        return "so far"
    monkeypatch.setattr(engine, "transcribe", slow_from_two_seconds)
    for n in range(live_text.SLOW_READS):
        rec = _next_dictation(monkeypatch, rec.frames)
        for seconds in (1, 2):
            rec.frames = _speech(seconds)
            live_text.tick(now=100.0 + 10 * n + seconds)
    _off_while_that_engine_runs(rec, window, monkeypatch)


def test_one_slow_read_between_fast_ones_doesnt_end_it(talking, monkeypatch):
    """A card that's fast but slow once in a while (another app on it) keeps
    live text: a fast read of as much audio clears the slow one."""
    rec, window, _heard = talking
    for n, seconds in enumerate([SLOW, 0.0, SLOW, 0.0]):
        monkeypatch.setattr(engine, "transcribe", _taking(seconds, f"dictation {n}"))
        rec = _next_dictation(monkeypatch, rec.frames)
        live_text.tick(now=100.0 + n)
    assert window.shown == ["dictation 1", "dictation 3"]


def test_a_slow_password_check_isnt_blamed_on_the_card(talking, monkeypatch):
    """An app hanging on the accessibility bus ends that dictation's reads,
    but the card is fine."""
    rec, _window, _heard = talking

    def slow_check(budget=None):
        time.sleep(SLOW)
        return False
    monkeypatch.setattr(focused_window, "is_password", slow_check)
    for n in range(3):
        rec = _next_dictation(monkeypatch, rec.frames)
        live_text.tick(now=100.0 + n)
    assert len(_heard) == 3                 # each dictation was read
    monkeypatch.setattr(focused_window, "is_password", lambda budget=None: False)
    rec = _next_dictation(monkeypatch, rec.frames)
    live_text.tick(now=200.0)
    assert _window.shown == ["so far so good"]


def test_parakeet_moving_to_the_processor_ends_the_reads(talking, monkeypatch):
    """After two failures on the chip Parakeet works on the processor for the
    rest of the session, where a read could hold the typed text up."""
    _rec, window, heard = talking
    monkeypatch.setattr(engine, "on_graphics_card", ON_GRAPHICS_CARD)
    monkeypatch.setattr(engine, "notify_tray", lambda: None)
    engine.processor_took_over()
    live_text.tick(now=100.0)
    assert heard == [] and engine.running() == ("parakeet", "cpu")


@pytest.mark.parametrize("why", ["CUDA fell back", "resting"])
def test_no_graphics_card_means_no_reads(monkeypatch, why):
    """CUDA failing over to the processor records it there; Power Saving
    lets the model go, and a read would wait for it to load again."""
    monkeypatch.setattr(engine, "_running_engine", ("small", "cpu" if why == "CUDA fell back"
                                                    else "cuda"))
    monkeypatch.setattr(engine, "model", None if why == "resting" else Model())
    assert not engine.on_graphics_card()


def test_on_the_processor_nothing_is_read_and_the_user_is_told_once(talking, monkeypatch,
                                                                     caplog, floated):
    """A read there can't be kept short, and the typed text would wait on it;
    a box that never opens, with no word why, looks broken."""
    _rec, window, heard = talking
    monkeypatch.setattr(engine, "_running_engine", ("parakeet", "cpu"))
    monkeypatch.setattr(engine, "on_graphics_card", lambda: False)
    monkeypatch.setattr(live_text, "_said_processor", False)
    with caplog.at_level(logging.INFO, logger="tuxtalk.live_text"):
        live_text.tick(now=100.0)
        live_text.tick(now=101.0)
    assert heard == [] and window.shown == []
    assert floated == [live_text.ON_PROCESSOR]
    assert len([r for r in caplog.records if "graphics card" in r.getMessage()]) == 1


def test_a_model_still_loading_says_nothing_about_the_processor(talking, monkeypatch, floated):
    """Power Saving let the model go: it's on its way back, wherever it runs."""
    monkeypatch.setattr(engine, "model", None)
    monkeypatch.setattr(live_text, "_said_processor", False)
    live_text.tick(now=100.0)
    assert floated == [] and live_text._said_processor is False


def test_words_said_into_a_password_field_stay_hidden_after_leaving_it(talking, monkeypatch):
    """The dictation still holds them, so it isn't read again until the next one."""
    rec, window, heard = talking
    monkeypatch.setattr(focused_window, "is_password", lambda budget=None: True)
    live_text.tick(now=100.0)
    monkeypatch.setattr(focused_window, "is_password", lambda budget=None: False)
    live_text.tick(now=101.0)
    assert heard == [] and window.shown == []
    rec = _next_dictation(monkeypatch, rec.frames)
    live_text.tick(now=102.0)
    assert window.shown == ["so far so good"]


def test_a_password_field_reached_during_a_read_gets_nothing(talking, monkeypatch):
    _rec, window, _heard = talking
    answers = iter([False, True])
    monkeypatch.setattr(focused_window, "is_password", lambda budget=None: next(answers))
    live_text.tick(now=100.0)
    assert window.shown == []


def test_each_read_sees_only_a_little_more_than_the_last(talking):
    """A jump from a short read to a long one could hold up the typing."""
    rec, _window, heard = talking
    rec.frames = _speech(90)
    for n in range(3):
        live_text.tick(now=100.0 + n)
    growth = int(live_text.GROWTH_S * 16000)
    assert heard == [growth, 2 * growth, 3 * growth]


def _talk_with_a_gap(before, after, rate=16000):
    """Talking for *before* seconds, half a second's quiet, then *after* more."""
    def talk(seconds):
        return (np.sin(np.arange(int(rate * seconds)) / 9) * 8000).astype(np.int16)
    gap = np.zeros(rate // 2, dtype=np.int16)
    return [np.concatenate([talk(before), gap, talk(after)]).tobytes()]


def _numbered_reads(monkeypatch):
    """Each read answers "read 1", "read 2"...; returns how much each heard."""
    heard = []

    def transcribe(audio, language):
        heard.append(len(audio))
        return f"read {len(heard)}"
    monkeypatch.setattr(engine, "transcribe", transcribe)
    return heard


def test_a_long_dictations_start_is_read_once_at_a_pause_and_then_kept(talking, monkeypatch):
    """The audio grows for the whole dictation: its start is settled at a
    gap between words, and its words stay put on screen while the rest goes
    on being read."""
    rec, window, _heard = talking
    heard = _numbered_reads(monkeypatch)
    rec.frames = _talk_with_a_gap(20, 15)
    monkeypatch.setattr(live_text, "_seen", (rec, 35.0))
    live_text.tick(now=100.0)
    settled, rest = heard
    assert abs(settled / 16000 - 20.25) <= live_text.QUIET_S
    assert window.shown == ["read 1 read 2"]
    rec.frames = rec.frames + _speech(2)
    live_text.tick(now=101.0)
    assert heard[2] == rest + 2 * 16000     # the kept start isn't heard again
    assert window.shown[-1] == "read 1 read 3"


def test_nothing_is_kept_from_a_read_that_skipped_words(talking, monkeypatch):
    """A first read held back hears only the newest seconds: keeping its
    words would leave out what came before them for good."""
    rec, _window, _heard = talking
    heard = _numbered_reads(monkeypatch)
    rec.frames = _talk_with_a_gap(20, 30)
    monkeypatch.setattr(live_text, "_seen", (rec, 35.0))
    live_text.tick(now=100.0)
    assert heard == [40 * 16000] and live_text._settled is None


def test_a_moment_of_sound_isnt_read(talking):
    rec, window, heard = talking
    rec.frames = _speech(0.2)
    live_text.tick(now=100.0)
    assert heard == [] and window.shown == []


def test_a_microphone_at_another_rate_is_read_at_the_engines(talking):
    rec, _window, heard = talking
    rec.samplerate = 48000
    rec.frames = _speech(2, rate=48000)
    live_text.tick(now=100.0)
    assert heard == [2 * 16000]


def test_a_failed_read_lets_the_next_one_run(talking, monkeypatch):
    def broken(audio, language):
        raise RuntimeError("engine hiccup")
    monkeypatch.setattr(engine, "transcribe", broken)
    with pytest.raises(RuntimeError):
        live_text.tick(now=100.0)
    assert not live_text._reading.is_set()


def test_stopping_closes_the_window(talking):
    _rec, window, _heard = talking
    live_text.tick(now=100.0)
    live_text.hide()
    assert not window.up


@pytest.fixture
def real_worker(monkeypatch):
    """The real dictation worker, one thread running jobs in order."""
    worker = dictation_worker.DictationWorker()
    monkeypatch.setattr(dictation_worker, "_instance", worker)
    yield worker
    assert worker.drain(5)


def test_no_read_is_queued_behind_a_dictation_being_typed(talking, real_worker):
    _rec, window, heard = talking
    typing, release = threading.Event(), threading.Event()
    real_worker.submit(lambda: (typing.set(), release.wait(5)))
    assert typing.wait(5)
    live_text.tick(now=100.0)
    release.set()
    assert real_worker.drain(5)
    assert heard == [] and not live_text._reading.is_set()


def test_a_dictation_stopped_during_a_read_is_typed_right_after_it(talking, real_worker,
                                                                   monkeypatch):
    """The read already running finishes, its words (being typed now) aren't
    shown, and the dictation's own job runs next."""
    rec, window, _heard = talking
    reading, release, order = threading.Event(), threading.Event(), []

    def slow_transcribe(audio, language):
        reading.set()
        release.wait(5)
        order.append("read")
        return "the words"
    monkeypatch.setattr(engine, "transcribe", slow_transcribe)
    live_text.tick(now=100.0)
    assert reading.wait(5)
    monkeypatch.setattr(rs.state, "is_recording", False)    # the key came up
    real_worker.submit(lambda: order.append("typed"))
    release.set()
    assert real_worker.drain(5)
    assert order == ["read", "typed"] and window.shown == []


def test_submit_if_idle_queues_nothing_while_the_worker_has_work(real_worker):
    release = threading.Event()
    real_worker.submit(lambda: release.wait(5))
    assert not real_worker.submit_if_idle(lambda: None)
    release.set()
    assert real_worker.drain(5)
    ran = threading.Event()
    assert real_worker.submit_if_idle(ran.set)
    assert ran.wait(5)


def test_a_read_isnt_queued_alongside_a_job_being_handed_over(real_worker, monkeypatch):
    """The dictation before is handed to the worker while live text asks
    whether it's free: the read must see that job, not slip in before it."""
    putting, finish, typed = threading.Event(), threading.Event(), threading.Event()

    def handed():
        typed.wait(5)                       # still being typed when the answer comes
    queue_put = real_worker._queue.put

    def slow_put(job):
        if job is handed:
            putting.set()
            finish.wait(5)
        queue_put(job)
    monkeypatch.setattr(real_worker._queue, "put", slow_put)
    answer = []
    handing_over = threading.Thread(target=real_worker.submit, args=(handed,))
    asking = threading.Thread(target=lambda: answer.append(real_worker.submit_if_idle(print)))
    try:
        handing_over.start()
        assert putting.wait(5)
        asking.start()
        asking.join(0.2)
        finish.set()
        handing_over.join(5)
        asking.join(5)
    finally:
        finish.set()
        typed.set()
    assert answer == [False]


def test_a_slow_read_ending_after_the_stop_keeps_no_recording(talking, real_worker,
                                                              monkeypatch):
    """Its audio, the whole dictation, mustn't stay in memory until some
    later dictation is too slow."""
    rec, _window, _heard = talking
    reading, release = threading.Event(), threading.Event()

    def held(audio, language):
        reading.set()
        release.wait(5)
        return "late"
    monkeypatch.setattr(engine, "transcribe", held)
    monkeypatch.setattr(live_text, "STOP_AFTER_S", -1.0)
    live_text.tick(now=100.0)
    assert reading.wait(5)
    monkeypatch.setattr(rs.state, "is_recording", False)
    live_text.hide()
    release.set()
    assert real_worker.drain(5)
    assert live_text._skip is None and live_text._seen is None and live_text._settled is None


def test_a_hiccup_on_a_five_second_read_doesnt_linger(talking, monkeypatch):
    """A first read held back by typing hears 5 s; one slow one there (the
    helper restarting) must be cleared by ordinary fast reads, or the next
    hiccup, however short, would end live text for the session."""
    rec, window, _heard = talking
    for n, (seconds, slow) in enumerate([(5, True), (4.8, False), (1, True), (2, False)]):
        rec.frames = _speech(seconds)
        rec = _next_dictation(monkeypatch, rec.frames)
        monkeypatch.setattr(engine, "transcribe", _taking(SLOW if slow else 0.0, f"p{n}"))
        live_text.tick(now=100.0 + n)
    assert window.shown == ["p1", "p3"]


def test_a_read_ending_after_the_stop_keeps_nothing_it_saw(talking, monkeypatch):
    """The key came up while the read checked for a password field: the
    recording, and all its audio, mustn't stay in memory."""
    rec, _window, _heard = talking
    first = []

    def stop_during_the_check(budget=None):
        if not first:
            first.append(True)
            monkeypatch.setattr(rs.state, "is_recording", False)
            live_text.hide()
            monkeypatch.setattr(rs.state, "is_recording", True)
        return False
    monkeypatch.setattr(focused_window, "is_password", stop_during_the_check)
    live_text.tick(now=100.0)
    monkeypatch.setattr(rs.state, "is_recording", False)
    live_text._let_go_if_ended(rec)
    assert live_text._seen is None and live_text._settled is None


def test_only_the_newest_audio_is_converted(talking, monkeypatch):
    """Converting the whole recording every second costs more as it grows."""
    rec, _window, heard = talking
    second = _speech(1)[0]
    rec.frames = [second] * 90              # a minute and a half, in one-second blocks
    converted = []
    real = live_text.recording.frames_f32

    def counting(frames):
        converted.append(len(frames))
        return real(frames)
    monkeypatch.setattr(live_text.recording, "frames_f32", counting)
    live_text.tick(now=100.0)
    assert converted == [int(live_text.GROWTH_S)]
    assert heard == [int(live_text.GROWTH_S * 16000)]


def test_live_reads_leave_the_log_alone_but_not_its_warnings():
    """A line of text every second, from every engine, would be most of the
    log: the log file's own handler leaves them out."""
    handler = tt_logger._shared_handler()

    def kept(level):
        return bool(handler.filter(logging.LogRecord(
            "tuxtalk.parakeet_engine", level, __file__, 1, "Raw transcription", None, None)))
    with tt_logger.quietly():
        with tt_logger.quietly():
            pass
        assert not kept(logging.INFO)           # still quiet after an inner block
        assert kept(logging.WARNING)
    assert kept(logging.INFO)


def test_a_read_leaves_no_info_lines_in_the_log(talking, monkeypatch):
    """Every engine's timing and raw-text lines, once a second, for each read."""
    _rec, window, _heard = talking
    quiet = []

    def transcribe(audio, language):
        quiet.append(("read", tt_logger.is_quiet()))
        return "so far"

    def is_password(budget=None):
        quiet.append(("check", tt_logger.is_quiet()))
        return False
    monkeypatch.setattr(engine, "transcribe", transcribe)
    monkeypatch.setattr(focused_window, "is_password", is_password)
    live_text.tick(now=100.0)
    assert quiet == [("check", True), ("read", True), ("check", True)]
    assert window.shown == ["so far"]


def test_the_labs_choice_turns_it_on(prefs_window):
    """Off until it's picked; picking it changes the setting to be saved."""
    win = prefs_window()
    choice = win.advanced_tab.type_at_pause.while_talking
    assert choice.get_active_id() == "nothing"
    choice.set_active_id("box")
    assert win.config["live_text"] is True
