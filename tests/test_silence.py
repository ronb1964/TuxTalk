"""Hands-free auto-stop: the silence detector, on synthetic audio.

Audio is built from quiet room noise and louder "speech" (noise plus a tone),
fed in 20 ms blocks the way the microphone callback delivers it.
"""
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from tuxtalk import beeps, silence
from tuxtalk.silence import SilenceDetector
from tuxtalk import recording_state as rs
from tuxtalk import live_settings as live
from tuxtalk import tray_link
from tuxtalk import engine
from tuxtalk import notices
from tuxtalk import recording
from tuxtalk import pipeline

SR = 16000
BLOCK = 320   # 20 ms
rng = np.random.default_rng(0)


def noise(seconds, level=60.0):
    return (rng.normal(0, level, int(seconds * SR))).astype(np.int16)


def speech(seconds, level=3000.0, noise_level=60.0):
    """Syllables: 180 ms of voice, then a 70 ms gap where only the room is
    heard, like real speech (a detector must not rely on a gapless tone)."""
    t = np.arange(int(seconds * SR)) / SR
    voiced = (t % 0.25) < 0.18
    tone = np.sin(2 * np.pi * 220 * t) * level * voiced
    return (tone + rng.normal(0, noise_level, t.size)).astype(np.int16)


def run(audio, silence_seconds=1.5, block=BLOCK):
    """Feed audio in blocks of *block* samples; return (seconds at which it
    fired or None, detector)."""
    det = SilenceDetector(silence_seconds, SR)
    for i in range(0, len(audio), block):
        if det.feed(audio[i:i + block].tobytes()):
            return (i + block) / SR, det
    return None, det


def test_stops_after_the_speaker_goes_quiet():
    audio = np.concatenate([noise(0.5), speech(2.0), noise(3.0)])
    at, _ = run(audio, 1.5)
    assert at == pytest.approx(0.5 + 2.0 + 1.5, abs=0.1)


def test_never_stops_before_the_speaker_starts():
    """A slow start (long silence before talking) is not "done"."""
    at, _ = run(np.concatenate([noise(6.0), speech(1.0)]), 1.5)
    assert at is None


def test_a_click_or_cough_does_not_count_as_talking():
    audio = np.concatenate([noise(1.0), speech(0.1), noise(4.0)])
    at, _ = run(audio, 1.5)
    assert at is None


def test_a_thinking_pause_shorter_than_the_setting_does_not_stop_it():
    audio = np.concatenate([noise(0.3), speech(1.0), noise(1.2), speech(1.0), noise(3.0)])
    at, _ = run(audio, 1.5)
    assert at == pytest.approx(0.3 + 1.0 + 1.2 + 1.0 + 1.5, abs=0.1)


def test_works_in_a_noisier_room():
    """Background noise well above a quiet room's still reads as silence."""
    audio = np.concatenate([noise(0.5, 400), speech(2.0, 4000, 400), noise(3.0, 400)])
    at, _ = run(audio, 1.5)
    assert at == pytest.approx(4.0, abs=0.1)


def test_fires_only_once():
    audio = np.concatenate([speech(1.0), noise(3.0), speech(1.0), noise(3.0)])
    det = SilenceDetector(1.0, SR)
    fired = sum(det.feed(audio[i:i + BLOCK].tobytes()) for i in range(0, len(audio), BLOCK))
    assert fired == 1


def test_trims_only_the_measured_silent_tail():
    _, det = run(np.concatenate([speech(2.0), noise(3.0)]), 2.0)
    assert det.fired
    # 2.0 s of silence measured, 0.3 s kept for the last word's decay.
    assert det.tail_to_trim() == pytest.approx(int(1.7 * SR), abs=BLOCK)


def test_nothing_is_trimmed_unless_it_fired():
    _, det = run(np.concatenate([speech(2.0), noise(0.5)]), 2.0)
    assert not det.fired and det.tail_to_trim() == 0


def test_talking_instantly_with_no_quiet_gaps_still_counts_as_speech():
    """Even speech whose gaps never get quiet is recognised when it starts at
    once."""
    t = np.arange(int(2.0 * SR)) / SR
    gapless = (np.sin(2 * np.pi * 220 * t) * 3000).astype(np.int16)
    at, _ = run(np.concatenate([gapless, noise(3.0)]), 1.5)
    assert at == pytest.approx(3.5, abs=0.1)


def test_talking_the_instant_recording_starts_still_counts_as_speech():
    """The first block cannot be taken as the room's background noise."""
    at, _ = run(np.concatenate([speech(2.0), noise(3.0)]), 1.5)
    assert at == pytest.approx(3.5, abs=0.1)


# --- Wiring into the service -------------------------------------------------

def _start(monkeypatch, from_toggle=True, seconds=1.0, mic_delay=0.0, beeps_on=False):
    """A recording started as the service starts one, on a microphone that
    takes *mic_delay* to open."""
    monkeypatch.setattr(live, "auto_stop_seconds", seconds)

    def open_microphone(rec, idx):
        time.sleep(mic_delay)
        rec.samplerate = SR
    monkeypatch.setattr(recording, "open_input_stream", open_microphone)
    if not beeps_on:
        monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *a: None)
    monkeypatch.setattr(recording, "indicator", None, raising=False)
    assert recording.start_recording(beeps_on, False, 0, from_toggle=from_toggle)
    recording.stop_requested.clear()


def _speak_then_go_quiet(quiet_seconds=3.0):
    audio = np.concatenate([speech(1.0), noise(quiet_seconds)])
    for i in range(0, len(audio), BLOCK):
        recording._sd_callback(rs.state.current, audio[i:i + BLOCK].reshape(-1, 1),
                                BLOCK, None, None)


def test_a_single_tap_is_a_plain_toggle_however_long_the_pause(monkeypatch):
    _start(monkeypatch)
    assert rs.state.current.silence is None
    _speak_then_go_quiet(quiet_seconds=8.0)
    assert not rs.state.current.went_quiet
    rs.state.is_recording = False


def test_a_double_tap_switches_to_hands_free_and_it_stops_itself(monkeypatch):
    _start(monkeypatch)
    assert recording.toggle_pressed_while_recording(beeps_on=False) is True   # 2nd tap: keep going
    assert rs.state.current.silence is not None
    _speak_then_go_quiet()
    assert rs.state.current.went_quiet
    rs.state.is_recording = False


def test_a_double_tap_at_a_relaxed_pace_still_switches_to_hands_free(monkeypatch):
    """Issue #21: taps about half a second apart, a normal pace for someone
    whose hands are slow, stopped the recording instead."""
    _start(monkeypatch)
    rs.state.current.toggle_started_at -= 0.55
    assert recording.toggle_pressed_while_recording(beeps_on=False) is True
    assert rs.state.current.silence is not None
    rs.state.is_recording = False


def _goes_quiet():
    """After the second tap the room goes quiet: (seconds of quiet until the
    recording stopped by itself, or None if it didn't; whether it stopped as
    one in which nothing was said, which drops it instead of typing it)."""
    quiet = noise(12.0)
    for i in range(0, len(quiet), BLOCK):
        recording._sd_callback(rs.state.current, quiet[i:i + BLOCK].reshape(-1, 1),
                                BLOCK, None, None)
        if rs.state.current.went_quiet:
            return (i + BLOCK) / SR, rs.state.current.silence.nothing_said
    return None, False


def _stopped_to_type_it():
    """Going quiet stops the recording the usual way, after the auto-stop
    setting's second of quiet, to type what was said."""
    stopped_after, nothing_said = _goes_quiet()
    rs.state.is_recording = False
    return stopped_after == pytest.approx(1.0, abs=0.05) and not nothing_said


