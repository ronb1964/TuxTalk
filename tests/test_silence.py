"""Hands-free auto-stop: the silence detector, on synthetic audio.

Audio is built from quiet room noise and louder "speech" (noise plus a tone),
fed in 20 ms blocks the way the microphone callback delivers it.
"""
import time

import numpy as np
import pytest

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

def _start(monkeypatch, from_toggle=True, seconds=1.0, mic_delay=0.0):
    from tuxtalk import app
    monkeypatch.setattr(app, "_auto_stop_seconds", seconds)

    def open_microphone(idx):
        time.sleep(mic_delay)
        app.state.recording_samplerate = SR
    monkeypatch.setattr(app, "_open_input_stream", open_microphone)
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_notify_tray_recording_state", lambda *a: None)
    monkeypatch.setattr(app, "recording_indicator", None, raising=False)
    assert app.start_recording(False, False, 0, from_toggle=from_toggle)
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


def test_a_word_said_before_the_second_tap_is_kept(monkeypatch):
    """Tap, say a quick word, tap again, then nothing more: the word is
    dictation, so going quiet stops the recording to type it, not to drop
    it as "nothing said"."""
    app = _start(monkeypatch)
    word = speech(0.4)
    for i in range(0, len(word), BLOCK):
        app._sd_callback(word[i:i + BLOCK].reshape(-1, 1), BLOCK, None, None)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    quiet = noise(12.0)
    for i in range(0, len(quiet), BLOCK):
        app._sd_callback(quiet[i:i + BLOCK].reshape(-1, 1), BLOCK, None, None)
    assert app._cmd_stop_recording.is_set()
    assert not app.state.silence.nothing_said
    app._cmd_stop_recording.clear()
    app.state.is_recording = False


def test_the_start_beep_picked_up_by_the_mic_is_not_a_word(monkeypatch):
    """Speakers next to the mic: the start beep is recorded, but a double-tap
    with nothing said after it is still nothing said."""
    app = _start(monkeypatch)
    t = np.arange(int(app.START_BEEP[1] * SR)) / SR
    beep = (np.sin(2 * np.pi * app.START_BEEP[0] * t) * 8000).astype(np.int16)
    heard = np.concatenate([noise(0.1), beep, noise(0.3)])   # it sounds a moment in
    for i in range(0, len(heard), BLOCK):
        app._sd_callback(heard[i:i + BLOCK].reshape(-1, 1), BLOCK, None, None)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    quiet = noise(12.0)
    for i in range(0, len(quiet), BLOCK):
        app._sd_callback(quiet[i:i + BLOCK].reshape(-1, 1), BLOCK, None, None)
    assert app.state.silence.nothing_said
    app._cmd_stop_recording.clear()
    app.state.is_recording = False


def test_on_a_slow_output_the_start_beep_picked_up_late_is_not_a_word(monkeypatch):
    """The beep reaches the speakers, and so the mic, one output latency
    after it is played: 0.3 s here, past two beep lengths."""
    app = _start(monkeypatch)
    from tuxtalk import beeps
    monkeypatch.setattr(beeps, "output_latency", lambda: 0.3)
    t = np.arange(int(app.START_BEEP[1] * SR)) / SR
    beep = (np.sin(2 * np.pi * app.START_BEEP[0] * t) * 8000).astype(np.int16)
    heard = np.concatenate([noise(0.3 + 0.02), beep, noise(0.2)])
    for i in range(0, len(heard), BLOCK):
        app._sd_callback(heard[i:i + BLOCK].reshape(-1, 1), BLOCK, None, None)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    quiet = noise(12.0)
    for i in range(0, len(quiet), BLOCK):
        app._sd_callback(quiet[i:i + BLOCK].reshape(-1, 1), BLOCK, None, None)
    assert app.state.silence.nothing_said
    app._cmd_stop_recording.clear()
    app.state.is_recording = False


def test_a_quick_second_tap_counts_when_the_recording_was_slow_to_start(monkeypatch):
    """Issue #21: the keys are read on the thread that starts the recording,
    so a second tap made while the microphone and the start beep took most
    of a second is only read afterwards. It is still a double-tap."""
    app = _start(monkeypatch, mic_delay=0.9)
    assert app._toggle_pressed_while_recording(beeps_on=False) is True
    app.state.is_recording = False


def _slow_start_beep(monkeypatch):
    """The start beep takes half a second to be heard, as on a slow output.
    Returns when each wait for it ended."""
    from tuxtalk import beeps
    waits = []

    def wait_until_played(limit=beeps.WAIT_LIMIT_S):
        time.sleep(0.5)
        waits.append(time.time())
        return True
    monkeypatch.setattr(beeps, "wait_until_played", wait_until_played)
    return waits


def test_the_double_tap_is_timed_from_the_start_beep_heard(monkeypatch):
    """Issue #21: the user waits for the start beep before the second tap,
    and on a slow output it comes most of a second after the first."""
    waits = _slow_start_beep(monkeypatch)
    app = _start(monkeypatch)
    assert len(waits) == 1
    assert app.state.toggle_started_at >= waits[0]
    app.state.is_recording = False


def test_a_hold_start_never_waits_for_the_start_beep(monkeypatch):
    waits = _slow_start_beep(monkeypatch)
    app = _start(monkeypatch, from_toggle=False)
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
