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


def run(audio, silence_seconds=1.5):
    """Feed audio in blocks; return (seconds at which it fired or None, detector)."""
    det = SilenceDetector(silence_seconds, SR)
    for i in range(0, len(audio), BLOCK):
        if det.feed(audio[i:i + BLOCK].tobytes()):
            return (i + BLOCK) / SR, det
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
    """Even speech whose gaps never get quiet is recognised when it starts at once."""
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
    from tuxtalk import app
    monkeypatch.setattr(app, "_auto_stop_seconds", seconds)

    def open_microphone(idx):
        time.sleep(mic_delay)
        app.state.recording_samplerate = SR
    monkeypatch.setattr(app, "_open_input_stream", open_microphone)
    if not beeps_on:
        monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_notify_tray_recording_state", lambda *a: None)
    monkeypatch.setattr(app, "recording_indicator", None, raising=False)
    assert app.start_recording(beeps_on, False, 0, from_toggle=from_toggle)
    app._cmd_stop_recording.clear()
    return app


def _speak_then_go_quiet(app, quiet_seconds=3.0):
    audio = np.concatenate([speech(1.0), noise(quiet_seconds)])
    for i in range(0, len(audio), BLOCK):
        app._sd_callback(audio[i:i + BLOCK].reshape(-1, 1), BLOCK, None, None)


def test_a_single_tap_is_a_plain_toggle_however_long_the_pause(monkeypatch):
    app = _start(monkeypatch)
    assert app.state.silence is None
    _speak_then_go_quiet(app, quiet_seconds=8.0)
    assert not app._cmd_stop_recording.is_set()
    app.state.is_recording = False


def test_a_double_tap_switches_to_hands_free_and_it_stops_itself(monkeypatch):
    app = _start(monkeypatch)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True   # 2nd tap: keep going
    assert app.state.silence is not None
    _speak_then_go_quiet(app)
    assert app._cmd_stop_recording.is_set()
    app._cmd_stop_recording.clear()
    app.state.is_recording = False


def test_a_double_tap_at_a_relaxed_pace_still_switches_to_hands_free(monkeypatch):
    """Issue #21: taps about half a second apart, a normal pace for someone
    whose hands are slow, stopped the recording instead."""
    app = _start(monkeypatch)
    app.state.toggle_started_at -= 0.55
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    assert app.state.silence is not None
    app.state.is_recording = False


def _goes_quiet(app):
    """After the second tap the room goes quiet: (seconds of quiet until the
    recording stopped by itself, or None if it didn't; whether it stopped as
    one in which nothing was said, which drops it instead of typing it)."""
    quiet = noise(12.0)
    for i in range(0, len(quiet), BLOCK):
        app._sd_callback(quiet[i:i + BLOCK].reshape(-1, 1), BLOCK, None, None)
        if app._cmd_stop_recording.is_set():
            return (i + BLOCK) / SR, app.state.silence.nothing_said
    return None, False


def _stopped_to_type_it(app):
    """Going quiet stops the recording the usual way, after the auto-stop
    setting's second of quiet, to type what was said."""
    stopped_after, nothing_said = _goes_quiet(app)
    app._cmd_stop_recording.clear()
    app.state.is_recording = False
    return stopped_after == pytest.approx(1.0, abs=0.05) and not nothing_said


def _say(app, audio):
    for i in range(0, len(audio), BLOCK):
        app._sd_callback(audio[i:i + BLOCK].reshape(-1, 1), BLOCK, None, None)


def test_a_word_said_before_the_second_tap_is_kept(monkeypatch):
    """Tap, say a quick word, tap again, then nothing more: the word is
    dictation, so going quiet stops the recording to type it, not to drop
    it as "nothing said"."""
    app = _start(monkeypatch)
    _say(app, speech(0.4))
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    assert _stopped_to_type_it(app)


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
# full scale, so 5.5 times is clipped at 1.1 times full scale and 8 at 1.6.
BEEP_HEARD = {"faint": (0.1, "clean"), "as played": (1.0, "clean"),
              "near full scale": (4.0, "clean"), "distorted": (1.0, "distorted"),
              "distorted near full scale": (4.0, "distorted"),
              "clipped at 1.1x full scale": (5.5, "clean"),
              "clipped at 1.6x full scale": (8.0, "clean"),
              "saturated at 1.6x full scale": (8.0, "saturated")}


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
    app = _start(monkeypatch, beeps_on=beeps_on)
    _say(app, heard)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    stopped_after, nothing_said = _goes_quiet(app)
    app._cmd_stop_recording.clear()
    app.state.is_recording = False
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


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
@pytest.mark.parametrize("room", [6.0, 300.0], ids=["silent room", "noisy room"])
@pytest.mark.parametrize("name", BEEP_HEARD)
def test_of_a_start_beep_one_block_at_most_counts(name, room, rate):
    """Wherever it falls in a 20 ms block, and at the rate a microphone that
    only takes its own records at (often 48 kHz, where the beep has more
    harmonics): a quarter of the shortest word, so a key click with it is
    still not one."""
    for ms in range(20):
        heard = _recording([(_picked_up_beep(*BEEP_HEARD[name], rate=rate), 0.4 + ms / 1000)],
                           room, rate=rate) / 32768.0
        assert silence._loud_seconds(heard, rate, beeps.START_BEEP[0]) <= 0.02, f"{ms} ms"


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
    app = _start(monkeypatch, beeps_on=True)
    _say(app, heard)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    assert _stopped_to_type_it(app)


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


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
@pytest.mark.parametrize("gain", [1.0, 0.1, 0.01])
@pytest.mark.parametrize("word", ["ask", "not"])
def test_the_filter_for_the_start_beep_takes_nothing_from_a_word(word, gain, rate):
    """Every block of a quick word that is loud enough to count still counts
    with the start beep's pitch filtered out, softly said or not."""
    heard = _recording([(_real_word(word, gain, rate), 0.3)], room=6.0, rate=rate) / 32768.0
    loud = silence._loud_seconds(heard, rate)
    assert loud >= 0.08
    assert silence._loud_seconds(heard, rate, beeps.START_BEEP[0]) == loud


