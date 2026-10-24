"""Chunked dictation: the pause detector and chunk worker on synthetic audio,
and the app's chunk pipeline with a fake engine and fake typing.

Audio is built like test_silence.py: quiet room noise and louder "speech"
(noise plus a tone, with syllable gaps), fed in 20 ms blocks.
"""
import threading
import time

import numpy as np
import pytest

from tuxtalk.history import add_entry as REAL_ADD_ENTRY

SR = 16000
BLOCK = 320   # 20 ms at 16 kHz
rng = np.random.default_rng(1)


def noise(seconds, level=60.0, sr=SR):
    return (rng.normal(0, level, int(seconds * sr))).astype(np.int16)


def speech(seconds, level=3000.0, noise_level=60.0, sr=SR):
    """Syllables: 180 ms of voice, then a 70 ms gap where only the room is heard."""
    t = np.arange(int(seconds * sr)) / sr
    voiced = (t % 0.25) < 0.18
    tone = np.sin(2 * np.pi * 220 * t) * level * voiced
    return (tone + rng.normal(0, noise_level, t.size)).astype(np.int16)


def blocks(audio, block=BLOCK):
    for i in range(0, len(audio), block):
        yield audio[i:i + block]


# --- the shared noise floor ---------------------------------------------------

def test_noise_floor_tells_room_from_voice():
    from tuxtalk.silence import NoiseFloor, block_rms
    floor = NoiseFloor()
    for b in blocks(noise(1.0)):
        rms, dur = block_rms(b.tobytes(), SR)
        assert floor.update(rms, dur) is False
    loud = speech(0.18)[:BLOCK]
    rms, dur = block_rms(loud.tobytes(), SR)
    assert floor.update(rms, dur) is True


def test_block_rms_of_an_empty_block():
    from tuxtalk.silence import block_rms
    assert block_rms(b"", SR) == (0.0, 0.0)


# --- the pause detector ------------------------------------------------------

def run_pauses(audio, pause=0.8, sr=SR, block=BLOCK):
    """Feed audio in blocks; return (times in s at which a boundary fired, detector)."""
    from tuxtalk.chunking import PauseDetector
    det = PauseDetector(pause, sr)
    fired = []
    for i in range(0, len(audio), block):
        if det.feed(audio[i:i + block].tobytes()):
            fired.append((i + block) / sr)
    return fired, det


def test_a_pause_after_speech_is_a_boundary():
    fired, det = run_pauses(np.concatenate([speech(1.0), noise(1.5)]))
    assert len(fired) == 1
    # The last voiced syllable ends at ~0.93 s, so 0.8 s of quiet is reached by ~1.75 s.
    assert 1.6 <= fired[0] <= 1.9
    assert det.last_pause >= 0.8


def test_a_short_pause_is_not_a_boundary():
    fired, _ = run_pauses(np.concatenate([speech(1.0), noise(0.5), speech(1.0), noise(0.2)]))
    assert fired == []


def test_it_rearms_after_each_boundary():
    audio = np.concatenate([speech(1.0), noise(1.0), speech(1.0), noise(1.0)])
    fired, _ = run_pauses(audio)
    assert len(fired) == 2


def test_a_cough_alone_is_not_a_boundary():
    fired, _ = run_pauses(np.concatenate([noise(0.5), speech(0.2), noise(2.0)]))
    assert fired == []


def test_long_silence_gives_one_boundary():
    fired, _ = run_pauses(np.concatenate([speech(1.0), noise(10.0)]))
    assert len(fired) == 1


def test_cough_in_a_long_silence_is_forgotten():
    """A cough after a boundary, then more silence, then another cough: the two
    coughs must not add up to 'enough speech' and cut a chunk of room noise."""
    audio = np.concatenate([speech(1.0), noise(1.0), speech(0.2), noise(3.0),
                            speech(0.2), noise(3.0)])
    fired, _ = run_pauses(audio)
    assert len(fired) == 1


def test_tail_keeps_the_last_words_decay():
    from tuxtalk.chunking import TAIL_KEEP_S
    fired, det = run_pauses(np.concatenate([speech(1.0), noise(1.5)]))
    assert fired
    expected = int((det.last_pause - TAIL_KEEP_S) * SR)
    assert abs(det.tail_samples() - expected) <= BLOCK


def test_works_at_48k():
    sr, block = 48000, 960
    audio = np.concatenate([speech(1.0, sr=sr), noise(1.5, sr=sr)])
    fired, _ = run_pauses(audio, sr=sr, block=block)
    assert len(fired) == 1
    assert 1.6 <= fired[0] <= 1.9


def test_a_quiet_dynamic_mic_still_gets_boundaries():
    """Measured on a Beyerdynamic M 70 PRO X into a MiniFuse 2 in a quiet room:
    the room at int16 RMS 1-6, speech at 50-600. Auto-stop's 300 minimum
    would never hear that as speech, so no pause would ever end a chunk."""
    audio = np.concatenate([speech(1.0, level=150.0, noise_level=2.0), noise(1.5, level=2.0),
                            speech(1.0, level=150.0, noise_level=2.0), noise(1.5, level=2.0)])
    fired, _ = run_pauses(audio)
    assert len(fired) == 2


def loud_room(seconds, sr=SR):
    """cbox's onboard mic at full gain: steady hiss (int16 RMS ~380) plus a
    strong rumble below 20 Hz."""
    t = np.arange(int(seconds * sr)) / sr
    return np.clip(rng.normal(0, 380, t.size) + 600 * np.sin(2 * np.pi * 15 * t),
                   -32768, 32767).astype(np.int16)


def loud_speech(seconds, sr=SR, level=2000.0):
    t = np.arange(int(seconds * sr)) / sr
    voiced = (t % 0.25) < 0.18
    tone = np.sin(2 * np.pi * 220 * t) * level * voiced
    return np.clip(tone + loud_room(seconds, sr), -32768, 32767).astype(np.int16)


def test_a_loud_noisy_room_still_gets_boundaries():
    """Measured on cbox (ALC887, +50 dB): hiss at RMS 370-410 once the rumble
    is filtered out, speech at 600-4000. Taking the quietest block ever heard
    as the room, plus a fixed minimum, counted the hiss as speech forever."""
    audio = np.concatenate([loud_room(1.0), loud_speech(2.0), loud_room(1.5),
                            loud_speech(2.0), loud_room(1.5)])
    fired, _ = run_pauses(audio)
    assert len(fired) == 2


