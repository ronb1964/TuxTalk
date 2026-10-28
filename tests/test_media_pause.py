"""Pause music and videos while dictating (config pause_media).

Sound from the speakers reaches the microphone (a YouTube video can turn "isn't
it" into "Venezuela"), so TuxTalk pauses every MPRIS player that is playing
when a recording starts and, when it ends, resumes only the ones it paused.
"""
import threading
import time

import pytest

from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs
from tuxtalk import config as C
from tuxtalk.media_pause import MediaPauser
from tuxtalk import keyboard_grab
from tuxtalk import tray_link
from tuxtalk import recording
from tuxtalk import mic_stream


class FakeBus:
    """The session bus as MediaPauser sees it: MPRIS players and their state."""

    def __init__(self, **players):
        self.status = dict(players)   # player name -> "Playing" / "Paused" / "Stopped"
        self.calls = []               # (player, method) in order
        self.hang = set()             # players that never answer
        self.release = threading.Event()

    def players(self):
        return list(self.status)

    def playback_status(self, name):
        if name in self.hang:
            self.release.wait(5)
            raise TimeoutError(name)
        return self.status[name]

    def call(self, name, method):
        self.calls.append((name, method))
        self.status[name] = {"Pause": "Paused", "Play": "Playing"}[method]


def run(pauser, *steps):
    for step in steps:
        step()
    assert pauser.wait_idle(2), "the media worker never finished"


def test_pauses_only_what_is_playing():
    bus = FakeBus(youtube="Playing", spotify="Paused", vlc="Stopped")
    p = MediaPauser(bus)
    run(p, p.pause)
    assert bus.calls == [("youtube", "Pause")]


def test_resumes_only_what_it_paused():
    bus = FakeBus(youtube="Playing", spotify="Paused")
    p = MediaPauser(bus)
    run(p, p.pause, p.resume)
    assert bus.calls == [("youtube", "Pause"), ("youtube", "Play")]
    assert bus.status == {"youtube": "Playing", "spotify": "Paused"}


def test_leaves_a_player_alone_that_the_user_restarted_or_stopped():
    bus = FakeBus(youtube="Playing", vlc="Playing")
    p = MediaPauser(bus)
    run(p, p.pause)
    bus.status["youtube"] = "Playing"   # user pressed play during the dictation
    bus.status["vlc"] = "Stopped"       # user stopped it
    run(p, p.resume)
    assert ("youtube", "Play") not in bus.calls
    assert ("vlc", "Play") not in bus.calls


def test_resume_without_pause_does_nothing():
    bus = FakeBus(youtube="Paused")
    p = MediaPauser(bus)
    run(p, p.resume)
    assert bus.calls == []


def test_a_second_dictation_does_not_resume_the_first_ones_players_twice():
    bus = FakeBus(youtube="Playing")
    p = MediaPauser(bus)
    run(p, p.pause, p.resume, p.resume)
    assert bus.calls.count(("youtube", "Play")) == 1


def test_a_hung_player_never_blocks_the_caller():
    """pause() runs while every keyboard is grabbed; it must return at once."""
    bus = FakeBus(stuck="Playing", youtube="Playing")
    bus.hang.add("stuck")
    p = MediaPauser(bus)
    t0 = time.monotonic()
    p.pause()
    p.resume()
    assert time.monotonic() - t0 < 0.1
    bus.release.set()
    assert p.wait_idle(2)
    # The stuck player is skipped; the others are still handled in order.
    assert bus.calls == [("youtube", "Pause"), ("youtube", "Play")]


def test_a_failing_bus_is_logged_not_raised():
    class Broken(FakeBus):
        def players(self):
            raise RuntimeError("no session bus")
    p = MediaPauser(Broken())
    run(p, p.pause, p.resume)   # no exception escapes, the worker keeps going


