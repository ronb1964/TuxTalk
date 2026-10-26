"""Turn other sounds down while dictating (audio_duck.py), with a fake pactl.

Asked for in Handy (#2008, "must have"); Superwhisper ("Muffle", Oct 2) and
Vocalinux 0.18 (Oct 6) added it. It reaches what pausing can't: calls,
games, apps without media keys.
"""
import json
import subprocess

from tuxtalk import audio_duck
from tuxtalk import live_settings as live
from tuxtalk import recording

OWN_PID = 4242


class FakePactl:
    """Sink inputs with per-channel volumes; answers list and set-volume.

    As on real PipeWire, a sink input carries no process id: its client does
    (a stream's own properties had none, so TuxTalk's beeps were taken for
    another app's and turned down until silent, 2026-10-17)."""

    def __init__(self, streams):
        # index -> {"pid":, "volumes": [...], "mute":, "app":, "role":}
        self.streams = streams
        self.calls = []
        self.failing_sets = 0       # the next this many set-volumes fail (a pactl hiccup)

    def __call__(self, cmd, **kw):
        self.calls.append(cmd[1:])
        args = cmd[1:]
        if args[:4] == ["-f", "json", "list", "sink-inputs"]:
            out = [{"index": i, "mute": s.get("mute", False), "client": str(1000 + i),
                    "corked": s.get("corked", False),
                    "properties": {"application.name": s.get("app", f"app{i}"),
                                   **({"media.role": s["role"]} if "role" in s else {})},
                    "volume": {f"ch{n}": {"value": v} for n, v in enumerate(s["volumes"])}}
                   for i, s in self.streams.items()]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")
        if args[:4] == ["-f", "json", "list", "clients"]:
            out = [{"index": 1000 + i, "properties": {"application.process.id": str(s["pid"])}}
                   for i, s in self.streams.items()]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")
        if args[0] == "set-sink-input-volume":
            index = int(args[1])
            if index not in self.streams:
                return subprocess.CompletedProcess(cmd, 1, "", "No such entity")
            if self.failing_sets:
                self.failing_sets -= 1
                return subprocess.CompletedProcess(cmd, 1, "", "Connection failure")
            self.streams[index]["volumes"] = [int(v) for v in args[2:]]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "unknown")


def _no_reset(app, volumes):
    """Tests never play sound: resetting the desktop's memory 'fails'."""
    return False


def _ducker(tmp_path, pactl, reset=_no_reset):
    return audio_duck.AudioDucker(run=pactl, own_pid=OWN_PID, state_path=str(tmp_path / "ducked.json"),
                                  reset=reset)