def test_a_dropout_does_not_poison_the_room_level():
    """A few blocks of digital silence (stream start, an xrun) must not make the
    detector take the room's hiss for speech for the rest of the recording."""
    audio = np.concatenate([np.zeros(BLOCK * 3, dtype=np.int16), loud_room(1.0),
                            loud_speech(2.0), loud_room(1.5),
                            np.zeros(BLOCK * 2, dtype=np.int16),
                            loud_speech(2.0), loud_room(1.5)])
    fired, _ = run_pauses(audio)
    assert len(fired) == 2


def test_the_room_level_follows_a_room_that_gets_noisier():
    """A fan starts: the hiss jumps. It may cost one stray cut while it still
    sounds like speech, but within ROOM_WINDOW_S it is the room, and the pause
    after the next words is found again."""
    audio = np.concatenate([speech(1.0), noise(1.0), noise(6.0, level=400.0),
                            loud_speech(2.0), loud_room(1.5)])
    fired, _ = run_pauses(audio)
    assert any(10.5 <= t <= 11.5 for t in fired)   # the words end at 10.0 s
    assert len(fired) <= 3


def test_tiny_audio_blocks_still_get_boundaries():
    """The service's callback on cbox delivered ~6 ms blocks; a block that
    small is too noisy a measurement, so the level is taken over fixed 20 ms
    windows whatever the block size."""
    audio = np.concatenate([loud_room(1.0), loud_speech(2.0), loud_room(1.5),
                            loud_speech(2.0), loud_room(1.5)])
    fired, _ = run_pauses(audio, block=64)
    assert len(fired) == 2


def test_lone_noise_spikes_do_not_hide_a_pause():
    """A click or a burst of hiss during a pause (shorter than a consonant)
    must not start the pause over."""
    room = loud_room(1.5)
    for start in (0.3, 0.7, 1.1):          # three 10 ms clicks in the pause
        i = int(start * SR)
        room[i:i + 160] = np.clip(room[i:i + 160].astype(np.int32) * 4, -32768, 32767)
    audio = np.concatenate([loud_room(1.0), loud_speech(2.0), room, loud_speech(2.0),
                            loud_room(1.5)])
    fired, _ = run_pauses(audio)
    assert len(fired) == 2


def test_higher_sensitivity_db_hears_less_as_speech():
    """Quiet speech over cbox's hiss: +3 dB hears it, +12 dB takes it for room."""
    from tuxtalk.chunking import PauseDetector
    audio = np.concatenate([loud_room(1.0), loud_speech(2.0, level=1000.0), loud_room(1.5),
                            loud_speech(2.0, level=1000.0), loud_room(1.5)])
    def cuts(db):
        det = PauseDetector(0.8, SR, speech_db=db)
        return sum(det.feed(b.tobytes()) for b in blocks(audio))
    assert cuts(3.0) == 2
    assert cuts(12.0) == 0


def test_snapshot_reports_what_the_detector_hears():
    from tuxtalk.chunking import PauseDetector
    det = PauseDetector(0.8, SR)
    for b in blocks(np.concatenate([loud_room(2.0), loud_speech(2.0), loud_room(1.2)])):
        det.feed(b.tobytes())
    snap = det.snapshot()
    assert 250 < snap.room < 450
    assert snap.threshold == pytest.approx(snap.room * 10 ** (6 / 20))
    assert snap.cuts == 1
    assert snap.silence_s > 0.2
    assert snap.level > 0


def test_default_sensitivity_is_six_db():
    from tuxtalk.chunking import DEFAULT_SPEECH_DB, PauseDetector
    assert DEFAULT_SPEECH_DB == 6.0
    assert PauseDetector(0.8, SR).speech_over_room == pytest.approx(1.995, abs=0.01)


# --- the chunk worker --------------------------------------------------------

def test_worker_runs_jobs_in_order():
    from tuxtalk.chunking import ChunkWorker
    w = ChunkWorker()
    done = []
    for i in range(5):
        w.submit(lambda i=i: (time.sleep(0.01 * (5 - i)), done.append(i)))
    assert w.drain(5)
    assert done == [0, 1, 2, 3, 4]


def test_a_failing_chunk_does_not_stop_the_next():
    from tuxtalk.chunking import ChunkWorker
    w = ChunkWorker()
    done = []

    def boom():
        raise RuntimeError("engine fell over")

    w.submit(lambda: done.append(1))
    w.submit(boom)
    w.submit(lambda: done.append(3))
    assert w.drain(5)
    assert done == [1, 3]


def test_drain_waits_for_the_job_in_progress():
    from tuxtalk.chunking import ChunkWorker
    w = ChunkWorker()
    w.submit(lambda: time.sleep(0.5))
    assert w.drain(0.1) is False
    assert w.drain(5) is True


def test_drain_with_nothing_queued_returns_at_once():
    from tuxtalk.chunking import ChunkWorker
    assert ChunkWorker().drain(0.01) is True


def test_jobs_run_holding_the_lock():
    from tuxtalk.chunking import ChunkWorker
    lock = threading.Lock()
    w = ChunkWorker(lock)
    seen = []
    w.submit(lambda: seen.append(lock.locked()))
    assert w.drain(5)
    assert seen == [True]


# --- settings ----------------------------------------------------------------

def test_chunk_settings_default_off():
    from tuxtalk.config import Settings
    s = Settings()
    assert s.chunked_dictation is False
    assert s.chunk_pause_seconds == 0.8


@pytest.mark.parametrize("value,ok", [(0.8, True), (0.3, True), (3.0, True),
                                      (0.2, False), (3.5, False)])
def test_chunk_pause_range(value, ok):
    from tuxtalk import config
    problems = config._validation_problems(config.Settings(chunk_pause_seconds=value))
    flagged = any(key == "chunk_pause_seconds" for key, _msg in problems)
    assert flagged is (not ok)


def test_chunk_settings_apply_without_a_restart():
    from tuxtalk.config import LIVE_APPLIED_KEYS
    assert {"chunked_dictation", "chunk_pause_seconds"} <= LIVE_APPLIED_KEYS


def test_chunk_setting_follows_config(monkeypatch):
    from types import SimpleNamespace
    from tuxtalk import app
    monkeypatch.setattr(app, "_ai_engine", None)
    on = SimpleNamespace(chunked_dictation=True, chunk_pause_seconds=1.0)
    app._apply_cleanup_settings(on)
    assert app._chunk_pause_seconds == 1.0
    assert app._chunk_cfg is on
    app._apply_cleanup_settings(SimpleNamespace(chunked_dictation=False, chunk_pause_seconds=1.0))
    assert app._chunk_pause_seconds == 0.0