def _say(audio):
    for i in range(0, len(audio), BLOCK):
        recording._sd_callback(rs.state.current, audio[i:i + BLOCK].reshape(-1, 1),
                                BLOCK, None, None)


def test_a_word_said_before_the_second_tap_is_kept(monkeypatch):
    """Tap, say a quick word, tap again, then nothing more: the word is
    dictation, so going quiet stops the recording to type it, not to drop
    it as "nothing said"."""
    _start(monkeypatch)
    _say(speech(0.4))
    assert recording.toggle_pressed_while_recording(beeps_on=False) is True
    assert _stopped_to_type_it()


# --- The start beep, picked up by the microphone -------------------------------
# With speakers near the microphone, what it records before the second tap
# holds the start beep. Where in the recording is never known for sure: an
# output or microphone can be slower than it reports (a Bluetooth speaker,
# the sound server's own buffers), so the beep is told apart by its pitch.
# How these limits were measured, and where the recorded words come from:
# tests/data/README.md.

def _real_word(name, gain=1.0, rate=SR):
    """A quick word said by a person, at *gain*, at *rate*: "ask" or "not",
    cut from whisper.cpp's sample of John F. Kennedy's inaugural address."""
    with wave.open(str(Path(__file__).with_name("data") / f"jfk-{name}.wav")) as w:
        assert w.getframerate() == SR
        word = np.frombuffer(w.readframes(w.getnframes()), np.int16) * gain
    return np.interp(np.arange(int(len(word) * rate / SR)) * SR / rate, np.arange(len(word)), word)


# How loud the beep reaches the microphone, as (times the level it plays at,
# what the speaker and microphone do to it). Played, the beep peaks at 0.2 of
# full scale, so 5.5 times is clipped at 1.1 times full scale, 8 at 1.6 and
# 20 at 4.
BEEP_HEARD = {"faint": (0.1, "clean"), "as played": (1.0, "clean"),
              "near full scale": (4.0, "clean"), "distorted": (1.0, "distorted"),
              "distorted near full scale": (4.0, "distorted"),
              "clipped at 1.1x full scale": (5.5, "clean"),
              "clipped at 1.6x full scale": (8.0, "clean"),
              "saturated at 1.6x full scale": (8.0, "saturated"),
              "clipped at 4x full scale": (20.0, "clean"),
              "saturated at 4x full scale": (20.0, "saturated")}


def _picked_up_beep(level, shape, rate=SR):
    """The start beep as the microphone records it: the tone TuxTalk plays,
    *level* times as loud; "distorted" by a small speaker, with 2nd and 3rd
    harmonics 20 dB down, and squashed as it nears full scale; "saturated" by
    an overloaded microphone, squashed smoothly; "clean" is clipped where it
    passes full scale, as the recording is (_recording)."""
    freq, length = beeps.START_BEEP
    heard = beeps.tone(freq, length, rate).astype(np.float64)
    if shape == "distorted":
        heard += 0.1 * (beeps.tone(2 * freq, length, rate) + beeps.tone(3 * freq, length, rate))
    heard *= level
    return (heard if shape == "clean" else np.tanh(heard)) * 32767


def _recording(sounds, room, seconds=1.2, rate=SR):
    """What the microphone recorded before the second tap: the room (int16
    RMS *room*) and each (sound, where it starts in seconds) of *sounds*,
    cut off where the recording starts and ends, and clipped at full scale."""
    heard = np.random.default_rng(1).normal(0, room, int(seconds * rate))
    for sound, at in sounds:
        start = int(at * rate)
        first, last = max(0, start), min(len(heard), start + len(sound))
        heard[first:last] += sound[first - start:last - start]
    return np.clip(heard, -32768, 32767).astype(np.int16)


def _nothing_said(monkeypatch, heard, beeps_on=True):
    """The microphone records *heard*, the second tap of a double-tap comes,
    and then the room goes quiet: whether the recording stops as one with
    nothing said (dropped, rather than typed). It must stop by itself."""
    _start(monkeypatch, beeps_on=beeps_on)
    _say(heard)
    assert recording.toggle_pressed_while_recording(beeps_on=False) is True
    stopped_after, nothing_said = _goes_quiet()
    rs.state.is_recording = False
    assert stopped_after is not None, "the recording never stopped by itself"
    return nothing_said


# Where the beep reaches the recording: at once; 0.4 and 0.6 s late; already
# playing as the microphone opened; still playing at the second tap.
BEEP_AT = {"at once": 0.0, "0.4 s late": 0.4, "0.6 s late": 0.6,
           "before the mic opened": -0.06, "still playing at the tap": 1.12}


@pytest.mark.parametrize("room", [6.0, 300.0], ids=["silent room", "noisy room"])
@pytest.mark.parametrize("level,shape", BEEP_HEARD.values(), ids=BEEP_HEARD.keys())
@pytest.mark.parametrize("beep_at", BEEP_AT.values(), ids=BEEP_AT.keys())
def test_the_start_beep_picked_up_by_the_mic_is_not_a_word(
        monkeypatch, beep_at, level, shape, room):
    """A double-tap with nothing said but the start beep is nothing said."""
    heard = _recording([(_picked_up_beep(level, shape), beep_at)], room)
    assert _nothing_said(monkeypatch, heard)


@pytest.mark.parametrize("name", ["as played", "near full scale", "clipped at 1.6x full scale"])
def test_the_start_beep_is_not_a_word_wherever_it_falls_in_a_block(monkeypatch, name):
    """The microphone's sound comes in 20 ms blocks, and the beep starts and
    ends anywhere in one. A beep that clicked as it started and stopped
    counted as a word at some of them."""
    for ms in range(20):
        heard = _recording([(_picked_up_beep(*BEEP_HEARD[name]), 0.4 + ms / 1000)], 6.0)
        assert _nothing_said(monkeypatch, heard), f"the beep {ms} ms into a block"


# Rooms from silent to just under speaking level (int16 RMS 500), where the
# beep's limits were measured; from about 480 up the room itself counts as a
# word (silence._loud_seconds).
ROOMS = {"silent room": 6.0, "quiet room": 60.0, "noisy room": 300.0, "loud room": 450.0}


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
@pytest.mark.parametrize("room", ROOMS.values(), ids=ROOMS.keys())
@pytest.mark.parametrize("name", BEEP_HEARD)
def test_of_a_start_beep_one_block_at_most_counts(name, room, rate):
    """Wherever it falls in a 20 ms block, and at the rate a microphone that
    only takes its own records at (often 48 kHz, where the beep has more
    harmonics): a quarter of the shortest word, so a key click with it is
    still not one. In a loud room a faint beep is loud only with the room's
    help, and a share of 0.4 let it count for two blocks."""
    for ms in range(20):
        heard = _recording([(_picked_up_beep(*BEEP_HEARD[name], rate=rate), 0.4 + ms / 1000)],
                           room, rate=rate) / 32768.0
        assert silence._loud_seconds(heard, rate, beeps.START_BEEP[0]) <= 0.02, f"{ms} ms"


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
@pytest.mark.parametrize("room", ROOMS.values(), ids=ROOMS.keys())
def test_the_room_on_its_own_is_no_word(room, rate):
    """Up to just under speaking level, steady room noise is no word, with
    the start beep's pitch filtered out or not."""
    heard = _recording([], room, rate=rate) / 32768.0
    assert not silence.has_a_word(heard, rate)
    assert not silence.has_a_word(heard, rate, ignoring_hz=beeps.START_BEEP[0])


