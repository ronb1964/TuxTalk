"""A fake pactl for the tests of audio_duck and sound_control: sink inputs with
per-channel volumes, answering list and set-sink-input-volume."""
import json
import subprocess


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