def test_chunk_pause_stays_below_auto_stop(monkeypatch):
    from types import SimpleNamespace
    from tuxtalk import app
    monkeypatch.setattr(app, "_ai_engine", None)
    app._apply_cleanup_settings(SimpleNamespace(
        chunked_dictation=True, chunk_pause_seconds=1.5,
        auto_stop_silence=True, auto_stop_seconds=1.0))
    assert app._chunk_pause_seconds == pytest.approx(0.8)
    app._apply_cleanup_settings(SimpleNamespace(chunked_dictation=False))


# --- where a sentence ends at a pause (the engine's word, in context) ---------

@pytest.mark.parametrize("prev,joint,mark", [
    ("Wir treffen uns morgen.", "Wir treffen uns morgen um drei Uhr.", ""),
    ("Das ist gut.", "Das ist gut. Wir gehen.", "."),
    ("Kommst du?", "Kommst du? Ich warte.", "?"),
    ("Wir treffen uns morgen", "Wir treffen uns morgen, um drei.", ","),
    ("Die Straße.", "die strasse ist nass", ""),          # ß, casefolded
    ("Hallo Welt.", "Etwas ganz anderes.", None),       # no match: no verdict
    ("Wir gehen.", "Wir gehen.", None),                  # nothing after it
])
def test_boundary_mark(prev, joint, mark):
    from tuxtalk.chunking import boundary_mark
    assert boundary_mark(prev, joint) == mark


@pytest.mark.parametrize("prev,joint,heard", [
    ("then let the timer run out.", "then let the timer run out it", "it"),
    ("Wir treffen uns morgen.", "Wir treffen uns morgen, um drei Uhr.", "um drei Uhr."),
    ("Hallo Welt.", "Etwas ganz anderes.", None),
    ("Wir gehen.", "Wir gehen.", None),
])
def test_words_in_context(prev, joint, heard):
    from tuxtalk.chunking import words_in_context
    assert words_in_context(prev, joint) == heard


# --- chunk roles in the pipeline ---------------------------------------------

@pytest.fixture
def pipeline(monkeypatch):
    """A fake engine and fake typing around the real _transcribe_and_inject.
    texts: what the engine 'hears', one per call, in call order (a chunk after
    a held-back one is transcribed alone, then together with it).
    Returns (texts, typed, beeps)."""
    from tuxtalk import app, history
    texts, typed, beeps = [], [], []
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: texts.pop(0))

    def fake_inject(text, mode, t0, one_block=False):
        typed.append(text)
        app.state.last_inserted_text = text
        return "ok"

    monkeypatch.setattr(app, "_inject_text", fake_inject)
    monkeypatch.setattr(app, "_beep", lambda enabled, *tone: beeps.append(tone))
    monkeypatch.setattr(app, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: None)
    monkeypatch.setattr(app, "_adapt_to_app", False)
    monkeypatch.setattr(app, "_usage_stats", False)
    monkeypatch.setattr(history, "add_entry", lambda text, replaces=None: None)
    monkeypatch.setattr(app.state, "continue_mid_sentence", False)
    monkeypatch.setattr(app.state, "pending_boundary", None)
    monkeypatch.setattr(app.state, "last_inserted_text", "")
    return texts, typed, beeps


def frames_of(audio):
    return [audio.tobytes()]


def call(frames, role, auto_period=False):
    from tuxtalk import app
    app._transcribe_and_inject(frames, SR, True, True, False, None, True, auto_period,
                               "type", chunk_role=role)


def test_middle_chunk_types_without_the_ready_beep(pipeline):
    from tuxtalk import app
    texts, typed, beeps = pipeline
    texts.append("we meet")
    call(frames_of(speech(1.0)), "middle")
    assert typed == ["We meet"]
    assert app.READY_BEEP not in beeps


def test_middle_chunk_holds_back_its_final_mark(pipeline):
    """The engine heard the chunk alone and ended it with a period; that is a
    guess, so it is not typed yet (nor the space after it)."""
    from tuxtalk import app
    texts, typed, _ = pipeline
    texts.append("we meet.")
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["We meet"]
    assert app.state.pending_boundary["mark"] == "."