# (where the word starts, where the beep starts), in seconds
WORD_AND_BEEP = {"word before the beep": (0.02, 0.6), "word after the beep": (0.3, 0.0),
                 "word over the beep": (0.25, 0.3)}


@pytest.mark.parametrize("gain", [1.0, 0.1], ids=["spoken", "spoken softly"])
@pytest.mark.parametrize("word", ["ask", "not"])
@pytest.mark.parametrize("word_at,beep_at", WORD_AND_BEEP.values(), ids=WORD_AND_BEEP.keys())
def test_a_quick_word_said_with_the_start_beep_is_kept(monkeypatch, word_at, beep_at, word,
                                                       gain):
    """A real word said before the second tap counts, wherever the beep the
    mic picked up falls: going quiet after it types it."""
    heard = _recording([(_picked_up_beep(1.0, "distorted"), beep_at),
                        (_real_word(word, gain), word_at)], room=60.0)
    _start(monkeypatch, beeps_on=True)
    _say(heard)
    assert recording.toggle_pressed_while_recording(beeps_on=False) is True
    assert _stopped_to_type_it()


@pytest.mark.parametrize("beeps_on,beep_heard", [(True, False), (True, True), (False, False)],
                         ids=["beep not picked up", "beep picked up", "beeps off"])
def test_a_word_said_very_softly_counts_with_the_start_beep_or_without(
        monkeypatch, beeps_on, beep_heard):
    """A soft "not" on a quiet microphone in a silent room is only just loud
    enough: a filter that took a third of every pitch away made it nothing."""
    sounds = [(_real_word("not", 0.01), 0.3)]
    if beep_heard:
        sounds.append((_picked_up_beep(1.0, "clean"), 0.0))
    assert not _nothing_said(monkeypatch, _recording(sounds, room=6.0), beeps_on=beeps_on)


# How much louder a word must be to count with the start beep's pitch filtered
# out, in dB, as measured: in a loud room the room's own unevenness is as loud
# as a soft word over it.
FILTER_COST_DB = {6.0: 0.5, 60.0: 0.5, 300.0: 0.5, 450.0: 2.0}


@pytest.mark.parametrize("beep_heard", [False, True], ids=["no beep heard", "beep heard"])
@pytest.mark.parametrize("rate", [16000, 44100, 48000])
@pytest.mark.parametrize("room", ROOMS.values(), ids=ROOMS.keys())
@pytest.mark.parametrize("word", ["ask", "not"])
def test_a_word_only_just_loud_enough_still_counts_with_the_filter(word, room, rate, beep_heard):
    """Said ever more softly, a quick word counts with the start beep's pitch
    filtered out down to within FILTER_COST_DB of where it stops counting
    without the filter, picked up with the beep or not. Measured against the
    room as recorded, the part of the room's noise the filter takes looked
    like lost speech: in a room of 450 these two words had to be 4 dB
    louder (up to 16 dB over all the recordings tests/data/README.md
    describes)."""
    def heard(db, beep):
        sounds = [(_real_word(word, 10 ** (db / 20), rate), 0.3)]
        if beep:
            sounds.append((_picked_up_beep(1.0, "clean", rate), 0.0))
        return _recording(sounds, room, rate=rate) / 32768.0

    softest = 0.0
    while silence.has_a_word(heard(softest - 0.5, False), rate):
        softest -= 0.5
    assert softest < -20, "the word should count when said softly"
    for db in np.arange(softest + FILTER_COST_DB[room], 0.25, 0.5):
        assert silence.has_a_word(heard(db, beep_heard), rate, ignoring_hz=beeps.START_BEEP[0]), \
            f"{db} dB"


def test_with_beeps_off_nothing_is_taken_for_the_start_beep(monkeypatch):
    """No start beep played, so a sound at its pitch is a sound like any
    other: someone whistling, or another app's alert."""
    heard = _recording([(_picked_up_beep(1.0, "clean"), 0.4)], room=6.0)
    assert not _nothing_said(monkeypatch, heard, beeps_on=False)


def test_a_word_kept_with_the_start_beep_is_transcribed(monkeypatch):
    """Kept, the word goes on to be transcribed, all of it."""
    word = _recording([(_real_word("ask"), 0.3), (_picked_up_beep(1.0, "distorted"), 0.0)],
                      room=60.0)
    _start(monkeypatch, beeps_on=True)
    _say(word)
    assert recording.toggle_pressed_while_recording(beeps_on=False) is True
    stopped_after, nothing_said = _goes_quiet()
    assert stopped_after is not None and not nothing_said
    monkeypatch.setattr(recording, "stop_stream_safely", lambda rec: None)
    rs.state.current.press_t0 = time.time() - 5
    frames, rate = recording.finish_capture(rs.state.current, False, False)
    assert rate == SR
    assert b"".join(frames).startswith(word.tobytes())


def test_a_quick_second_tap_counts_when_the_recording_was_slow_to_start(monkeypatch):
    """Issue #21: the keys are read on the thread that starts the recording,
    so a second tap made while the microphone and the start beep took most
    of a second is only read afterwards. It is still a double-tap."""
    _start(monkeypatch, mic_delay=0.9)
    assert recording.toggle_pressed_while_recording(beeps_on=False) is True
    rs.state.is_recording = False


def _slow_start_beep(monkeypatch):
    """Every sound takes half a second to be heard, as on a slow output.
    Returns each wait for one: what it waited for, and when it ended."""
    from tuxtalk import beeps
    waits = []

    def wait_until_played(limit=beeps.WAIT_LIMIT_S, *, over=None):
        time.sleep(0.5)
        waits.append((over, time.time()))
        return True
    monkeypatch.setattr(beeps, "wait_until_played", wait_until_played)
    return waits


def test_the_double_tap_is_timed_from_the_start_beep_heard(monkeypatch):
    """Issue #21: the user waits for the start beep before the second tap,
    and on a slow output it comes most of a second after the first."""
    waits = _slow_start_beep(monkeypatch)
    _start(monkeypatch, beeps_on=True)
    (waited_for, ended), = waits
    assert waited_for is rs.state.current.start_beep is not None
    assert rs.state.current.toggle_started_at >= ended
    rs.state.is_recording = False


@pytest.mark.parametrize("from_toggle,beeps_on", [(False, True), (True, False)],
                         ids=["hold start", "beeps off"])
def test_no_start_waits_for_a_start_beep_it_doesnt_have_to(monkeypatch, from_toggle, beeps_on):
    waits = _slow_start_beep(monkeypatch)
    _start(monkeypatch, from_toggle=from_toggle, beeps_on=beeps_on)
    assert waits == []
    rs.state.is_recording = False


def test_a_slow_second_tap_is_a_normal_stop(monkeypatch):
    _start(monkeypatch)
    rs.state.current.toggle_started_at -= recording.DOUBLE_TAP_S + 0.1
    assert recording.toggle_pressed_while_recording(beeps_on=False) is False
    assert rs.state.current.silence is None
    rs.state.is_recording = False


def test_a_third_tap_after_a_double_tap_stops(monkeypatch):
    _start(monkeypatch)
    assert recording.toggle_pressed_while_recording(beeps_on=False) is True
    assert recording.toggle_pressed_while_recording(beeps_on=False) is False
    rs.state.is_recording = False


def test_double_tap_does_nothing_special_with_the_option_off(monkeypatch):
    _start(monkeypatch, seconds=0.0)
    assert recording.toggle_pressed_while_recording(beeps_on=False) is False
    rs.state.is_recording = False