def test_resuming_at_exit_runs_on_the_calling_thread():
    """At exit the worker may not get another turn."""
    bus = FakeBus(youtube="Playing")
    p = MediaPauser(bus)
    run(p, p.pause)
    p._submit = lambda job: pytest.fail("left to the worker")
    p.wait_idle = lambda timeout: True
    p.resume_at_exit()
    assert bus.calls[-1] == ("youtube", "Play")


def test_pause_media_is_on_by_default_and_applies_live():
    assert C.Settings().pause_media is True
    assert "pause_media" in C.LIVE_APPLIED_KEYS


# --- wiring into the dictation service -------------------------------------

class SpyPauser:
    def __init__(self):
        self.calls = []

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")


class FakeStream:
    def start(self): pass
    def stop(self): pass
    def close(self): pass


@pytest.fixture
def service(monkeypatch):
    """start/stop/cancel with the hardware faked out and a spy pauser."""
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *a, **k: None)
    monkeypatch.setattr(mic_stream, "input_samplerate", lambda idx, rate: 16000)
    monkeypatch.setattr(recording, "indicator", None)
    monkeypatch.setattr(recording.sd, "InputStream", lambda *a, **k: FakeStream())
    spy = SpyPauser()
    monkeypatch.setattr(recording, "_media_pauser", spy)
    monkeypatch.setattr(live, "pause_media", True)
    rs.state.is_recording = False
    yield spy
    rs.state.is_recording = False
    rs.state.stream = None


def test_the_service_never_pauses_real_players_until_config_says_so():
    """The service turns this on from config at startup, not at import."""
    import subprocess, sys
    out = subprocess.run(
        [sys.executable, "-c", "from tuxtalk import app, live_settings, recording; "
         "print(live_settings.pause_media, recording._media_pauser)"],
        capture_output=True, text=True, check=True)
    assert out.stdout.split()[-2:] == ["False", "None"]


def test_the_suite_cannot_reach_real_media_players():
    from tuxtalk.media_pause import SessionBus
    with pytest.raises(RuntimeError):
        SessionBus().players()


def test_recording_start_pauses_and_cancel_resumes(service):
    assert recording.start_recording(False, False, 0) is True
    recording.cancel_recording(False, False)
    assert service.calls == ["pause", "resume"]


def test_a_finished_recording_resumes(service):
    recording.start_recording(False, False, 0)
    rs.state.press_t0 -= 5           # held long enough to count
    assert recording.finish_capture(False, False) is not None
    assert service.calls == ["pause", "resume"]


def test_too_short_a_tap_still_resumes(service):
    recording.start_recording(False, False, 0)
    assert recording.finish_capture(False, False) is None
    assert service.calls == ["pause", "resume"]


def test_switched_off_pauses_nothing(service, monkeypatch):
    monkeypatch.setattr(live, "pause_media", False)
    recording.start_recording(False, False, 0)
    recording.cancel_recording(False, False)
    assert "pause" not in service.calls


def test_a_microphone_failure_pauses_nothing(service, monkeypatch):
    monkeypatch.setattr(recording.sd, "InputStream",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("no mic")))
    monkeypatch.setattr(recording, "pick_input_device", lambda mic: 0)
    monkeypatch.setattr(keyboard_grab, "release_all", lambda: None)
    assert recording.start_recording(False, False, 0) is False
    assert service.calls == []


def test_quitting_right_after_a_pause_was_asked_for_leaves_nothing_paused():
    """The pause was still waiting for the worker when TuxTalk quit: the
    exit's resume ran first and found nothing, then the pause went through,
    and the music stayed paused after TuxTalk was gone."""
    gate = threading.Event()

    class SlowBus(FakeBus):
        def players(self):
            gate.wait(5)                    # the worker is still busy asking
            return super().players()
    bus = SlowBus(youtube="Playing")
    p = MediaPauser(bus)
    p.pause()                                # queued, not done yet
    threading.Timer(0.2, gate.set).start()
    p.resume_at_exit()
    assert bus.calls == [("youtube", "Pause"), ("youtube", "Play")]
    assert bus.status == {"youtube": "Playing"}