def test_context_without_a_period_carries_the_sentence_on(pipeline):
    texts, typed, _ = pipeline
    texts.extend(["we meet.", "at three.", "we meet at three."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "middle")
    assert typed == ["We meet", " ", "at three"]


def test_context_with_a_period_ends_the_sentence(pipeline):
    texts, typed, _ = pipeline
    texts.extend(["we meet.", "the end.", "we meet. The end."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "middle")
    assert typed == ["We meet", ". ", "The end"]


def test_context_with_a_comma_types_the_comma(pipeline):
    texts, typed, _ = pipeline
    texts.extend(["we meet.", "at three.", "we meet, at three."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "middle")
    assert typed == ["We meet", ", ", "at three"]


def test_no_verdict_keeps_the_engines_mark(pipeline):
    texts, typed, _ = pipeline
    texts.extend(["we meet.", "the end.", "something else entirely"])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "middle")
    assert typed == ["We meet", ". ", "The end"]


def test_last_chunk_gets_the_auto_period(pipeline):
    texts, typed, _ = pipeline
    texts.extend(["we meet", "at three", "we meet at three"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "last", auto_period=True)
    assert typed == ["We meet", " ", "at three. "]


def test_stop_after_a_pause_types_the_held_mark(pipeline):
    from tuxtalk import app
    texts, typed, beeps = pipeline
    texts.append("are you there?")
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call([], "last", auto_period=True)
    assert typed == ["Are you there", "? "]
    assert app.state.last_inserted_text == "Are you there? "
    assert beeps == [app.READY_BEEP]


def test_stop_after_a_pause_adds_the_auto_period(pipeline):
    texts, typed, _ = pipeline
    texts.append("we meet")
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call([], "last", auto_period=True)
    assert typed == ["We meet", ". "]


def test_a_dictated_period_is_not_held_back():
    """A voice command ("Punkt" -> ".") made the period: it is the speaker's own,
    not the engine's guess, so it is typed as it is."""
    from tuxtalk import app
    text = "Bei jedem Unterbruch ein. "
    assert app._hold_back_boundary(text, "bei jedem Unterbruch ein Punkt.") == (text, None)


def test_the_engines_period_is_held_back():
    from tuxtalk import app
    assert app._hold_back_boundary("Die Strasse ist nass. ", "die Straße ist nass.") == \
        ("Die Strasse ist nass", ".")


def test_a_line_break_is_not_held_back():
    from tuxtalk import app
    text = "Dear John,\n"
    assert app._hold_back_boundary(text, "dear john new line") == (text, None)


def test_a_command_after_a_pause_sees_the_finished_chunk(pipeline, monkeypatch):
    """'delete last sentence' must delete the chunk before it, mark and all."""
    from types import SimpleNamespace
    from tuxtalk import app
    texts, typed, _ = pipeline
    erased = []
    monkeypatch.setattr(app, "_output_backend",
                        lambda: SimpleNamespace(backspaces=lambda n: (erased.append(n), True)[1]))
    texts.extend(["we meet.", "delete last sentence", "we meet. Delete last sentence."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "middle")
    assert typed == ["We meet", ". "]
    assert erased == [len("We meet. ")]


def test_silent_middle_chunk_is_dropped_without_a_sound(pipeline):
    texts, typed, beeps = pipeline
    call(frames_of(noise(0.4)), "middle")
    assert typed == [] and beeps == []


def test_last_chunk_with_no_speech_still_beeps_ready(pipeline):
    from tuxtalk import app
    _, typed, beeps = pipeline
    call(frames_of(noise(0.4)), "last")
    assert typed == []
    assert beeps == [app.READY_BEEP]


def test_last_chunk_with_no_audio_beeps_ready(pipeline):
    from tuxtalk import app
    _, typed, beeps = pipeline
    call([], "last")
    assert typed == [] and beeps == [app.READY_BEEP]


def test_whole_dictation_is_unchanged(pipeline):
    """chunk_role=None: today's behaviour, a silent tap gets the cancel beep."""
    from tuxtalk import app
    _, typed, beeps = pipeline
    call(frames_of(noise(0.4)), None)
    assert typed == [] and beeps == [app.CANCEL_BEEP]


def test_held_mark_never_reaches_the_next_recording(pipeline):
    from tuxtalk import app
    texts, typed, _ = pipeline
    texts.extend(["we meet.", "hello there"])
    call(frames_of(speech(1.0)), "middle")
    app._arm_chunker(SR)            # the next recording starts
    assert app.state.pending_boundary is None
    call(frames_of(speech(1.0)), None)
    assert typed[1].strip() == "Hello there"


# --- chunks cut during a recording -------------------------------------------

@pytest.fixture
def recording(monkeypatch, pipeline):
    """A recording in progress at 16 kHz with chunking on, no real stream, a
    fresh chunk worker and the fake engine/typing from `pipeline`."""
    from types import SimpleNamespace
    from tuxtalk import app
    monkeypatch.setattr(app, "_chunk_worker_instance", None)
    monkeypatch.setattr(app, "_chunk_pause_seconds", 0.8)
    monkeypatch.setattr(app, "_chunk_cfg", SimpleNamespace(
        beeps=True, smart_quotes=True, notify=False, language=None,
        auto_space=True, auto_period=False, injection_mode="type"))
    monkeypatch.setattr(app, "recording_indicator", None)
    monkeypatch.setattr(app, "_stop_stream_safely", lambda: None)
    monkeypatch.setattr(app, "_resume_media_players", lambda: None)
    monkeypatch.setattr(app, "_notify_tray_recording_state", lambda *a: None)
    monkeypatch.setattr(app.state, "silence", None)
    monkeypatch.setattr(app.state, "recording_samplerate", SR, raising=False)
    app.state.frames = []
    app.state.was_cancelled = False
    app.state.edit_mode = False
    app.state.press_t0 = time.time() - 10
    app.state.is_recording = True
    app._arm_chunker(SR)
    yield pipeline
    app.state.is_recording = False
    app.state.chunker = None
    app.state.chunks_submitted = 0


def feed(audio):
    from tuxtalk import app
    for b in blocks(audio):
        app._sd_callback(b.reshape(-1, 1), len(b), None, None)


def stop(auto_period=False):
    from tuxtalk import app
    app.stop_recording(beeps_on=True, smart_quotes=True, notify_on=False,
                       language=None, auto_space=True, auto_period=auto_period,
                       injection_mode="type")


def test_two_pauses_give_three_chunks_in_order(recording):
    from tuxtalk import app
    texts, typed, beeps = recording
    texts.extend(["we meet.", "at three.", "we meet at three.",
                  "in the garden", "at three in the garden"])
    feed(np.concatenate([speech(1.0), noise(1.0), speech(1.0), noise(1.0), speech(0.8)]))
    stop()
    assert "".join(typed) == "We meet at three in the garden "
    assert beeps.count(app.READY_BEEP) == 1
    assert app.state.chunks_submitted == 2


def test_chunking_off_types_one_dictation(recording, monkeypatch):
    from tuxtalk import app
    texts, typed, beeps = recording
    monkeypatch.setattr(app, "_chunk_pause_seconds", 0.0)
    app._arm_chunker(SR)
    assert app.state.chunker is None
    texts.append("we meet at three in the garden")
    feed(np.concatenate([speech(1.0), noise(1.0), speech(1.0), noise(1.0), speech(0.8)]))
    stop()
    assert len(typed) == 1
    assert beeps.count(app.READY_BEEP) == 1


def test_a_voice_edit_is_never_chunked(recording):
    from tuxtalk import app
    app._arm_chunker(SR, edit_mode=True)
    assert app.state.chunker is None


def test_auto_stop_after_a_cut_beeps_ready_once(recording):
    """Review focus 1: the rest after the cut is only room noise."""
    from tuxtalk import app
    texts, typed, beeps = recording
    texts.append("we meet")
    feed(np.concatenate([speech(1.0), noise(1.2)]))
    stop()
    assert "".join(typed) == "We meet "
    assert beeps == [app.READY_BEEP]


def test_a_silent_rest_never_reaches_the_engine(recording):
    """After the last cut only the room was heard (1.2 s, too long for the tap
    check): the engine must not get it, or it invents a "Yeah."."""
    from tuxtalk import app
    texts, typed, beeps = recording
    texts.extend(["we meet", "yeah"])
    feed(np.concatenate([speech(1.0), noise(2.0)]))
    stop(auto_period=True)
    assert "".join(typed) == "We meet. "
    assert texts == ["yeah"]
    assert beeps == [app.READY_BEEP]


def test_cut_after_stop_snapshot_hands_nothing_on(recording):
    """Review focus 2: a block arriving after the stop took its snapshot must
    not cut a chunk from frames the stop already owns."""
    from tuxtalk import app
    feed(speech(1.0))
    chunker = app.state.chunker
    cap = app._finish_capture(beeps_on=False, notify_on=False)
    assert cap is not None
    app._cut_chunk(chunker)
    assert app.state.chunks_submitted == 0
    assert app._chunk_worker().drain(1)


def test_drop_tail_samples_keeps_partial_blocks():
    from tuxtalk import app
    frames = [b"\0\0" * 320, b"\0\0" * 320, b"\0\0" * 320]
    app._drop_tail_samples(frames, 700)   # two whole blocks fit, the third doesn't
    assert len(frames) == 1


def test_flatpak_stop_queues_the_last_chunk_on_the_worker(recording, monkeypatch):
    from types import SimpleNamespace
    from tuxtalk import app
    texts, typed, _ = recording
    texts.extend(["we meet", "at three", "we meet at three"])
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    monkeypatch.setattr(app, "_release_all_grabs", lambda: None)
    feed(np.concatenate([speech(1.0), noise(1.0), speech(0.8)]))
    svc = SimpleNamespace(input_device_idx=None, last_activity=0.0,
                          timeout_enabled=False, last_config_check=time.time())
    app._cmd_stop_recording.set()
    app._service_tick(app._chunk_cfg, svc)
    assert app._chunk_worker().drain(5)
    assert "".join(typed) == "We meet at three "


def test_arming_starts_the_worker(monkeypatch):
    """The worker must exist before the first cut, so the audio callback never
    has to build it (a thread start and an import on the audio thread)."""
    from tuxtalk import app
    monkeypatch.setattr(app, "_chunk_worker_instance", None)
    monkeypatch.setattr(app, "_chunk_pause_seconds", 0.8)
    app._arm_chunker(SR)
    assert app._chunk_worker_instance is not None
    app.state.chunker = None


def test_a_cut_queues_its_chunk_before_releasing_the_lock(recording, monkeypatch):
    """Otherwise the stop could slip in between counting the chunk and queuing
    it, and queue the last chunk ahead of this one."""
    from types import SimpleNamespace
    from tuxtalk import app
    held = []
    monkeypatch.setattr(app, "_chunk_worker_instance",
                        SimpleNamespace(submit=lambda job: held.append(app._chunk_lock.locked())))
    feed(np.concatenate([speech(1.0), noise(1.0)]))
    assert held == [True]


def test_a_recording_without_a_pause_says_why(recording, caplog):
    """When no pause was found, the log names the room level, the threshold and
    the loudest block, so a mic the detector can't read is easy to spot."""
    import logging
    from tuxtalk import app
    caplog.set_level(logging.INFO)
    feed(speech(2.0))
    app._finish_capture(beeps_on=False, notify_on=False)
    assert any("no pause found" in r.getMessage() and "room" in r.getMessage()
               for r in caplog.records)


# --- sensitivity and context settings ----------------------------------------

def test_new_chunk_settings_defaults():
    from tuxtalk.config import Settings
    s = Settings()
    assert s.chunk_speech_db == 6.0
    assert s.chunk_context_marks is True


@pytest.mark.parametrize("value,ok", [(6.0, True), (3.0, True), (12.0, True),
                                      (2.0, False), (13.0, False)])
def test_chunk_speech_db_range(value, ok):
    from tuxtalk import config
    problems = config._validation_problems(config.Settings(chunk_speech_db=value))
    assert any(k == "chunk_speech_db" for k, _ in problems) is (not ok)


def test_new_chunk_settings_apply_without_a_restart():
    from tuxtalk.config import LIVE_APPLIED_KEYS
    assert {"chunk_speech_db", "chunk_context_marks"} <= LIVE_APPLIED_KEYS


def test_service_reads_the_new_chunk_settings(monkeypatch):
    from types import SimpleNamespace
    from tuxtalk import app
    monkeypatch.setattr(app, "_ai_engine", None)
    app._apply_cleanup_settings(SimpleNamespace(
        chunked_dictation=True, chunk_speech_db=9.0, chunk_context_marks=False))
    assert app._chunk_speech_db == 9.0
    assert app._chunk_context_marks is False
    app._apply_cleanup_settings(SimpleNamespace(chunked_dictation=False))
    assert app._chunk_speech_db == 6.0
    assert app._chunk_context_marks is True


def test_speech_db_reaches_the_detector(monkeypatch):
    from tuxtalk import app
    monkeypatch.setattr(app, "_chunk_pause_seconds", 0.8)
    monkeypatch.setattr(app, "_chunk_speech_db", 9.0)
    monkeypatch.setattr(app, "_chunk_worker_instance", None)
    app._arm_chunker(SR)
    assert app.state.chunker.speech_over_room == pytest.approx(10 ** (9 / 20))
    app.state.chunker = None


def test_context_marks_off_types_the_engines_mark(pipeline, monkeypatch):
    """Off: the chunk is typed as Parakeet wrote it, mark and space included,
    and nothing waits for the next chunk."""
    from tuxtalk import app
    monkeypatch.setattr(app, "_chunk_context_marks", False)
    texts, typed, _ = pipeline
    texts.append("we meet.")
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["We meet. "]
    assert app.state.pending_boundary is None


# --- final review fixes ------------------------------------------------------

def test_context_marks_off_carries_on_after_a_comma(pipeline, monkeypatch):
    """Off, a chunk ending in a comma (or no mark) is mid-sentence: the next one
    must not start with a capital."""
    from tuxtalk import app
    monkeypatch.setattr(app, "_chunk_context_marks", False)
    texts, typed, _ = pipeline
    texts.extend(["we meet,", "at the station"])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "last")
    assert typed[1].strip().startswith("at the station")


def test_context_marks_off_does_not_carry_past_the_recording(pipeline, monkeypatch):
    from tuxtalk import app
    monkeypatch.setattr(app, "_chunk_context_marks", False)
    texts, typed, _ = pipeline
    texts.append("we meet,")
    call(frames_of(speech(1.0)), "middle")
    call([], "last")
    assert app.state.continue_mid_sentence is False


# --- a correction said after a pause (AI self-corrections on) -----------------

class FakeAI:
    """Stands in for the local AI: drops "three" and the cue, as the model does."""
    def __init__(self):
        self.asked = []

    def correct(self, text):
        self.asked.append(text)
        return "Meet at four." if "three" in text.lower() else text


@pytest.fixture
def ai_on(pipeline, monkeypatch):
    """`pipeline` with AI self-corrections on and backspaces recorded."""
    from types import SimpleNamespace
    from tuxtalk import app
    ai = FakeAI()
    erased = []
    monkeypatch.setattr(app, "_ai_corrections", True)
    monkeypatch.setattr(app, "_ai_engine", ai)
    monkeypatch.setattr(app, "_output_backend",
                        lambda: SimpleNamespace(backspaces=lambda n: (erased.append(n), True)[1]))
    monkeypatch.setattr(app.state, "chunk_typed", False)
    monkeypatch.setattr(app.state, "held_cue", "")
    monkeypatch.setattr(app.state, "last_inserted_window", None)
    texts, typed, beeps = pipeline
    return texts, typed, erased, ai


def test_correction_after_a_pause_fixes_the_phrase_before(ai_on):
    """"meet at three" ... "no wait, four": the AI hears both, the first
    phrase is backspaced and the fixed sentence typed in its place."""
    from tuxtalk import app
    texts, typed, erased, _ = ai_on
    texts.extend(["meet at three.", "no wait, four.", "meet at three. No wait, four."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "middle")
    assert erased == [len("Meet at three. ")]
    assert typed[-1] == "Meet at four"
    assert app.state.pending_boundary["mark"] == "."


def test_correction_on_the_last_phrase(ai_on):
    texts, typed, erased, _ = ai_on
    texts.extend(["meet at three.", "no wait, four.", "meet at three. No wait, four."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "last", auto_period=True)
    assert erased == [len("Meet at three. ")]
    assert typed[-1] == "Meet at four. "


def test_a_bare_cue_waits_for_the_value(ai_on):
    """"meet at three" ... "no wait" ... "four": nothing is typed for the cue
    alone; the value after it makes the correction."""
    texts, typed, erased, ai = ai_on
    texts.extend(["meet at three.", "no wait.", "meet at three. No wait.", "four."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "middle")
    assert typed == ["Meet at three", ". "]          # the cue is held
    call(frames_of(speech(1.0)), "last", auto_period=True)
    assert erased == [len("Meet at three. ")]
    assert typed[-1] == "Meet at four. "


def test_a_bare_cue_said_last_is_typed(ai_on):
    from tuxtalk import app
    texts, typed, erased, _ = ai_on
    texts.extend(["meet at three.", "no wait.", "meet at three. No wait."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call([], "last", auto_period=True)
    assert typed == ["Meet at three", ". ", "No wait. "]
    assert erased == [] and app.state.held_cue == ""


def test_no_correction_types_the_phrase_as_said(ai_on):
    texts, typed, erased, ai = ai_on
    texts.extend(["meet at three.", "in the garden.", "meet at three in the garden."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "middle")
    assert erased == [] and ai.asked == []
    assert typed == ["Meet at three", " ", "in the garden"]


def test_ai_unchanged_types_the_phrase_as_said(ai_on):
    """The AI found nothing to fix: nothing is erased."""
    texts, typed, erased, _ = ai_on
    texts.extend(["we meet.", "no wait, four.", "we meet. No wait, four."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "middle")
    assert erased == []
    assert typed[-1] == "No wait, four"


def test_correction_never_reaches_into_another_window(ai_on, monkeypatch):
    from tuxtalk import app
    texts, typed, erased, _ = ai_on
    texts.extend(["meet at three.", "no wait, four.", "meet at three. No wait, four."])
    call(frames_of(speech(1.0)), "middle")
    app.state.last_inserted_window = "kwrite"
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "konsole")
    call(frames_of(speech(1.0)), "middle")
    assert erased == []
    assert typed[-1] == "No wait, four"


def test_correction_never_reaches_into_the_dictation_before(ai_on):
    """A new recording's first phrase never changes the last recording's text."""
    from tuxtalk import app
    texts, typed, erased, _ = ai_on
    app.state.last_inserted_text = "Meet at three. "
    texts.append("no wait, four.")
    call(frames_of(speech(1.0)), "middle")
    assert erased == []


def test_ai_off_leaves_a_correction_after_a_pause_alone(ai_on, monkeypatch):
    from tuxtalk import app
    monkeypatch.setattr(app, "_ai_corrections", False)
    texts, typed, erased, _ = ai_on
    texts.extend(["meet at three.", "no wait, four.", "meet at three. No wait, four."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "middle")
    assert erased == []
    assert typed[-1] == "No wait, four"


def test_the_context_pass_hears_only_the_seconds_around_the_pause(pipeline, monkeypatch):
    """A long phrase isn't transcribed a second time whole."""
    from tuxtalk import app
    texts, typed, _ = pipeline
    heard = []
    real = app._transcribe_audio
    monkeypatch.setattr(app, "_transcribe_audio",
                        lambda audio, lang: (heard.append(len(audio) / SR), real(audio, lang))[1])
    texts.extend(["we meet.", "at three.", "we meet at three."])
    call(frames_of(speech(20.0)), "middle")
    call(frames_of(speech(20.0)), "middle")
    assert heard[:2] == [20.0, 20.0]
    assert heard[2] == pytest.approx(2 * app.CONTEXT_S + app.JOINT_GAP_S)
    assert typed == ["We meet", " ", "at three"]


def test_describe_says_how_close_a_pause_came():
    from tuxtalk.chunking import PauseDetector
    det = PauseDetector(0.8, SR)
    for b in blocks(np.concatenate([speech(1.0), noise(0.5), speech(1.0)])):
        assert not det.feed(b)
    assert 0.4 < det.longest_quiet < 0.6
    assert "longest quiet 0.5" in det.describe()


# --- one recording, one Recent Dictations entry -------------------------------

@pytest.fixture
def recent(pipeline, monkeypatch, tmp_path):
    """`pipeline` with the real Recent Dictations list (in a private runtime
    folder) and a recording just started."""
    from tuxtalk import app, history
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(history, "add_entry", REAL_ADD_ENTRY)    # `pipeline` stubbed it
    app._arm_chunker(SR)
    return pipeline


def test_a_recording_is_one_recent_dictation(recent):
    """Phrases typed at pauses make one entry, so Fix a Word and Paste Last
    get the whole recording, not its last phrase."""
    from tuxtalk import app, history
    texts, typed, _ = recent
    texts.extend(["we meet.", "the end.", "we meet. The end."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "last", auto_period=True)
    assert typed == ["We meet", ". ", "The end. "]
    assert history.get_entries() == ["We meet. The end."]
    app._arm_chunker(SR)            # the next recording is an entry of its own
    texts.append("hello there.")
    call(frames_of(speech(1.0)), None, auto_period=True)
    assert history.get_entries() == ["Hello there.", "We meet. The end."]


def test_a_correction_across_a_pause_leaves_one_entry(recent, ai_on):
    from tuxtalk import history
    texts, typed, erased, _ = ai_on
    texts.extend(["meet at three.", "no wait, four.", "meet at three. No wait, four."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "last", auto_period=True)
    assert typed[-1] == "Meet at four. "
    assert history.get_entries() == ["Meet at four."]


@pytest.fixture
def fix_word(recent, monkeypatch):
    """`recent`, with "fix that word" handing its in-place file to a list
    instead of opening the window. Returns (texts, typed, opened)."""
    import json
    from tuxtalk import explain
    opened = []

    def fake_start(module, *args):
        data = None
        if args:
            with open(args[1]) as f:
                data = json.load(f)
        opened.append(data)

    monkeypatch.setattr(explain, "start_helper_window", fake_start)
    texts, typed, _ = recent
    return texts, typed, opened


def say_fix_that_word(texts):
    from tuxtalk import app
    app._arm_chunker(SR)
    texts.append("fix that word")
    call(frames_of(speech(1.0)), None)


def test_fix_that_word_gets_the_whole_recording(fix_word):
    texts, typed, opened = fix_word
    texts.extend(["we meet.", "the end.", "we meet. The end."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "last", auto_period=True)
    say_fix_that_word(texts)
    assert opened[0]["text"] == "We meet. The end. "


def test_fix_that_word_after_a_delete_gets_what_is_left(fix_word, monkeypatch):
    from types import SimpleNamespace
    from tuxtalk import app
    texts, typed, opened = fix_word
    monkeypatch.setattr(app, "_output_backend",
                        lambda: SimpleNamespace(backspaces=lambda n: True))
    texts.extend(["we meet.", "the end is near.", "we meet. The end is near."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "last", auto_period=True)
    app._arm_chunker(SR)
    texts.append("delete last word")
    call(frames_of(speech(1.0)), None)
    left = app.state.last_inserted_text
    assert left and "near" not in left
    say_fix_that_word(texts)
    assert opened[0]["text"] == "We meet. " + left


def test_fix_that_word_after_a_window_change_gets_the_last_phrase(fix_word, monkeypatch):
    """The phrases landed in two windows: only the last one is at the cursor,
    so fixing in place may only count on that one."""
    from tuxtalk import app
    texts, typed, opened = fix_word
    windows = iter(["kwrite", "kwrite", "konsole"])

    def fake_inject(text, mode, t0, one_block=False):
        typed.append(text)
        app.state.last_inserted_text = text
        app.state.last_inserted_window = next(windows)
        return "ok"

    monkeypatch.setattr(app, "_inject_text", fake_inject)
    texts.extend(["we meet.", "the end.", "we meet. The end."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "last", auto_period=True)
    say_fix_that_word(texts)
    assert opened[0]["text"] == "The end. "


def test_fix_that_word_after_a_voice_move_gets_the_last_phrase(fix_word, monkeypatch):
    """A phrase moved the cursor ("go to the start of the line"): what came
    before isn't beside the cursor any more."""
    from tuxtalk import app
    texts, typed, opened = fix_word
    texts.extend(["we meet.", "the end.", "we meet. The end."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    app.state.last_inserted_text = ""       # what a voice move does
    app._forget_shown()
    texts.extend(["at noon.", "the end. At noon."])
    call(frames_of(speech(1.0)), "last", auto_period=True)
    say_fix_that_word(texts)
    assert opened[0]["text"] == "At noon. "


# --- "press enter" with Type at pause -------------------------------------------------

@pytest.fixture
def enter(pipeline, monkeypatch):
    """The pipeline, with Enter presses recorded among the typing as "<ENTER>"."""
    from tuxtalk import app
    texts, typed, beeps = pipeline

    def fake_enter(key, why):
        typed.append(f"<{key}>")
        app.state.last_inserted_text = ""
        return True
    monkeypatch.setattr(app, "_press_key", fake_enter)
    monkeypatch.setattr(app, "_press_enter_after", False)
    monkeypatch.setattr(app.state, "chunk_typed", False)
    monkeypatch.setattr(app.state, "held_cue", "")
    return texts, typed, beeps


def test_press_enter_at_the_end_of_a_phrase_sends_it(enter):
    """Hands-free: "see you at noon, press enter", then a pause. The sentence
    is finished (no mark held back for later) and sent at once, and what's
    said next starts a new message."""
    from tuxtalk import app
    texts, typed, _ = enter
    texts.extend(["see you at noon, press enter", "how are you"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["See you at noon.", "<ENTER>"]
    assert app.state.pending_boundary is None and not app.state.chunk_typed
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed[2:] == ["How are you"]


def test_an_enter_that_couldnt_be_pressed_leaves_the_message_open(enter, monkeypatch):
    """A Right Ctrl stuck down stopped the Enter: the phrase is still in the
    box, so "edit that" must still reach all of it, not start afresh."""
    from tuxtalk import app
    texts, typed, _ = enter
    monkeypatch.setattr(app, "_press_key", lambda key, why: False)
    monkeypatch.setattr(app.state, "recording_serial", 7)
    texts.extend(["see you at noon, press enter", "press enter"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["See you at noon."]
    assert app.state.shown_text == "See you at noon."
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert app.state.shown_text == "See you at noon."


def test_press_enter_after_a_pause_ends_the_phrase_before_it(enter):
    """ "we meet at three" ... pause ... "press enter": the held-back end of
    the phrase is typed (the auto-period, no space) before Enter, with no
    context pass deciding a mark against the words "press enter"."""
    from tuxtalk import app
    texts, typed, beeps = enter
    texts.extend(["we meet at three", "press enter"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["We meet at three", ".", "<ENTER>"]
    assert texts == []                       # transcribed once each, no joint pass
    assert app.READY_BEEP not in beeps       # mid-recording: no beep
    assert app.state.pending_boundary is None


def test_press_tab_at_the_end_of_a_phrase_leaves_the_box_without_a_mark(enter):
    """Hands-free in a form: "john, press tab", a pause, "smith": no period
    or space in the first box, and the next one starts afresh."""
    from tuxtalk import app
    texts, typed, _ = enter
    texts.extend(["john, press tab", "smith"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["John", "<TAB>"]
    assert app.state.pending_boundary is None and not app.state.chunk_typed
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed[2:] == ["Smith"]


def test_press_tab_after_a_pause_types_no_mark_before_it(enter):
    from tuxtalk import app
    texts, typed, _ = enter
    texts.extend(["john", "press tab"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["John", "<TAB>"]
    assert texts == []                       # no context pass against "press tab"
    assert app.state.pending_boundary is None


def test_press_enter_after_each_dictation_waits_for_the_end(enter, monkeypatch):
    from tuxtalk import app
    texts, typed, _ = enter
    monkeypatch.setattr(app, "_press_enter_after", True)
    texts.extend(["we meet", "at three", "we meet at three"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert "<ENTER>" not in typed
    call(frames_of(speech(1.0)), "last", auto_period=True)
    assert typed == ["We meet", " ", "at three.", "<ENTER>"]


def test_press_enter_after_each_dictation_when_it_ends_in_a_pause(enter, monkeypatch):
    from tuxtalk import app
    texts, typed, _ = enter
    monkeypatch.setattr(app, "_press_enter_after", True)
    texts.append("we meet")
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    app.state.chunk_typed = True
    call([], "last", auto_period=True)
    assert typed == ["We meet", ".", "<ENTER>"]


def test_a_press_enter_misheard_alone_is_caught_by_the_context_pass(enter):
    """Ron, 2026-10-17: alone the short phrase came out "Press center.", heard
    with the end of the one before it was "... to say. Press enter."."""
    texts, typed, _ = enter
    texts.extend(["we meet at three", "Press center.", "We meet at three. Press enter."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["We meet at three", ".", "<ENTER>"]


def test_words_the_context_pass_doesnt_hear_as_press_enter_stay_words(enter):
    texts, typed, _ = enter
    texts.extend(["we meet at three", "press center.", "We meet at three. Press center."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert "<ENTER>" not in typed and typed[-1] == "Press center"


def test_a_longer_phrase_is_never_turned_into_press_enter(enter):
    texts, typed, _ = enter
    texts.extend(["we meet at three", "then go to the press center",
                  "We meet at three. Then go to the press enter."])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert "<ENTER>" not in typed


def test_a_sentence_made_up_for_a_short_phrase_gives_way_to_the_context(pipeline):
    """Ron, 2026-10-18: he said "it" after a pause; alone, 1.7 s of audio came
    out "It's a little bit more than a little bit.", while heard with the
    phrase before it was "... run out it"."""
    texts, typed, _ = pipeline
    texts.extend(["then let the timer run out", "It's a little bit more than a little bit.",
                  "then let the timer run out it"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.7)), "middle", auto_period=True)
    assert "".join(typed) == "Then let the timer run out it"


def test_a_longer_phrase_keeps_the_words_it_was_heard_with(pipeline):
    texts, typed, _ = pipeline
    texts.extend(["then let the timer run out", "and it inserted a carriage return.",
                  "then let the timer run out and it inserted"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(3.0)), "middle", auto_period=True)
    assert "".join(typed) == "Then let the timer run out and it inserted a carriage return"


def test_a_short_phrase_the_context_hears_differently_keeps_its_own_words(pipeline):
    """Only fewer words in context: a word heard differently is the engine's
    call either way, and alone it heard this phrase on its own terms."""
    texts, typed, _ = pipeline
    texts.extend(["we meet at three", "at the press center.",
                  "we meet at three at the press enter"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    call(frames_of(speech(1.5)), "middle", auto_period=True)
    assert "".join(typed) == "We meet at three at the press center"


def test_a_phrase_being_worked_on_holds_hands_free_open(monkeypatch):
    """_transcribe_chunk holds the silence detector for as long as the phrase
    is being transcribed, edited and typed, and releases it after."""
    from tuxtalk import app
    events = []

    class Detector:
        def hold(self):
            events.append("hold")

        def release(self):
            events.append("release")
    monkeypatch.setattr(app.state, "silence", Detector())
    from tuxtalk.config import Settings
    monkeypatch.setattr(app, "_chunk_cfg", Settings())
    monkeypatch.setattr(app, "_transcribe_and_inject", lambda *a, **k: events.append("work"))
    app._transcribe_chunk([b""], SR, "middle")
    assert events == ["hold", "work", "release"]


def test_a_held_mark_stays_in_the_window_its_phrase_went_to(pipeline, monkeypatch):
    """Ron, 2026-10-17: "What's next" typed into the wrong window, a click into
    the chat, "What's next" again: the chat got "? What's next". The first
    phrase's held "?" belongs in the first window; the chat starts afresh."""
    from tuxtalk import app
    texts, typed, _ = pipeline
    focus = {"now": "brave-browser"}
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: focus["now"])
    texts.extend(["what's next?", "what's next?"])
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    app.state.pending_boundary["window"] = "brave-browser"
    focus["now"] = "com.anthropic.Claude"             # the click into the chat
    call(frames_of(speech(1.0)), "middle", auto_period=True)
    assert typed == ["What's next", "What's next"]
    assert texts == []                                # no context pass across windows


def test_a_question_goes_on_across_a_pause(pipeline):
    """Ron, 2026-10-18: "do you think ... feature" (pause) "to make it work
    better" came out ending in a period. The engine heard a question before
    the pause and a sentence going on across it, so the question ends at the
    end of the next phrase."""
    from tuxtalk import app
    texts, typed, _ = pipeline
    texts.extend(["do you think we need a setting?", "to make it work better.",
                  "do you think we need a setting to make it work better."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "last", auto_period=True)
    assert typed[:2] == ["Do you think we need a setting", " "]
    assert typed[2].rstrip() == "to make it work better?"
    assert app.state.question_open is False


def test_a_question_that_ended_at_the_pause_is_not_carried(pipeline):
    texts, typed, _ = pipeline
    texts.extend(["is it ready?", "yes it is.", "is it ready? Yes it is."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "last", auto_period=True)
    assert typed[1] == "? "
    assert typed[2].rstrip() == "Yes it is."


def test_a_carried_question_skips_a_phrase_with_two_sentences(pipeline):
    """The question ended somewhere in there; guessing where would be worse."""
    texts, typed, _ = pipeline
    texts.extend(["do you think we should", "go now. I do.",
                  "do you think we should go now. I do."])
    call(frames_of(speech(1.0)), "middle")
    call(frames_of(speech(1.0)), "last", auto_period=True)
    assert typed[-1].rstrip() == "go now. I do."


def test_a_recording_that_opens_with_digital_silence_still_learns_the_room():
    """A first block of zeros held the noise floor at 0 for good: steady fan
    noise then counted as speech and hands-free auto-stop never fired."""
    from tuxtalk.silence import NoiseFloor
    floor = NoiseFloor()
    floor.update(0.0, 0.03)
    heard = [floor.update(400.0, 0.03) for _ in range(100)]
    assert not heard[-1], "steady room noise still counts as speech"