def test_a_hold_recording_cannot_become_hands_free(monkeypatch):
    _start(monkeypatch, from_toggle=False)
    assert recording.toggle_pressed_while_recording(beeps_on=False) is False
    _speak_then_go_quiet()
    assert not rs.state.current.went_quiet
    rs.state.is_recording = False


def test_the_silent_tail_is_trimmed_and_speech_is_kept(monkeypatch):
    monkeypatch.setattr(recording, "stop_stream_safely", lambda rec: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *a: None)
    monkeypatch.setattr(recording, "indicator", None, raising=False)
    det = SilenceDetector(2.0, SR)
    talk, quiet = speech(1.0), noise(2.4)   # 2.4 s: blocks keep arriving after it fires
    speech_blocks = [talk[i:i + BLOCK].tobytes() for i in range(0, len(talk), BLOCK)]
    silent_blocks = [quiet[i:i + BLOCK].tobytes() for i in range(0, len(quiet), BLOCK)]
    for blk in speech_blocks + silent_blocks:
        det.feed(blk)
    assert det.fired
    rs.state.current.silence = det
    rs.state.current.frames = speech_blocks + silent_blocks
    rs.state.is_recording = True
    rs.state.current.message.cancelled = False
    rs.state.current.press_t0 = time.time() - 5   # held well past MIN_HOLD_MS
    frames, _ = recording.finish_capture(rs.state.current, False, False)
    kept_s = sum(len(f) // 2 for f in frames) / SR
    # 1 s of speech (its last syllable gap counts as silence) + 0.3 s kept.
    assert 1.1 <= kept_s <= 1.4
    assert frames[:50] == speech_blocks  # every speech block untouched
    assert rs.state.current.silence is None


def test_auto_stop_settings_apply_without_a_restart():
    from tuxtalk.config import LIVE_APPLIED_KEYS
    assert {"auto_stop_silence", "auto_stop_seconds"} <= LIVE_APPLIED_KEYS


def test_setting_follows_config(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(live, "ai_engine", None)
    live.apply(SimpleNamespace(auto_stop_silence=True, auto_stop_seconds=3.0))
    assert live.auto_stop_seconds == 3.0
    live.apply(SimpleNamespace(auto_stop_silence=False, auto_stop_seconds=3.0))
    assert live.auto_stop_seconds == 0.0


# --- an accidental tap of the hold key ------------------------------------------
# A tap just over the minimum hold time records a fraction of a second of room
# noise and the key's click, and Parakeet can "hear" a word in that, such as a
# stray "Thank you.".

from tuxtalk.silence import is_speechless_tap  # noqa: E402


def f32(int16_audio):
    return int16_audio.astype(np.float32) / 32768.0


def click(seconds=0.25, noise_level=60.0):
    """Room noise with a key click: 10 ms, loud."""
    audio = noise(seconds, noise_level).astype(np.float64)
    start = int(0.05 * SR)
    audio[start:start + 160] += rng.normal(0, 6000, 160)
    return np.clip(audio, -32768, 32767).astype(np.int16)


def test_a_tap_with_only_room_noise_has_no_speech():
    assert is_speechless_tap(f32(noise(0.25)), SR)


def test_a_tap_with_a_key_click_has_no_speech():
    assert is_speechless_tap(f32(click()), SR)


def test_a_tap_on_a_noisy_desk_mic_has_no_speech():
    """Shaped like real taps from a Sennheiser Profile: a noisy background
    and a key thump about 4x louder for 40 ms. A 1.5x threshold lets such taps
    through, and Parakeet types "Yeah." for them."""
    audio = noise(0.3, 100).astype(np.float64)
    start = int(0.02 * SR)
    audio[start:start + 640] += rng.normal(0, 380, 640)
    assert is_speechless_tap(f32(np.clip(audio, -32768, 32767).astype(np.int16)), SR)


def test_a_quick_word_is_kept():
    word = np.concatenate([noise(0.08), speech(0.3), noise(0.07)])
    assert not is_speechless_tap(f32(word), SR)


def test_a_quick_quiet_word_is_kept_too():
    """Judged against the clip's own background, not a fixed loudness, so a
    quiet microphone isn't mistaken for silence."""
    word = np.concatenate([noise(0.08, 15), speech(0.3, level=250, noise_level=15),
                           noise(0.07, 15)])
    assert not is_speechless_tap(f32(word), SR)


def test_a_word_that_fills_the_whole_tap_is_kept():
    """Real speech has no neat gaps; a plain 4x ratio drops 224 of 510 real
    half-second clips, because the clip's quietest blocks are speech."""
    t = np.arange(int(0.45 * SR)) / SR
    voice = np.sin(2 * np.pi * 180 * t) * (2000 + 800 * np.sin(2 * np.pi * 4 * t))
    assert not is_speechless_tap(f32((voice + rng.normal(0, 60, t.size)).astype(np.int16)), SR)


def test_anything_longer_than_a_tap_is_never_judged():
    """Long recordings go to the engine as always, silent or not."""
    assert not is_speechless_tap(f32(noise(1.0)), SR)


def test_the_service_skips_a_speechless_tap(monkeypatch):
    monkeypatch.setattr(engine, "transcribe",
                        lambda *a: pytest.fail("a speechless tap was transcribed"))
    pipeline.transcribe_and_inject([click().tobytes()], SR, False, False, False,
                                   message=rs.state.current.message)


# --- Nothing said at all -----------------------------------------------------
# Double-tap and say nothing: the recording must not stay open until a third
# press. Before the first word only, a fixed limit applies.

def test_nothing_said_for_the_limit_stops_and_says_so():
    at, det = run(noise(12.0), 1.5)
    assert at == pytest.approx(SilenceDetector.NOTHING_SAID_S, abs=0.05)
    assert det.nothing_said


def test_a_cough_in_the_quiet_still_counts_as_nothing_said():
    at, det = run(np.concatenate([noise(4.0), speech(0.1), noise(8.0)]), 1.5)
    assert at is not None and det.nothing_said


def test_once_talking_starts_the_limit_no_longer_applies():
    """Thinking for 8 s, then talking: the normal silence setting decides."""
    audio = np.concatenate([noise(8.0), speech(4.0), noise(3.0)])
    at, det = run(audio, 1.5)
    assert at == pytest.approx(8.0 + 4.0 + 1.5, abs=0.1)
    assert not det.nothing_said


def test_a_hands_free_recording_with_nothing_said_is_dropped(monkeypatch):
    _start(monkeypatch)
    assert recording.toggle_pressed_while_recording(beeps_on=False) is True
    audio = noise(SilenceDetector.NOTHING_SAID_S + 0.5)
    for i in range(0, len(audio), BLOCK):
        recording._sd_callback(rs.state.current, audio[i:i + BLOCK].reshape(-1, 1),
                                BLOCK, None, None)
    assert rs.state.current.went_quiet
    monkeypatch.setattr(recording, "stop_stream_safely", lambda rec: None)
    assert recording.finish_capture(rs.state.current, False,
                                    False) is None      # nothing is transcribed
    assert rs.state.current.message.cancelled
    rs.state.current.message.cancelled = False


# --- A recording with nobody talking -------------------------------------------
# Stands in for Whisper's no-speech score on the Vulkan engine, whose own score
# reads 0.00 on silence.

from tuxtalk.silence import has_no_speech  # noqa: E402


def test_a_long_quiet_hold_has_no_speech():
    assert has_no_speech(f32(noise(4.0)), SR)


def test_a_cough_or_a_click_in_a_long_hold_is_still_no_speech():
    audio = np.concatenate([noise(2.0), speech(0.1), noise(2.0), click()])
    assert has_no_speech(f32(audio), SR)


def test_a_short_sign_off_said_aloud_is_speech():
    audio = np.concatenate([noise(0.5), speech(1.0), noise(1.5)])
    assert not has_no_speech(f32(audio), SR)


def test_a_quiet_microphone_saying_a_sign_off_is_speech():
    audio = np.concatenate([noise(0.5, 15), speech(1.0, level=250, noise_level=15),
                            noise(1.5, 15)])
    assert not has_no_speech(f32(audio), SR)


# --- A microphone that sends nothing at all --------------------------------

def test_dead_mic_is_digital_silence_only():
    from tuxtalk.silence import is_dead_mic
    sr = 16000
    assert is_dead_mic(np.zeros(sr * 2, dtype=np.float32), sr)
    assert is_dead_mic(np.full(sr * 2, 1 / 32768, dtype=np.float32), sr)    # one bit of hum
    # A quiet room still has the mic's own hiss (a desk mic: RMS ~0.003).
    rng = np.random.default_rng(0)
    room = (rng.standard_normal(sr * 2) * 0.003).astype(np.float32)
    assert not is_dead_mic(room, sr)
    # Too short to judge: a quick tap is handled elsewhere.
    assert not is_dead_mic(np.zeros(sr // 2, dtype=np.float32), sr)


def test_a_dead_mic_is_reported_not_transcribed(monkeypatch):
    said, transcribed = [], []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, msg: said.append((title, msg)))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr(engine, "transcribe", lambda *a: transcribed.append(1) or "hi")
    frames = [np.zeros(engine.SAMPLE_RATE * 2, dtype=np.int16).tobytes()]
    pipeline.transcribe_and_inject(frames, engine.SAMPLE_RATE, beeps_on=False,
                               smart_quotes=False, notify_on=False,
                               message=rs.state.current.message)
    assert transcribed == []
    # The pop-up shows the title and a line or two: the problem itself must be
    # the title, not "TuxTalk", which the desktop already shows as the sender.
    assert said == [("No sound from the microphone", notices.SILENT_MIC_NOTICE)]


def _feed(det, audio):
    """Feed audio; the second it fired at (from this audio's start), or None."""
    for i in range(0, len(audio), BLOCK):
        if det.feed(audio[i:i + BLOCK].tobytes()):
            return (i + BLOCK) / SR
    return None


def test_the_quiet_waits_while_tuxtalk_works_on_a_phrase():
    """After "edit that, make it more formal", hands-free stopped 2 s after
    the rewrite appeared, before there was time to say "press enter". While
    a phrase is worked on the quiet doesn't count; once it's on screen, the
    full silence time starts."""
    det = SilenceDetector(1.5, SR)
    assert _feed(det, np.concatenate([noise(0.5), speech(2.0), noise(1.0)])) is None
    det.hold()                                   # the AI rewrite takes 4 s
    assert _feed(det, noise(4.0)) is None
    det.release()                                # the rewrite is typed
    assert _feed(det, noise(1.2)) is None        # still reading
    assert _feed(det, noise(0.5)) is not None    # 1.5 s after it appeared


def test_holds_nest_and_release_never_goes_below_zero():
    det = SilenceDetector(1.0, SR)
    _feed(det, speech(1.0))
    det.hold()
    det.hold()
    det.release()
    assert _feed(det, noise(2.0)) is None        # one job still running
    det.release()
    det.release()                                # an extra release is harmless
    assert _feed(det, noise(1.2)) is not None


# --- A noisy room -------------------------------------------------------------
# With a clothes dryer running nearby hands-free never stopped: its thumps
# counted as talking, and steady noise louder than speech's threshold could
# never be learned. Steady noise well under the voice is learned now, three
# seconds after the last word, and its bumps pause the wait. Noise close to
# the voice is left alone, as learning it cut people off during quiet words.
# How the rules were measured on recorded speech: NoiseFloor and
# SilenceDetector.

def rumble(seconds, level):
    """A dryer: a drum's rumble, mostly below 200 Hz, with a 100 ms thump,
    8 dB louder, every 0.8 s."""
    size = int(seconds * SR)
    pitches = np.fft.rfftfreq(size, 1 / SR)
    drum = np.fft.irfft(np.fft.rfft(rng.normal(0, 1, size)) / (1 + pitches / 200), size)
    drum *= level / np.sqrt(np.mean(drum ** 2))
    for start in np.arange(0.3, seconds - 0.1, 0.8):
        i = int(start * SR)
        drum[i:i + int(0.1 * SR)] *= 2.5
    return drum


def fan(seconds, level=300.0, gust=1200.0):
    """A fan: steady noise with a 60 ms gust, 12 dB louder, every 0.45 s."""
    audio = rng.normal(0, level, int(seconds * SR))
    for start in np.arange(0.2, seconds - 0.06, 0.45):
        i = int(start * SR)
        audio[i:i + int(0.06 * SR)] = rng.normal(0, gust, int(0.06 * SR))
    return audio


def hum(seconds, level):
    """Steady noise, as loud as *level* (int16 RMS)."""
    return rng.normal(0, level, int(seconds * SR))


ROOM_NOISES = {"a dryer": lambda s: rumble(s, 500.0), "a fan": fan}


def _in(room, audio):
    """*audio* with *room*'s noise over all of it, as int16."""
    return np.clip(audio + ROOM_NOISES[room](len(audio) / SR), -32768, 32767).astype(np.int16)


def _three_words(pause_s=1.2):
    """Three words said by a person, *pause_s* apart, after half a second."""
    pause = np.zeros(int(pause_s * SR))
    return np.concatenate([np.zeros(int(0.5 * SR)), _real_word("ask"), pause,
                           _real_word("not"), pause, _real_word("ask")])


@pytest.mark.parametrize("room", ROOM_NOISES)
def test_in_a_dryer_or_a_fan_it_stops_by_itself_after_the_last_word(room):
    """Each thump or gust used to start the wait over, for good. Now it stops
    once the noise is learned, never before the last word, at most the
    three seconds that takes late (plus a thump or two)."""
    said = _three_words()
    at, det = run(_in(room, np.concatenate([said, np.zeros(8 * SR)])), 1.5)
    assert at is not None, "it never stopped"
    assert len(said) / SR + 1.5 - 0.2 <= at <= len(said) / SR + 3.0 + 1.5
    assert not det.nothing_said


@pytest.mark.parametrize("when", ["running from the start", "switched on while talking"])
def test_steady_noise_louder_than_speech_s_threshold_becomes_the_room(when):
    """Every block of the hum counts as speech over the floor learned before,
    so only the steady rule can learn it: then the recording stops."""
    talk = speech(3.0, level=20000.0)
    before = np.zeros(int(1.5 * SR)) if when == "switched on while talking" else hum(1.5, 1000)
    audio = np.concatenate([np.zeros(int(0.5 * SR)), talk, np.zeros(8 * SR)])
    audio[int(0.5 * SR):] += np.concatenate([before, hum(len(audio) / SR, 1000)])[
        :len(audio) - int(0.5 * SR)]
    at, det = run(np.clip(audio, -32768, 32767).astype(np.int16), 1.5)
    assert at is not None, "it never stopped"
    assert 3.5 + 1.5 - 0.1 <= at <= 3.5 + 3.0 + 1.0     # learned 3 s after the last word
    assert det.tail_to_trim() <= int(1.5 * SR), "it trimmed what it took back as noise"


def test_steady_noise_close_to_the_voice_never_ends_it_during_quiet_words():
    """A hum only 16 dB under the loudest word isn't learned: its quiet
    words would drown in it. It then never stops by itself, as before, but
    it never cuts the speaker off either."""
    said = _three_words()
    audio = np.clip(np.concatenate([said, np.zeros(6 * SR)]) + hum(len(said) / SR + 6, 1500),
                    -32768, 32767).astype(np.int16)
    at, _ = run(audio, 1.0)
    assert at is None


def test_talking_evenly_is_not_taken_for_the_room():
    """A voice held loud, its syllables swinging by only 6 dB (2.5 then 1.5
    times its quiet level, every 50 ms) for three seconds, after a quiet
    start: the room stays quiet, so it neither stops while it talks nor
    trims any of it."""
    frames = [np.full(800, 100.0)] * 10 + [
        np.sin(2 * np.pi * 440 * np.arange(800) / SR) * np.sqrt(2) * (4000 if i % 2 else 1000)
        for i in range(20)] + [
        np.sin(2 * np.pi * 440 * np.arange(800) / SR) * np.sqrt(2) * (1500 if i % 2 else 1000)
        for i in range(120)]
    at, det = run(np.concatenate(frames).astype(np.int16), 1.0)
    assert at is None
    assert det.tail_to_trim() == 0


def test_in_a_quiet_room_a_short_first_syllable_starts_the_wait_over():
    """After a long pause, the next sentence can start with one short loud
    syllable and go on softly. In a quiet room that syllable is speech: the
    wait starts over, and the soft words after it don't end the recording."""
    det = SilenceDetector(2.0, SR)
    syllable = speech(0.1)
    soft = speech(1.2, level=250.0)
    audio = np.concatenate([speech(1.0), noise(1.6), syllable, soft])
    assert _feed(det, audio) is None


def test_in_a_fan_a_gust_pauses_the_wait_but_a_word_starts_it_over():
    """Once a gusty fan is learned, its 60 ms gusts (every 0.45 s) pause the
    wait instead of starting it over, so a 4 s wait after a word ends about
    the gusts' share of it late; had they started it over, it never would.
    What is trimmed is only the quiet after the last gust."""
    det = SilenceDetector(4.0, SR)
    fan_alone = np.clip(fan(3.8), -32768, 32767).astype(np.int16)
    assert _feed(det, np.concatenate([speech(0.5, level=20000.0), fan_alone])) is None
    word = speech(0.25, level=20000.0, noise_level=300.0)
    more = np.clip(fan(8.0), -32768, 32767).astype(np.int16)
    at = _feed(det, np.concatenate([word, more]))
    assert at is not None, "the gusts started the wait over"
    assert 0.25 + 4.0 <= at <= 0.25 + 4.0 + 1.0
    assert det.tail_to_trim() <= int(0.45 * SR)


def test_with_a_short_wait_a_short_sound_still_starts_it_over():
    """A bump is at most a tenth of the wait: with 0.5 s, the start of a quiet
    word (100 ms) is speech, and a 0.3 s pause each side of it is no stop."""
    det = SilenceDetector(0.5, SR)
    sound = hum(0.1, 1500)
    audio = np.concatenate([speech(1.0), hum(0.3, 60), sound, hum(0.3, 60)])
    assert _feed(det, audio.astype(np.int16)) is None


def test_noise_learned_right_after_a_phrase_is_typed_still_gives_the_full_wait():
    """Type at pause: the quiet while a phrase was typing (hold) is not the
    user finished. A hum learned a second after release() mustn't count it,
    so there is still the whole wait to read the phrase and say "press
    enter"."""
    det = SilenceDetector(1.5, SR)
    assert _feed(det, speech(0.5, level=20000.0)) is None
    det.hold()
    assert _feed(det, hum(2.0, 1000).astype(np.int16)) is None
    det.release()
    at = _feed(det, hum(4.0, 1000).astype(np.int16))
    assert at is not None and at >= 1.5


def test_a_phrase_finished_while_the_audio_thread_judges_again_stops_nothing():
    """Type at pause's worker calls release() while the audio thread is
    judging the last seconds again: the history was cleared under it, the
    audio callback raised, and sounddevice calls a callback that raised
    never again, so the recording went on hearing nothing."""
    import threading
    det = SilenceDetector(1.5, SR)
    assert _feed(det, speech(0.5)) is None
    inside, released, errors = threading.Event(), threading.Event(), []

    def judge_again_slowly():
        heard = iter(det._heard)
        next(heard)
        inside.set()
        released.wait(0.5)              # the worker's release() comes now
        list(heard)
    det._judge_again = judge_again_slowly
    det._floor.update = lambda rms, duration: setattr(det._floor, "changed", True) or False

    def audio_thread():
        try:
            det.feed(noise(0.02).tobytes())
        except RuntimeError as e:
            errors.append(e)
    feeding = threading.Thread(target=audio_thread)
    feeding.start()
    inside.wait(5)
    worker = threading.Thread(target=lambda: (det.release(), released.set()))
    worker.start()
    feeding.join(5)
    worker.join(5)
    assert errors == [] and released.is_set()


def test_a_pause_nearly_the_wait_then_a_short_first_syllable_in_a_quiet_room():
    """A 3 s wait, a 2.7 s pause, then a sentence starting with one short
    syllable and soft words. The learned room took the end of the last
    sentence for a bump of the room, then that syllable too, and the
    recording ended 0.3 s into the sentence."""
    said = np.concatenate([speech(1.0), noise(2.7), speech(0.04), speech(0.5, level=250),
                           speech(2.0)])
    at, _ = run(np.concatenate([said, noise(4.0)]), 3.0)
    assert at is not None and at >= len(said) / SR


def _stepping_hum(seconds, low=500.0, high=725.0):
    """A hum whose level steps between *low* and *high* (3.2 dB) every half second."""
    t = np.arange(int(0.5 * SR)) / SR
    return np.concatenate([np.sin(2 * np.pi * 120 * t) * np.sqrt(2) * (high if i % 2 else low)
                           for i in range(int(seconds * 2))])


def test_a_sound_that_steps_up_and_down_by_3_db_isnt_learned_as_the_room():
    """Measured by blocks alone, 3 s of it is as steady as a fan; by 50 ms
    frames it isn't, and frames are what keep a gusty fan from being
    learned so often that the next sentence's soft start is cut off."""
    audio = np.concatenate([speech(0.5, level=20000.0), _stepping_hum(8.0)])
    at, _ = run(np.clip(audio, -32768, 32767).astype(np.int16), 1.5)
    assert at is None


def test_when_the_dryer_stops_a_soft_voice_counts_again():
    """A dryer learned as the room, then switched off: a voice softer than
    the dryer was is still talking, not the quiet after it."""
    det = SilenceDetector(5.0, SR)
    dryer = np.clip(np.concatenate([speech(0.5, level=20000.0), hum(3.5, 1000)]),
                    -32768, 32767).astype(np.int16)
    assert _feed(det, dryer) is None
    soft = speech(1.5, level=900.0)
    at = _feed(det, np.concatenate([noise(0.5), soft, noise(6.0)]))
    assert at is not None and at >= 0.5 + 1.5 + 5.0 - 0.1


@pytest.mark.parametrize("talk_s", [0.1, 0.6], ids=["a click", "a quick word"])
def test_too_little_talking_in_a_mic_test_gives_no_verdict_on_the_room(talk_s):
    """Under a second of talking is too little to say how far a voice stands
    over the room: a take's first fraction of a second is often a word's
    soft start, and quiet rooms were called noisy."""
    take = np.concatenate([noise(3.0), speech(talk_s), noise(3.0)]).astype(np.float32) / 32768
    assert silence.speech_over_room_db(take, SR) is None


def test_talking_from_start_to_stop_in_a_quiet_room_gives_no_verdict():
    """With hardly any of the take outside the talking, there is no room to
    judge against: measured on the pauses between words instead, quiet
    words made a quiet room look noisy."""
    t = np.arange(SR // 2) / SR
    quiet_words = np.sin(2 * np.pi * 220 * t) * 300.0 + rng.normal(0, 20.0, t.size)
    talking = np.concatenate([speech(0.5, noise_level=20.0), quiet_words] * 3
                             + [speech(0.5, noise_level=20.0)])
    take = np.concatenate([noise(0.1, 20.0), talking, noise(0.1, 20.0)])
    assert silence.speech_over_room_db(take.astype(np.float32) / 32768, SR) is None


def test_soft_words_at_the_start_and_end_of_a_quiet_take_are_not_its_room():
    """A second of soft words (RMS 200) each side of two seconds at 2000, in
    a room at 6: taken for the room, the voice stood 20 dB over it and the
    test said the room was noisy. The pauses between the loud words are far
    quieter than that "room", so it gives no verdict."""
    t = np.arange(SR) / SR
    soft = np.sin(2 * np.pi * 220 * t) * 200.0 * np.sqrt(2) + rng.normal(0, 6.0, t.size)
    take = np.concatenate([soft, speech(2.0, level=2000.0 * np.sqrt(2), noise_level=6.0), soft])
    assert silence.speech_over_room_db(take.astype(np.float32) / 32768, SR) is None


def _blocks(*runs):
    """Audio made of 20 ms blocks, each as loud as given (int16 RMS):
    *runs* of (level, how many blocks)."""
    return np.concatenate([np.full(320 * count, float(level)) for level, count in runs])


@pytest.mark.parametrize("room_over_pauses_db, verdict", [(14.99, True), (15.01, False)])
def test_the_room_may_stand_up_to_15_db_over_the_pauses_between_words(room_over_pauses_db,
                                                                      verdict):
    """Rooms never stood more than 14 dB over the pauses; past 15 dB what is
    outside the talking is taken for soft words and given no verdict."""
    room = 20.0 * 10 ** (room_over_pauses_db / 20)
    talking = [(3000.0, 9), (20.0, 4)] * 7 + [(3000.0, 9)]
    take = _blocks((room, 60), *talking, (room, 60)) / 32768
    assert (silence.speech_over_room_db(take.astype(np.float32), SR) is not None) is verdict


def test_soft_words_around_talking_with_no_pauses_still_look_like_steady_noise():
    """A limit kept on purpose: soft words each side of talking that never
    pauses can't be told from steady noise 20 dB under the voice by level
    alone, so that warns (a voice detector could tell them apart)."""
    take = _blocks((200.0, 50), (2000.0, 100), (200.0, 50)) / 32768
    assert silence.speech_over_room_db(take.astype(np.float32), SR) < silence.NOISY_ROOM_DB


def _mic_test_take(room):
    """A microphone test: a second of *room* (a function of seconds giving
    int16-scale audio), 2.5 s of talking over it, then 1.5 s more; the
    voiced syllables' level is 2121 (int16 RMS)."""
    voice = np.concatenate([np.zeros(SR), speech(2.5, noise_level=0.0), np.zeros(int(1.5 * SR))])
    return ((voice + room(len(voice) / SR)) / 32768).astype(np.float32)


def _clattering(seconds, quiet=40.0, loud=250.0):
    """A kitchen or a cafe: dishes and voices 0.4 s of every 0.6 s, the room
    quiet in between."""
    t = np.arange(int(seconds * SR)) / SR
    return rng.normal(0, 1, t.size) * np.where((t % 0.6) < 0.4, loud, quiet)


def test_a_room_that_clatters_most_of_the_time_is_noisy_despite_its_quiet_moments():
    """Measured over the room's quietest moments, the voice stood 35 dB over
    this room and the test said nothing; in recorded kitchens and offices
    that loud, hands-free hardly ever stops by itself. Over the room's
    median it is 15 dB."""
    over_room = silence.speech_over_room_db(_mic_test_take(_clattering), SR)
    assert over_room < silence.NOISY_ROOM_DB


@pytest.mark.parametrize("under_db, noisy", [(40, False), (30, False), (20, True)])
def test_steady_noise_20_db_under_the_voice_is_noisy_and_30_db_under_is_not(under_db, noisy):
    """In steady noise 20 dB under the voice hands-free still stops by
    itself, but cuts the speaker off mid-sentence in about half the runs,
    against about one in seven at 30 dB; the test warns at 20 dB only."""
    take = _mic_test_take(lambda seconds: noise(seconds, 2121 / 10 ** (under_db / 20)))
    assert (silence.speech_over_room_db(take, SR) < silence.NOISY_ROOM_DB) is noisy


def test_a_voice_swinging_every_50_ms_isnt_learned_as_the_room():
    """A loud opening, then a voice alternating between two
    levels 3.5 dB apart every 50 ms. Its 50 ms frames smooth that out and
    look as steady as a fan; its blocks don't. Learned, it ended the
    recording at 3.8 s while the voice went on to 7.5 s."""
    t = np.arange(800) / SR

    def frame(level):
        return np.sin(2 * np.pi * 440 * t) * level * np.sqrt(2)
    audio = np.concatenate([frame(100)] * 10 + [frame(1000 if i % 2 else 20000) for i in range(20)]
                           + [frame(1000 if i % 2 else 1500) for i in range(120)])
    at, _ = run(audio.astype(np.int16), 1.0)
    assert at is None or at >= 7.5


def test_a_word_in_the_window_keeps_a_quiet_room_from_turning_bumpy():
    """A pause nearly the wait long, with a click in the middle of it: the
    last sentence's tail, the click and the next sentence's first short
    syllable put short sounds in every third of the window, and without
    the word-length check the room was taken for bumpy. The next soft
    words then ended the recording."""
    said = np.concatenate([speech(1.0), noise(1.3), speech(0.06), noise(1.3), speech(0.04),
                           speech(0.5, level=250), speech(2.0)])
    at, _ = run(np.concatenate([said, noise(4.0)]), 3.0)
    assert at is not None and at >= len(said) / SR


def test_judging_the_same_seconds_again_takes_nothing_back_twice():
    """Two learnings close together (the threshold jumps, then the bumps
    show a frame later) judge the same history twice: taking its speech
    back twice can leave too little to count as talking, and a real
    dictation ends as "Nothing heard"."""
    det = SilenceDetector(1.5, SR)
    _feed(det, np.concatenate([speech(0.6), hum(1.0, 400)]).astype(np.int16))
    det._floor.top, det._floor.steady_level = 600.0, 450.0    # a loud room learned
    det._judge_again()
    once = det.speech_time
    det._judge_again()
    assert det.speech_time == pytest.approx(once)


# --- Blocks of any size ---------------------------------------------------------
# Some microphones send blocks of 6 ms (96 samples at 16 kHz). Judged block by
# block, a dryer's rumble never looked steady over blocks that short, so
# hands-free never stopped in it. The audio is judged in 20 ms windows now,
# whatever size of block arrives.

@pytest.mark.parametrize("block", [96, 256, 512, 1024])
@pytest.mark.parametrize("room", ROOM_NOISES)
def test_the_same_recording_stops_at_the_same_moment_whatever_the_block_size(room, block):
    """In a dryer or a fan, fed in 20 ms blocks or in *block*-sample ones:
    it stops in the same window, so at most one block later, and trims
    the same quiet within a block."""
    audio = _in(room, np.concatenate([_three_words(), np.zeros(8 * SR)]))
    at_20ms, in_20ms = run(audio, 1.5)
    at, det = run(audio, 1.5, block)
    assert at is not None, "it never stopped"
    assert at_20ms <= at < at_20ms + block / SR
    assert abs(det.tail_to_trim() - in_20ms.tail_to_trim()) < block


def test_in_a_dryer_6_ms_blocks_stop_by_itself_after_the_last_word():
    """The microphone that sends 96-sample blocks: it stops after the last
    word, never during the words."""
    said = _three_words()
    at, det = run(_in("a dryer", np.concatenate([said, np.zeros(8 * SR)])), 1.5, 96)
    assert at is not None, "it never stopped"
    assert len(said) / SR + 1.5 - 0.2 <= at <= len(said) / SR + 3.0 + 1.5
    assert not det.nothing_said


def test_a_sound_in_the_last_few_samples_after_the_stop_is_never_trimmed():
    """Samples too few for a window count as quiet only when the last 20 ms
    is: a voice starting in the last 100 samples keeps the whole tail."""
    at, det = run(np.concatenate([speech(2.0), noise(3.0)]), 2.0)
    assert at is not None
    det.feed(noise(0.1).tobytes())
    det.feed(speech(0.3)[:100].tobytes())
    assert det.tail_to_trim() == 0


RATES = [16000, 44100, 48000]


def _room_at(rate, seconds):
    """Quiet room noise (int16 RMS about 60) at *rate*, as float."""
    return rng.normal(0, 60.0, int(round(seconds * rate)))


def _voice_at(rate, samples):
    """A held voice, *samples* long at *rate*, as float: every window loud."""
    return np.sin(2 * np.pi * 220 * np.arange(samples) / rate) * 3000.0


@pytest.mark.parametrize("ending", ["room noise", "a crest of room noise", "a voice",
                                    "a word's first millisecond"])
@pytest.mark.parametrize("rate", RATES)
def test_the_trim_keeps_exactly_0_3_s_after_the_last_word(rate, ending):
    """Half a second of room, a second of voice ending on a window's edge,
    then quiet, fed in blocks of one and a half windows plus one sample, so
    a few samples are left over at the end. 0.3 s after the voice is kept
    and the rest of the quiet trimmed, to the sample, unless the last few
    samples are a sound: then nothing is. A few samples of room noise that
    peak (a crest of 400 against a threshold of 300) are still the room; a
    word's first millisecond at 1000 is a sound, though averaged into the
    last 20 ms it would be quieter than the threshold."""
    window = int(0.02 * rate)
    last_word_ends = 75 * window
    audio = np.concatenate([_room_at(rate, 0.5)[:25 * window], _voice_at(rate, 50 * window),
                            _room_at(rate, 2.0)]).astype(np.int16)
    det = SilenceDetector(1.0, rate)
    block = window * 3 // 2 + 1
    fed = 0
    while not det.feed(audio[fed:fed + block].tobytes()):
        fed += block
    fed += block
    leftover = window // 20 if ending == "a word's first millisecond" else window // 10
    final = audio[fed:fed + 3 * window + leftover - (fed + 3 * window) % window].astype(float)
    if ending == "a crest of room noise":
        final[-leftover:] = 400.0 * (-1.0) ** np.arange(leftover)
    elif ending == "a voice":
        final[-leftover:] = _voice_at(rate, leftover)
    elif ending == "a word's first millisecond":
        final[-leftover:] = 1000.0
    det.feed(final.astype(np.int16).tobytes())
    end = fed + final.size
    assert det._pending.size == leftover
    kept = end - det.tail_to_trim()
    if ending in ("a voice", "a word's first millisecond"):
        assert kept == end
    else:
        assert abs(kept - (last_word_ends + 0.3 * rate)) <= 1


@pytest.mark.parametrize("where", ["at its end", "in its middle"])
@pytest.mark.parametrize("rate", RATES)
def test_a_voice_in_the_block_that_reaches_the_nothing_said_limit_is_kept(rate, where):
    """Nothing said for 9.9 s, then one block that crosses 10 s in silence
    with a voice starting in it, at its end or with quiet after: the
    recording goes on and the voice counts. Judged window by window, a
    quiet window of that block dropped it as nothing said."""
    window = int(0.02 * rate)
    det = SilenceDetector(1.5, rate)
    for _ in range(495):
        assert not det.feed(np.zeros(window, np.int16).tobytes())
    after = np.zeros(window if where == "in its middle" else 0)
    block = np.concatenate([np.zeros(10 * window), _voice_at(rate, window), after])
    assert det.feed(block.astype(np.int16).tobytes()) is False
    for _ in range(25):
        assert not det.feed(_voice_at(rate, window).astype(np.int16).tobytes())
    assert det.armed and not det.nothing_said


@pytest.mark.parametrize("block", [1024, 4096])
def test_clicks_after_the_nothing_said_limit_put_the_drop_off_one_block_at_most(block):
    """A quiet room, and from 10 s on a 10 ms click at the end of every
    block: the block that reaches 10 s is kept for its click, the next one
    is dropped as nothing said. Kept for each click, 15 of them added up to
    talking and the room went to the engine."""
    audio = rng.normal(0, 60, 20 * SR)
    crossing = int(10 * SR) // block * block
    for end in range(crossing + block, len(audio), block):
        window_end = end // BLOCK * BLOCK
        audio[window_end - 160:window_end] += rng.normal(0, 3000, 160)
    at, det = run(audio.astype(np.int16), 1.5, block)
    assert det.nothing_said
    assert at <= (crossing + 2 * block) / SR


def test_the_trim_waits_for_a_block_being_judged():
    """The audio thread can still be judging a block when the recording
    stops (a stream that wouldn't close in time): a voice in that block
    must not be trimmed away, so the trim waits until the block is judged."""
    import threading
    at, det = run(np.concatenate([speech(1.0), noise(3.0)]), 2.0)
    assert at is not None and det.tail_to_trim() > 0
    judging, go_on = threading.Event(), threading.Event()
    threshold = det._threshold

    def judge_slowly():
        judging.set()
        go_on.wait(5)
        return threshold()
    det._threshold = judge_slowly
    feeding = threading.Thread(target=det.feed, args=(speech(0.02).tobytes(),))
    feeding.start()
    assert judging.wait(5)
    trimmed = []
    trim = threading.Thread(target=lambda: trimmed.append(det.tail_to_trim()))
    trim.start()
    trim.join(0.3)
    go_on.set()
    feeding.join(5)
    trim.join(5)
    assert trimmed == [0]


def test_the_audio_system_reusing_its_buffers_changes_nothing_heard():
    """sounddevice hands each block in an (n, 1) array it reuses once the
    callback returns. The leftover samples are kept as a copy, so the stop
    and the trim are the same as with fresh blocks, even when each buffer
    is scribbled over while the next one is heard."""
    audio = np.concatenate([speech(2.0), noise(3.0)])
    audio = audio[:len(audio) - len(audio) % 96]
    fresh_at, fresh = run(audio, 2.0, 96)
    det, at = SilenceDetector(2.0, SR), None
    buffers = [np.zeros((96, 1), np.int16), np.zeros((96, 1), np.int16)]
    for n, i in enumerate(range(0, len(audio), 96)):
        buffer = buffers[n % 2]
        buffer[:, 0] = audio[i:i + 96]
        fired = det.feed(buffer)
        buffer[:] = 32000
        if fired:
            at = (i + 96) / SR
            break
    assert at == fresh_at and det.tail_to_trim() == fresh.tail_to_trim()
