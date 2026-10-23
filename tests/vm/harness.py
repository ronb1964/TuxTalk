"""The pieces every VM release check uses: the check registry, the outcome
of a check, and Run, which holds one VM's test session (its TuxTalk log,
config, the text window TuxTalk types into, the pretend microphone).
"""
import ast
import json
import re
import shlex
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import voices
from vmlib import REMOTE

LOG = "~/.config/tuxtalk/tuxtalk.log"
CONFIG = "~/.config/tuxtalk/config.toml"
# What the tray (and Preferences, which it starts) print, from session_background.
OUT = f"~/{REMOTE}/tuxtalk.out"
DBUS = ("gdbus call --session --dest io.github.ronb1964.TalkType "
        "--object-path /io/github/ronb1964/TalkType --method io.github.ronb1964.TalkType.")

# Log lines that mean a dictation has been dealt with, one way or another.
FINISHED = ("Text injected", "Auto-stop: nothing said", "sent only silence",
            "recording with no speech", "Pressed Enter", "Voice command:",
            "in a password field")


@dataclass
class Outcome:
    status: str                 # pass, fail, manual, misheard, skip, error
    detail: str = ""
    expected: str = None
    typed: str = None
    heard: str = None
    screenshot: str = None      # file name under the run's shots/


@dataclass
class Result(Outcome):
    id: str = ""
    name: str = ""
    section: str = ""
    vm: str = ""
    log: str = ""
    seconds: float = 0.0


class Abort(Exception):
    """A check found TuxTalk unusable: the remaining checks are skipped."""


@dataclass
class Check:
    section: str
    id: str
    name: str
    func: object
    desktops: tuple = ()        # empty: every desktop


CHECKS: list = []


def check(section, check_id, name, desktops=()):
    def register(func):
        CHECKS.append(Check(section, check_id, name, func, tuple(desktops)))
        return func
    return register