def test_other_apps_go_down_and_come_back(tmp_path):
    pactl = FakePactl({7: {"pid": 100, "volumes": [65536, 65536]},
                       9: {"pid": 200, "volumes": [40000]}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    assert pactl.streams[7]["volumes"] == [32768, 32768]
    assert pactl.streams[9]["volumes"] == [20000]
    assert (tmp_path / "ducked.json").exists()       # for a crash mid-dictation
    d._restore_now()
    assert pactl.streams[7]["volumes"] == [65536, 65536]
    assert pactl.streams[9]["volumes"] == [40000]
    assert not (tmp_path / "ducked.json").exists()


def test_tuxtalks_own_beeps_and_muted_streams_are_left_alone(tmp_path):
    pactl = FakePactl({1: {"pid": OWN_PID, "volumes": [65536]},
                       2: {"pid": 300, "volumes": [65536], "mute": True}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    assert pactl.streams[1]["volumes"] == [65536]
    assert pactl.streams[2]["volumes"] == [65536]
    assert not any(c[0] == "set-sink-input-volume" for c in pactl.calls)


def test_a_volume_the_user_changed_meanwhile_is_kept(tmp_path):
    pactl = FakePactl({7: {"pid": 100, "volumes": [65536]}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    pactl.streams[7]["volumes"] = [30000]          # turned up by hand during dictation
    d._restore_now()
    assert pactl.streams[7]["volumes"] == [30000]


def test_a_volume_that_wouldnt_go_back_is_tried_again(tmp_path, monkeypatch):
    """A pactl hiccup as the dictation ended: the app's record was thrown
    away, and the desktop remembered it at half volume for good."""
    monkeypatch.setattr(audio_duck, "FOLLOW_UP_S", ())     # the follow-up is run by hand
    pactl = FakePactl({7: {"pid": 100, "volumes": [65536]}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    pactl.failing_sets = 2                     # the restore, and the look right after it
    d._restore_now()
    assert pactl.streams[7]["volumes"] == [32768]
    assert (tmp_path / "ducked.json").exists()  # owed, at the next start too
    d._settle_later(d._round, 0)
    assert pactl.streams[7]["volumes"] == [65536]
    assert not (tmp_path / "ducked.json").exists()


def test_a_stream_that_ended_is_skipped(tmp_path):
    pactl = FakePactl({7: {"pid": 100, "volumes": [65536]}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    del pactl.streams[7]
    d._restore_now()                                # no error


def test_left_over_volumes_are_put_back_at_the_next_start(tmp_path):
    pactl = FakePactl({7: {"pid": 100, "volumes": [65536]}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()                                   # ...and the service dies here
    assert pactl.streams[7]["volumes"] == [32768]
    restored = audio_duck.restore_left_over(run=pactl, state_path=str(tmp_path / "ducked.json"), reset=_no_reset)
    assert restored == 1
    assert pactl.streams[7]["volumes"] == [65536]
    assert not (tmp_path / "ducked.json").exists()


def test_no_pactl_is_not_an_error(tmp_path):
    def missing(cmd, **kw):
        raise FileNotFoundError("pactl")
    d = _ducker(tmp_path, missing)
    d._duck_now()
    d._restore_now()
    assert audio_duck.restore_left_over(run=missing, state_path=str(tmp_path / "none.json"), reset=_no_reset) == 0


def test_setting_is_off_by_default_and_live():
    from tuxtalk import config
    assert config.Settings().duck_audio is False
    assert "duck_audio" in config.LIVE_APPLIED_KEYS


def test_recording_hooks_duck_and_restore(monkeypatch):
    calls = []

    class Ducker:
        def duck(self):
            calls.append("duck")

        def restore(self):
            calls.append("restore")
    monkeypatch.setattr(live, "duck_audio", True)
    monkeypatch.setattr(live, "pause_media", False)
    monkeypatch.setattr(recording, "_audio_ducker", Ducker())
    recording.pause_media_players()
    recording.resume_media_players()
    assert calls == ["duck", "restore"]
    # Switched off mid-recording: still restored.
    monkeypatch.setattr(live, "duck_audio", False)
    recording.resume_media_players()
    assert calls[-1] == "restore"


def test_tuxtalks_beep_is_known_by_its_client_pid(tmp_path):
    """The real failure: the beep stream's own properties have no pid."""
    pactl = FakePactl({5: {"pid": OWN_PID, "volumes": [65536], "app": "PipeWire ALSA [python3.14]"}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    assert pactl.streams[5]["volumes"] == [65536]


def test_short_system_sounds_are_left_alone(tmp_path):
    pactl = FakePactl({5: {"pid": 77, "volumes": [65536], "role": "event"}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    assert pactl.streams[5]["volumes"] == [65536]


def test_a_sound_that_ends_while_down_is_put_back_and_never_lowered_twice(tmp_path):
    """Another app's beep ends while turned down; the desktop then starts its
    next one at that level. It's put back, and ducked from its real volume,
    not from the lowered one (three presses made Ron's beeps silent)."""
    pactl = FakePactl({5: {"pid": 77, "volumes": [65536], "app": "Beeper"}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    assert pactl.streams[5]["volumes"] == [32768]
    del pactl.streams[5]                            # the beep ended
    d._restore_now()
    assert d._owed["Beeper"] == {"original": [65536], "lowered": [32768]}
    # The next beep starts where the desktop remembered it: lowered.
    pactl.streams[6] = {"pid": 77, "volumes": [32768], "app": "Beeper"}
    d._duck_now()
    assert pactl.streams[6]["volumes"] == [32768]   # half of 65536, not of 32768
    d._restore_now()
    assert pactl.streams[6]["volumes"] == [65536]
    assert d._owed == {}


def test_an_owed_volume_is_put_back_when_the_app_plays_again_after_dictation(tmp_path):
    pactl = FakePactl({5: {"pid": 77, "volumes": [50000], "app": "Beeper"}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    del pactl.streams[5]
    d._restore_now()
    # A new TuxTalk start (the service restarted): read from the state file.
    pactl.streams[8] = {"pid": 77, "volumes": [25000], "app": "Beeper"}
    d2 = _ducker(tmp_path, pactl)
    d2._duck_now()
    d2._restore_now()
    assert pactl.streams[8]["volumes"] == [50000]
    assert not (tmp_path / "ducked.json").exists()


def test_an_owed_app_the_user_set_themselves_is_left_alone(tmp_path):
    pactl = FakePactl({5: {"pid": 77, "volumes": [50000], "app": "Beeper"}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    del pactl.streams[5]
    d._restore_now()
    pactl.streams[9] = {"pid": 77, "volumes": [30000], "app": "Beeper"}   # set by hand
    d._restore_now()
    assert pactl.streams[9]["volumes"] == [30000] and d._owed == {}


def test_a_player_reopening_its_sound_after_resume_gets_its_volume_back(tmp_path, monkeypatch):
    """Ron, 2026-10-17: YouTube in Brave stayed quiet after a hands-free
    dictation. Paused, Brave closed its stream; resumed, it opened a new one
    at the level the desktop remembered, a moment after the restore. It's
    looked for again for a few seconds after, not only at the next dictation."""
    monkeypatch.setattr(audio_duck, "FOLLOW_UP_S", (0.05, 0.1, 0.2))
    pactl = FakePactl({5: {"pid": 77, "volumes": [65536], "app": "Brave"}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    del pactl.streams[5]                            # paused: Brave closed its sound
    d._restore_now()
    pactl.streams[6] = {"pid": 77, "volumes": [32768], "app": "Brave"}   # resumed
    import time
    time.sleep(0.4)
    assert d.wait_idle(2)
    assert pactl.streams[6]["volumes"] == [65536]
    assert d._owed == {}


def test_a_paused_player_isnt_turned_down(tmp_path):
    pactl = FakePactl({5: {"pid": 77, "volumes": [65536], "app": "Brave", "corked": True}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    assert pactl.streams[5]["volumes"] == [65536] and d._ducked == {}


def test_a_stream_gone_while_down_has_the_desktops_memory_put_right_at_once(tmp_path):
    """Ron, 2026-10-17: Brave's video was already paused, so its silent stream
    was turned down and then closed; Brave played quiet for minutes after.
    Now the desktop's remembered volume for the app is reset right away."""
    resets = []
    pactl = FakePactl({5: {"pid": 77, "volumes": [65536, 65536], "app": "Brave"}})
    d = _ducker(tmp_path, pactl, reset=lambda app, v: resets.append((app, v)) or True)
    d._duck_now()
    del pactl.streams[5]
    d._restore_now()
    assert resets == [("Brave", [65536, 65536])]
    assert d._owed == {}


def test_a_player_that_reopened_its_sound_before_the_reset_gets_its_volume_back(tmp_path):
    """Ron, 2026-10-20, "Pause music and videos, turn the rest down": Brave
    was turned down a moment before its pause took, closed its sound, and
    opened a new one on resume, before the restore, at the lowered level the
    desktop remembered. The reset put the memory right but not that stream,
    so the video played on almost silent."""
    pactl = FakePactl({5: {"pid": 77, "volumes": [65536, 65536], "app": "Brave"}})
    d = _ducker(tmp_path, pactl, reset=lambda app, v: True)
    d._duck_now()
    del pactl.streams[5]                                                   # paused
    pactl.streams[6] = {"pid": 77, "volumes": [32768, 32768], "app": "Brave"}   # resumed
    d._restore_now()
    assert pactl.streams[6]["volumes"] == [65536, 65536]
    assert d._owed == {}


def test_owed_apps_from_before_are_reset_at_the_next_start(tmp_path):
    resets = []
    pactl = FakePactl({5: {"pid": 77, "volumes": [50000], "app": "Brave"}})
    d = _ducker(tmp_path, pactl)
    d._duck_now()
    del pactl.streams[5]
    d._restore_now()                                # reset "failed": owed, in the file
    assert d._owed
    audio_duck.restore_left_over(run=pactl, state_path=str(tmp_path / "ducked.json"),
                                 reset=lambda app, v: resets.append(app) or True)
    assert resets == ["Brave"]
    assert not (tmp_path / "ducked.json").exists()
