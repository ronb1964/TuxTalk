"""Pause music, play music, next and previous track by voice (music_control.py),
with a pretend session bus (test_media_pause.FakeBus): no test reaches a real
media player. TuxTalk pauses music while you dictate; these commands share
its worker, so a "pause music" said during a recording isn't undone when the
recording ends."""
import time

import pytest

from test_media_pause import FakeBus, run
from type_at_pause_rig import service_tick_runs
from tuxtalk import media_pause, music_control, recording
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs
from tuxtalk.command_phrases import MusicRequest
from tuxtalk.media_pause import MediaPauser
from tuxtalk.music_control import Choice


@pytest.fixture
def bus():
    return FakeBus()


@pytest.fixture
def pauser(bus, monkeypatch):
    """The MediaPauser dictation and the music commands share, on *bus*."""
    p = MediaPauser(bus)
    monkeypatch.setattr(recording, "_media_pauser", p)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    yield p
    bus.release.set()


def test_pause_music_said_mid_recording_stays_paused_after_it(pauser, bus):
    bus.status = {"spotify": "Playing"}
    run(pauser, pauser.pause)                                # the recording starts
    assert music_control.run(MusicRequest("pause"), False) is True
    run(pauser, pauser.resume)                               # the recording ends
    assert bus.status["spotify"] == "Paused"
    assert bus.calls == [("spotify", "Pause")]               # TuxTalk's; no Play after


def test_pause_music_said_at_a_pause_in_type_at_pause_stays_paused_after_the_recording(
        pauser, bus, actions_on, recording_on, chat):
    """Type at pause acts on the phrase while still recording: the resume at
    the recording's end must not start the music the user just paused."""
    bus.status = {"spotify": "Playing"}
    rec, phrase = recording_on
    run(pauser, pauser.pause)                                # the recording starts
    phrase("pause music")
    service_tick_runs()
    assert rec.message.cancelled and rs.state.is_recording is False
    run(pauser, pauser.resume)                               # the recording's resume
    assert bus.status["spotify"] == "Paused" and ("spotify", "Play") not in bus.calls


def test_play_music_mid_recording_is_not_paused_again(pauser, bus):
    bus.status = {"spotify": "Playing"}
    run(pauser, pauser.pause)
    music_control.run(MusicRequest("play"), False)
    run(pauser, pauser.resume)
    assert bus.calls == [("spotify", "Pause"), ("spotify", "Play")]
    assert bus.status["spotify"] == "Playing"


def test_pause_music_after_the_hold_key_came_up_pauses_the_resumed_player(pauser, bus):
    bus.status = {"spotify": "Playing"}
    run(pauser, pauser.pause, pauser.resume)                 # key up: resumed, as today
    music_control.run(MusicRequest("pause"), False)
    assert bus.status["spotify"] == "Paused"


def test_pause_music_pauses_every_player_that_plays(pauser, bus):
    bus.status = {"spotify": "Playing", "vlc": "Playing", "mpv": "Stopped"}
    assert music_control.run(MusicRequest("pause"), False) is True
    assert bus.status == {"spotify": "Paused", "vlc": "Paused", "mpv": "Stopped"}


def test_next_track_goes_to_the_player_tuxtalk_paused(pauser, bus):
    bus.status = {"spotify": "Playing", "vlc": "Paused"}
    run(pauser, pauser.pause)
    assert music_control.run(MusicRequest("next"), False) is True
    assert bus.calls == [("spotify", "Pause"), ("spotify", "Next")]
    run(pauser, pauser.resume)                               # still resumed afterwards
    assert bus.status["spotify"] == "Playing"


def test_previous_track_goes_to_the_one_playing(pauser, bus):
    bus.status = {"spotify": "Paused", "vlc": "Playing"}
    assert music_control.run(MusicRequest("previous"), False) is True
    assert bus.calls == [("vlc", "Previous")]


def test_play_music_with_one_paused_player_starts_it(pauser, bus):
    bus.status = {"spotify": "Paused", "vlc": "Stopped"}
    assert music_control.run(MusicRequest("play"), False) is True
    assert bus.calls == [("spotify", "Play")] and bus.status["spotify"] == "Playing"