def test_with_beeps_off_nothing_is_taken_for_the_start_beep(monkeypatch):
    """No start beep played, so a sound at its pitch is a sound like any
    other: someone whistling, or another app's alert."""
    heard = _recording([(_picked_up_beep(1.0, "clean"), 0.4)], room=6.0)
    assert not _nothing_said(monkeypatch, heard, beeps_on=False)


def test_a_word_kept_with_the_start_beep_is_transcribed(monkeypatch):
    """Kept, the word goes on to be transcribed, all of it."""
    word = _recording([(_real_word("ask"), 0.3), (_picked_up_beep(1.0, "distorted"), 0.0)],
                      room=60.0)
    app = _start(monkeypatch, beeps_on=True)
    _say(app, word)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    stopped_after, nothing_said = _goes_quiet(app)
    assert stopped_after is not None and not nothing_said
    monkeypatch.setattr(app, "_stop_stream_safely", lambda: None)
    app.state.press_t0 = time.time() - 5
    frames, rate = app._finish_capture(False, False)
    assert rate == SR
    assert b"".join(frames).startswith(word.tobytes())
    app._cmd_stop_recording.clear()


def test_a_quick_second_tap_counts_when_the_recording_was_slow_to_start(monkeypatch):
    """Issue #21: the keys are read on the thread that starts the recording,
    so a second tap made while the microphone and the start beep took most
    of a second is only read afterwards. It is still a double-tap."""
    app = _start(monkeypatch, mic_delay=0.9)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    app.state.is_recording = False


def _slow_start_beep(monkeypatch):
    """Every sound takes half a second to be heard, as on a slow output.
    Returns each wait for one: what it waited for, and when it ended."""
    from tuxtalk import beeps
    waits = []

    def wait_until_played(limit=beeps.WAIT_LIMIT_S, sound=None):
        time.sleep(0.5)
        waits.append((sound, time.time()))
        return True
    monkeypatch.setattr(beeps, "wait_until_played", wait_until_played)
    return waits


def test_the_double_tap_is_timed_from_the_start_beep_heard(monkeypatch):
    """Issue #21: the user waits for the start beep before the second tap,
    and on a slow output it comes most of a second after the first."""
    waits = _slow_start_beep(monkeypatch)
    app = _start(monkeypatch, beeps_on=True)
    (waited_for, ended), = waits
    assert waited_for is app.state.start_beep is not None
    assert app.state.toggle_started_at >= ended
    app.state.is_recording = False


@pytest.mark.parametrize("from_toggle,beeps_on", [(False, True), (True, False)],
                         ids=["hold start", "beeps off"])
def test_no_start_waits_for_a_start_beep_it_doesnt_have_to(monkeypatch, from_toggle, beeps_on):
    waits = _slow_start_beep(monkeypatch)
    app = _start(monkeypatch, from_toggle=from_toggle, beeps_on=beeps_on)
    assert waits == []
    app.state.is_recording = False


def test_a_slow_second_tap_is_a_normal_stop(monkeypatch):
    app = _start(monkeypatch)
    app.state.toggle_started_at -= app.DOUBLE_TAP_S + 0.1
    assert app._toggle_pressed_while_recording(beeps_on=False) is False
    assert app.state.silence is None
    app.state.is_recording = False


def test_a_third_tap_after_a_double_tap_stops(monkeypatch):
    app = _start(monkeypatch)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    assert app._toggle_pressed_while_recording(beeps_on=False) is False
    app.state.is_recording = False


def test_double_tap_does_nothing_special_with_the_option_off(monkeypatch):
    app = _start(monkeypatch, seconds=0.0)
    assert app._toggle_pressed_while_recording(beeps_on=False) is False
    app.state.is_recording = False


def test_a_hold_recording_cannot_become_hands_free(monkeypatch):
    app = _start(monkeypatch, from_toggle=False)
    assert app._toggle_pressed_while_recording(beeps_on=False) is False
    _speak_then_go_quiet(app)
    assert not app._cmd_stop_recording.is_set()
    app.state.is_recording = False