def squash(text):
    """Letters and digits only, lower case: for telling "the voice was
    misheard" apart from "TuxTalk typed it wrong"."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\W_]+", "", text)


class Run:
    def __init__(self, vm, out_dir: Path, appimage: Path):
        self.vm = vm
        self.out_dir = out_dir
        self.appimage = appimage
        self.shots = out_dir / "shots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.uploaded = set()
        self.voices_used = set()

    # Screenshots

    def shot(self, name):
        """A screenshot for the report; returns its file name under shots/."""
        file = f"{self.vm.spec.key}-{name}.jpg"
        self.vm.move(self.vm.spec.width - 2, self.vm.spec.height - 2)   # no tooltip in the picture
        time.sleep(0.3)
        self.vm.screenshot(self.shots / file)
        return file

    # The TuxTalk log

    def log_mark(self, file=LOG):
        out = self.vm.ssh(f"stat -c %s {file} 2>/dev/null || echo 0", check=False).stdout.strip()
        return int(out or 0)

    def log_since(self, mark, file=LOG):
        return self.vm.ssh(f"tail -c +{mark + 1} {file} 2>/dev/null", check=False).stdout

    def wait_log(self, mark, *needles, timeout=30):
        """The log since *mark* once it holds any of *needles*, else None."""
        deadline = time.monotonic() + timeout
        while True:
            text = self.log_since(mark)
            if any(n in text for n in needles):
                return text
            if time.monotonic() > deadline:
                return None
            time.sleep(0.5)

    # Settings

    def config(self):
        values = {}
        for line in self.vm.read(CONFIG).splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
        return values

    def set_config(self, **changes):
        """Write *changes* into config.toml and restart the dictation
        service, which reads its settings when it starts."""
        lines = self.vm.read(CONFIG).splitlines()
        for key, value in changes.items():
            if isinstance(value, bool):
                value = "true" if value else "false"
            elif isinstance(value, str):
                value = json.dumps(value)
            entry = f"{key} = {value}"
            for i, line in enumerate(lines):
                if line.split("=", 1)[0].strip() == key:
                    lines[i] = entry
                    break
            else:
                lines.append(entry)
        self.vm.ssh(f"cat > {CONFIG}.tt && mv {CONFIG}.tt {CONFIG}", input="\n".join(lines) + "\n")
        self.restart_service()

    def restart_service(self):
        mark = self.log_mark()
        self.vm.session(*shlex.split(DBUS + "RestartService"))
        if self.wait_log(mark, "Dictation service is ready", timeout=90) is None:
            raise Abort("the dictation service didn't come back after a restart")
        time.sleep(2)   # it reports ready a moment before the hotkeys are grabbed

    # The text window TuxTalk types into

    def sink_ready(self, name="sink", title="TT Sink", fresh=False, password=False):
        """The test window open, empty and in front. *fresh* opens it anew,
        on top of every other window (two test windows are both maximised,
        so the click that focuses one lands on whichever is on top).
        *password*: one password box instead of a text area."""
        pid_file = f"~/{REMOTE}/{name}.txt.pid"
        if fresh:
            self.close_sink(name)
        alive = f"kill -0 $(cat {pid_file} 2>/dev/null) 2>/dev/null"
        if self.vm.ssh(alive, check=False).returncode:
            path = f"{self.vm.home}/{REMOTE}/{name}.txt"
            self.vm.ssh(f"rm -f {path} {path}.pid")
            self.vm.session_background(name, "python3", f"{self.vm.home}/{REMOTE}/sink.py",
                                       path, title, *(["password"] if password else []))
            if self.vm.ui("find", "frame", title, timeout=20) is None:
                raise Abort(f"the test window {title!r} didn't open")
            time.sleep(1)
        else:
            self.vm.ssh(f"kill -USR1 $(cat {pid_file})")
        # A click puts it in front whatever opened since; in an empty text
        # box it can only land the cursor at the start.
        self.vm.click_centre()
        time.sleep(0.5)

    def close_sink(self, name="sink"):
        self.vm.ssh(f"kill $(cat ~/{REMOTE}/{name}.txt.pid) 2>/dev/null", check=False)
        time.sleep(1)

    def sink_text(self, name="sink"):
        return self.vm.read(f"~/{REMOTE}/{name}.txt")

    # Speaking

    def play(self, phrase_id):
        """Play a phrase into the pretend microphone; returns once it ends."""
        wav, voice = voices.wav_for(phrase_id)
        self.voices_used.add(voice)
        remote = f"{REMOTE}/phrases/{wav.name}"
        if remote not in self.uploaded:
            self.vm.ssh(f"mkdir -p ~/{REMOTE}/phrases")
            self.vm.put(wav, remote)
            self.uploaded.add(remote)
        self.vm.ssh(f"pw-play --target tt_voice ~/{remote}")

    def wait_dictation(self, mark, sink="sink", timeout=25):
        """Until TuxTalk has finished with the dictation and stopped typing."""
        self.wait_log(mark, *FINISHED, timeout=timeout)
        last, steady_since = None, time.monotonic()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            text = self.sink_text(sink)
            if text != last:
                last, steady_since = text, time.monotonic()
            elif time.monotonic() - steady_since > 1.5:
                break
            time.sleep(0.3)
        return last

    def say(self, phrase_id, key="f8", sink="sink", wait=True):
        """Hold *key*, speak the phrase, let go. Returns (typed, heard, log),
        or with wait=False at once (None, None, log mark), for checks that act
        while TuxTalk is still busy."""
        mark = self.log_mark()
        self.vm.key_down(key)
        try:
            time.sleep(0.4)
            self.play(phrase_id)
            time.sleep(0.3)
        finally:
            # A phrase that failed to play must not leave the key held down
            # for every check after it.
            self.vm.key_up(key)
        if not wait:
            return None, None, mark
        typed = self.wait_dictation(mark, sink)
        log = self.log_since(mark)
        return typed, heard_in(log), log

    def expect_typed(self, phrase_id, expected, typed, heard, exact=True):
        """Outcome for a dictation that should have typed *expected*. A
        failure whose words Parakeet never heard is "misheard": the stand-in
        voice's fault, not TuxTalk's."""
        typed_clean = (typed or "").rstrip(" ")
        ok = typed_clean == expected if exact else expected in typed_clean
        if ok:
            return Outcome("pass", expected=expected, typed=typed_clean, heard=heard)
        said = voices.load_phrases()[phrase_id]["say"]
        if heard is not None and squash(heard) != squash(said):
            return Outcome("misheard", "Parakeet heard different words from the ones played",
                           expected=expected, typed=typed_clean, heard=heard)
        return Outcome("fail", expected=expected, typed=typed_clean, heard=heard)

    def window_blind(self):
        """Why TuxTalk can't tell which window has the focus here, or None.
        On GNOME only its extension can say (and hold F8 back from the app:
        gnome_hotkeys.py), so without it terminals and window switches look
        like any other window, by design."""
        if "gnome" not in self.vm.spec.tags:
            return None
        enabled = self.vm.session("gnome-extensions", "list", "--enabled", check=False).stdout
        if "talktype@ronb1964.github.io" in enabled:
            return None
        return ("Skipped: GNOME without the TuxTalk extension can't tell TuxTalk which window "
                "has the focus or hold F8 back (by design). Checked again with the extension.")

    def dbus(self, method):
        return self.vm.session(*shlex.split(DBUS + method), check=False)


def heard_in(log):
    """What the engine heard, from the log (log_transcripts must be on)."""
    found = re.findall(r"Raw transcription \([^)]*\): (.*)", log)
    if not found or found[-1].startswith("[hidden"):
        return None
    try:
        return ast.literal_eval(found[-1])     # logged as a Python repr: 'text'
    except (SyntaxError, ValueError):
        return found[-1]