def test_play_music_with_two_paused_players_names_them(pauser, bus, actions_on):
    bus.status = {"spotify": "Paused", "vlc": "Paused"}
    bus.identities = {"spotify": "Spotify", "vlc": "VLC"}
    assert music_control.run(MusicRequest("play"), False) is False
    assert actions_on == ["Several players paused: Spotify, VLC"]
    assert not [c for c in bus.calls if c[1] == "Play"]


def test_a_player_without_a_name_is_named_by_its_bus_name(pauser, bus, actions_on):
    bus.status = {f"{media_pause.MPRIS_PREFIX}spotify": "Paused", "vlc": "Paused"}
    bus.identities = {"vlc": "VLC"}
    music_control.run(MusicRequest("next"), False)
    assert actions_on == ["Several players paused: spotify, VLC"]


def test_no_player_says_so(pauser, bus, actions_on):
    assert music_control.run(MusicRequest("next"), False) is False
    assert actions_on == ["No music player to control"]


def test_pause_with_nothing_playing_says_so(pauser, bus, actions_on):
    bus.status = {"spotify": "Paused"}
    assert music_control.run(MusicRequest("pause"), False) is False
    assert actions_on == ["No music is playing"] and bus.calls == []


def test_playerctld_is_never_counted(pauser, bus):
    bus.status = {"org.mpris.MediaPlayer2.playerctld": "Paused", "spotify": "Paused"}
    assert music_control.run(MusicRequest("play"), False) is True
    assert bus.calls == [("spotify", "Play")]


@pytest.mark.parametrize("action, status, by_us, targets, forget, note", [
    ("pause", {"a": "Playing"}, ("b",), ("a",), {"b"}, None),
    ("pause", {}, (), (), set(), "no_player"),
    ("pause", {"a": "Stopped"}, (), (), set(), "nothing_playing"),
    ("play", {"a": "Paused"}, ("a",), ("a",), {"a"}, None),
    ("play", {"a": "Playing"}, (), (), set(), None),
    ("play", {"a": "Paused", "b": "Paused"}, (), (), set(), "which_player"),
    ("play", {"a": "Stopped"}, (), (), set(), "no_player"),
    ("next", {"a": "Playing", "b": "Paused"}, ("b",), ("b",), set(), None),
    ("next", {"a": "Playing", "b": "Playing"}, (), (), set(), "which_player"),
    ("previous", {}, (), (), set(), "no_player")])
def test_choose(action, status, by_us, targets, forget, note):
    assert music_control.choose(action, status, by_us) == Choice(targets, frozenset(forget),
                                                                 note)


def test_a_hung_player_never_holds_the_dictation_past_the_wait(pauser, bus):
    bus.status = {"stuck": "Playing"}
    bus.hang.add("stuck")
    t0 = time.monotonic()
    assert music_control.run(MusicRequest("pause"), False) is False
    assert time.monotonic() - t0 < media_pause.COMMAND_WAIT_S + 1


def test_a_command_the_worker_never_reached_is_dropped(pauser, bus):
    """Past the wait the command is given up; it mustn't act later, long
    after it was said."""
    bus.status = {"stuck": "Playing", "spotify": "Playing"}
    bus.hang.add("stuck")
    pauser.pause()                                           # holds the worker
    assert pauser.run_command(lambda b, paused: (b.call("spotify", "Next"), frozenset()),
                              timeout=0.05) is None
    bus.release.set()
    assert pauser.wait_idle(6)
    assert ("spotify", "Next") not in bus.calls


def test_music_commands_work_with_pausing_while_dictating_off(bus, monkeypatch):
    monkeypatch.setattr(live, "pause_media", False)
    monkeypatch.setattr(live, "duck_audio", False)
    monkeypatch.setattr(recording, "_media_pauser", None)
    monkeypatch.setattr(media_pause, "SessionBus", lambda: bus)
    monkeypatch.setattr(recording.atexit, "register", lambda f: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    bus.status = {"spotify": "Playing"}
    assert music_control.run(MusicRequest("pause"), False) is True
    assert bus.status["spotify"] == "Paused"
    recording.pause_media_players()                          # still pauses nothing itself
    assert bus.calls == [("spotify", "Pause")]


def test_pause_music_said_alone_types_nothing(actions_on, chat, say, pauser, bus):
    bus.status = {"spotify": "Playing"}
    say("Pause music.")
    assert chat[2] == [] and bus.status["spotify"] == "Paused"