def test_the_silent_tail_is_trimmed_and_speech_is_kept(monkeypatch):
    from tuxtalk import app
    monkeypatch.setattr(app, "_stop_stream_safely", lambda: None)
    monkeypatch.setattr(app, "_notify_tray_recording_state", lambda *a: None)
    monkeypatch.setattr(app, "recording_indicator", None, raising=False)
    det = SilenceDetector(2.0, SR)
    talk, quiet = speech(1.0), noise(2.4)   # 2.4 s: blocks keep arriving after it fires
    speech_blocks = [talk[i:i + BLOCK].tobytes() for i in range(0, len(talk), BLOCK)]
    silent_blocks = [quiet[i:i + BLOCK].tobytes() for i in range(0, len(quiet), BLOCK)]
    for blk in speech_blocks + silent_blocks:
        det.feed(blk)
    assert det.fired
    app.state.silence = det
    app.state.frames = speech_blocks + silent_blocks
    app.state.is_recording = True
    app.state.was_cancelled = False
    app.state.press_t0 = time.time() - 5   # held well past MIN_HOLD_MS
    frames, _ = app._finish_capture(False, False)
    kept_s = sum(len(f) // 2 for f in frames) / SR
    # 1 s of speech (its last syllable gap counts as silence) + 0.3 s kept.
    assert 1.1 <= kept_s <= 1.4
    assert frames[:50] == speech_blocks  # every speech block untouched
    assert app.state.silence is None


def test_auto_stop_settings_apply_without_a_restart():
    from tuxtalk.config import LIVE_APPLIED_KEYS
    assert {"auto_stop_silence", "auto_stop_seconds"} <= LIVE_APPLIED_KEYS


def test_setting_follows_config(monkeypatch):
    from types import SimpleNamespace
    from tuxtalk import app
    monkeypatch.setattr(app, "_ai_engine", None)
    app._apply_cleanup_settings(SimpleNamespace(auto_stop_silence=True, auto_stop_seconds=3.0))
    assert app._auto_stop_seconds == 3.0
    app._apply_cleanup_settings(SimpleNamespace(auto_stop_silence=False, auto_stop_seconds=3.0))
    assert app._auto_stop_seconds == 0.0


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
    from tuxtalk import app
    monkeypatch.setattr(app, "_transcribe_audio",
                        lambda *a: pytest.fail("a speechless tap was transcribed"))
    app._transcribe_and_inject([click().tobytes()], SR, False, False, False)


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
    app = _start(monkeypatch)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    audio = noise(SilenceDetector.NOTHING_SAID_S + 0.5)
    for i in range(0, len(audio), BLOCK):
        app._sd_callback(audio[i:i + BLOCK].reshape(-1, 1), BLOCK, None, None)
    assert app._cmd_stop_recording.is_set()
    monkeypatch.setattr(app, "_stop_stream_safely", lambda: None)
    assert app._finish_capture(False, False) is None      # nothing is transcribed
    assert app.state.was_cancelled
    app._cmd_stop_recording.clear()
    app.state.was_cancelled = False


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


# --- A microphone that sends nothing at all (2026-10-17) ---------------------

def test_dead_mic_is_digital_silence_only():
    from tuxtalk.silence import is_dead_mic
    sr = 16000
    assert is_dead_mic(np.zeros(sr * 2, dtype=np.float32), sr)
    assert is_dead_mic(np.full(sr * 2, 1 / 32768, dtype=np.float32), sr)    # one bit of hum
    # A quiet room still has the mic's own hiss (Ron's: RMS ~0.003).
    rng = np.random.default_rng(0)
    room = (rng.standard_normal(sr * 2) * 0.003).astype(np.float32)
    assert not is_dead_mic(room, sr)
    # Too short to judge: a quick tap is handled elsewhere.
    assert not is_dead_mic(np.zeros(sr // 2, dtype=np.float32), sr)


def test_a_dead_mic_is_reported_not_transcribed(monkeypatch):
    from tuxtalk import app
    said, transcribed = [], []
    monkeypatch.setattr(app, "_notify", lambda title, msg: said.append((title, msg)))
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_transcribe_audio", lambda *a: transcribed.append(1) or "hi")
    frames = [np.zeros(app.SAMPLE_RATE * 2, dtype=np.int16).tobytes()]
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, beeps_on=False,
                               smart_quotes=False, notify_on=False)
    assert transcribed == []
    # The pop-up shows the title and a line or two: the problem itself must be
    # the title, not "TuxTalk", which the desktop already shows as the sender.
    assert said == [("No sound from the microphone", app.SILENT_MIC_NOTICE)]


def _feed(det, audio):
    """Feed audio; the second it fired at (from this audio's start), or None."""
    for i in range(0, len(audio), BLOCK):
        if det.feed(audio[i:i + BLOCK].tobytes()):
            return (i + BLOCK) / SR
    return None


def test_the_quiet_waits_while_tuxtalk_works_on_a_phrase():
    """Ron, 2026-10-17: "edit that, make it more formal", and hands-free
    stopped 2 s after the rewrite appeared, before he could say "press
    enter". While a phrase is worked on the quiet doesn't count; once it's on
    screen, the full silence time starts."""
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
