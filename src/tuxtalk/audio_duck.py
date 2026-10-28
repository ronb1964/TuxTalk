"""Turn other sounds down while dictating (config duck_audio).

Pausing (media_pause.py) only reaches players that answer media keys. A
Discord call, a game or a video in an app without MPRIS keeps playing into
the microphone. So while a recording runs, every other app's sound is turned
down to half its volume setting, and put back when it ends. A stream whose volume the user
changed in the meantime is left as they set it.

It talks to PipeWire or PulseAudio through pactl (pulseaudio-utils, present
on every desktop that uses either). TuxTalk's own streams, its beeps, are
never touched: on PipeWire a stream doesn't carry its process id, its client
does, so streams are matched to their client's pid. Short system sounds
(media.role "event") are left alone too.

The desktop remembers each app's volume (by application.name). A sound that
ends while it's turned down (a beep, a notification, a browser closing its
stream while its video is paused) would come back that quiet next time, and
a second dictation would lower it again: TuxTalk's beeps went silent in
three presses that way, and a browser's videos played quiet for minutes. So
the desktop's memory is corrected at once (reset_remembered: a moment of
silence played under the app's name at its real volume). Where that can't
be done, the app is owed its volume until TuxTalk next sees it at the
lowered level and puts it back; it's never lowered from there.

Each stream is written to a small file in the runtime folder before it's
turned down, so a service that stops mid-dictation, however it stops,
leaves a record the next start puts back (restore_left_over). That's as
good as the record: a stream the sound server has renumbered since (it
restarted) isn't found again.

Like media_pause, all the work runs on one background thread, which alone
touches what was turned down, the restore at exit included: duck() is
called while every keyboard is grabbed and must never wait on pactl.
"""
import json
import os
import queue
import subprocess
import threading

from .logger import setup_logger

logger = setup_logger(__name__)

DUCK_TO = 0.5           # half on the volume slider; a quarter sounded muted
# After a dictation, look again this long after (seconds) for an app owed
# its volume: a browser closes its sound while its video is paused and opens
# a new one on resume, a moment after TuxTalk restored, at the lowered level.
FOLLOW_UP_S = (0.5, 1.0, 2.0, 4.0, 8.0)
PACTL_TIMEOUT_S = 2.0
# How long exit waits for the worker's last restore; past it, the next start
# puts back what the state file says.
EXIT_WAIT_S = 2.0
STATE_FILE = "tuxtalk-ducked-audio.json"


def _state_path():
    """The record of what's turned down, in the user's private runtime
    folder; None when there's none to be had (then nothing is turned down)."""
    from . import single_instance
    try:
        return os.path.join(single_instance.runtime_dir(), STATE_FILE)
    except OSError as e:
        logger.warning(f"Audio duck: no private folder for its record, so nothing is "
                       f"turned down: {e}")
        return None


def _pactl(*args, run=subprocess.run):
    """pactl's output, or None if it isn't there or fails."""
    try:
        result = run(["pactl", *args], capture_output=True, text=True, timeout=PACTL_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug(f"Audio duck: pactl {args[0] if args else ''} failed: {e}")
        return None
    if result.returncode != 0:
        logger.debug(f"Audio duck: pactl {' '.join(args)}: {result.stderr.strip()}")
        return None
    return result.stdout


def _client_pids(run=subprocess.run):
    """{client index: pid} from pactl's clients, where PipeWire puts the pid."""
    out = _pactl("-f", "json", "list", "clients", run=run)
    try:
        clients = json.loads(out) if out else []
    except ValueError:
        return {}
    pids = {}
    for c in clients:
        try:
            pid = int(c.get("properties", {}).get("application.process.id") or 0)
            if pid:
                pids[int(c["index"])] = pid
        except (KeyError, TypeError, ValueError):
            continue
    return pids


def list_streams(run=subprocess.run):
    """The apps playing now: dicts of index, pid (0 if unknown), app (its
    application.name), role, volumes ([channel volumes, raw]) and muted."""
    out = _pactl("-f", "json", "list", "sink-inputs", run=run)
    if not out:
        return []
    try:
        streams = json.loads(out)
    except ValueError:
        return []
    clients = None
    found = []
    for s in streams:
        try:
            props = s.get("properties", {})
            pid = int(props.get("application.process.id") or 0)
            if not pid and s.get("client") not in (None, ""):
                if clients is None:
                    clients = _client_pids(run)
                pid = clients.get(int(s["client"]), 0)
            found.append({"index": int(s["index"]), "pid": pid,
                          "app": props.get("application.name") or "",
                          "role": props.get("media.role") or "",
                          "volumes": [int(ch["value"]) for ch in s.get("volume", {}).values()],
                          "muted": bool(s.get("mute")),
                          "corked": bool(s.get("corked"))})
        except (KeyError, TypeError, ValueError):
            continue
    return found


def _set_volume(index, volumes, run=subprocess.run):
    return _pactl("set-sink-input-volume", str(index), *[str(v) for v in volumes], run=run) is not None


RESET_TIMEOUT_S = 3.0


def reset_remembered(app, volumes, run=subprocess.run, popen=subprocess.Popen):
    """Make the desktop remember *volumes* for *app* again, when the stream
    TuxTalk turned down is gone: play a moment of silence under the app's
    name (paplay, from the same pulseaudio-utils as pactl), set that to the
    real volume, which the desktop stores for the app, and stop. True if it
    took."""
    import time
    try:
        player = popen(["paplay", "--raw", "--format=s16le", "--rate=48000", "--channels=2",
                        f"--client-name={app}", f"--property=application.name={app}"],
                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        logger.debug(f"Audio duck: paplay failed: {e}")
        return False
    done = False
    try:
        silence = b"\0" * (48000 * 4 // 10)            # 0.1 s
        deadline = time.monotonic() + RESET_TIMEOUT_S
        while time.monotonic() < deadline:
            player.stdin.write(silence)
            player.stdin.flush()
            mine = [st for st in list_streams(run) if st["pid"] == player.pid]
            if mine:
                st = mine[0]
                want = volumes if len(volumes) == len(st["volumes"]) else [max(volumes)] * len(st["volumes"])
                done = _set_volume(st["index"], want, run)
                break
        if done:
            player.stdin.write(silence)     # let the desktop see it before the stream ends
            player.stdin.flush()
    except (OSError, ValueError) as e:
        logger.debug(f"Audio duck: resetting {app!r}: {e}")
    finally:
        try:
            player.stdin.close()
        except OSError:
            pass
        try:
            player.wait(timeout=RESET_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            player.kill()
    if done:
        logger.info(f"Put {app!r} back to its volume for next time (its sound had ended)")
    return done


class AudioDucker:
    """duck() lowers every other app; restore() puts back what it lowered."""

    def __init__(self, run=subprocess.run, own_pid=None, state_path=None, reset=None):
        self._run = run
        self._reset = reset or (lambda app, volumes: reset_remembered(app, volumes, run))
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._state_path = state_path or _state_path()
        self._ducked = {}          # index -> (original volumes, ducked volumes, app)
        # app -> {"original":, "lowered":}: its stream ended while turned
        # down, so the desktop remembers it lowered (see the top).
        self._owed = self._load_owed()
        self._jobs = queue.Queue()
        self._worker = None
        self._closing = False      # exiting: a turn-down not started yet does nothing
        self._round = 0            # each duck starts a new one; old follow-ups stop

    # -- called from the dictation service; never block -------------------

    def duck(self):
        self._submit(self._duck_now)

    def restore(self):
        self._submit(self._restore_now)

    def close(self, timeout=EXIT_WAIT_S):
        """For exit: put back what was turned down, as the worker's last job.
        A turn-down already under way finishes first and is undone by it; one
        not started yet does nothing. Restored on the worker, never here: a
        volume the worker is setting would otherwise be noted after this
        restored, and stayed down. Past *timeout* the exit goes on, and the
        next start restores from the state file."""
        self._closing = True
        done = threading.Event()

        def last_job():
            try:
                self._restore_now()
            finally:
                done.set()
        self._submit(last_job)
        if not done.wait(timeout):
            logger.warning("Audio duck: sounds not put back in time at exit; "
                           "the next start puts them back")

    def wait_idle(self, timeout):
        """Wait for queued jobs to finish. True if they did."""
        done = threading.Event()
        self._submit(done.set)
        return done.wait(timeout)

    # -- the worker -------------------------------------------------------

    def _submit(self, job):
        if self._worker is None:
            self._worker = threading.Thread(target=self._run_jobs, name="audio-duck", daemon=True)
            self._worker.start()
        self._jobs.put(job)

    def _run_jobs(self):
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception as e:
                logger.debug(f"Audio duck: {e}")

    def _duck_now(self):
        if self._closing:
            return
        self._round += 1
        streams = list_streams(self._run)
        self._settle_owed(streams)
        for st in streams:
            index, volumes = st["index"], st["volumes"]
            # Paused (corked) already: silent, and likely to close and come
            # back as a new stream, so there's nothing to gain lowering it.
            if (st["pid"] == self._own_pid or st["muted"] or st["role"] == "event"
                    or st["corked"] or index in self._ducked or not volumes):
                continue
            lowered = [max(0, int(v * DUCK_TO)) for v in volumes]
            # Saved first: stopped right after the change, the next start
            # still puts it back. One saved but never lowered is left alone;
            # one that couldn't be saved isn't lowered.
            self._ducked[index] = (volumes, lowered, st["app"])
            if not self._save():
                del self._ducked[index]
            elif not _set_volume(index, lowered, self._run) and self._unchanged(index, volumes):
                del self._ducked[index]
        if self._ducked:
            logger.info(f"Turned {len(self._ducked)} other sound(s) down while dictating")
        self._save()

    def _unchanged(self, index, volumes) -> bool:
        """Whether stream *index* is still at *volumes* after a change that
        failed: one that timed out may have gone through all the same, and
        its record must stay until it's put back."""
        return any(st["index"] == index and st["volumes"] == volumes
                   for st in list_streams(self._run))

    def _restore_now(self):
        ducked, self._ducked = self._ducked, {}
        if not ducked and not self._owed:
            return
        streams = list_streams(self._run)
        now = {st["index"]: st["volumes"] for st in streams}
        put_back = 0
        reset = {}
        for index, (original, lowered, app) in ducked.items():
            put_back += self._put_back(index, original, lowered, app, now, reset)
        if reset:
            # The memory is right again, but the app may have opened its next
            # sound before that, at the lowered level: a browser resuming a
            # video it was paused on. Look again, after the reset.
            streams = list_streams(self._run)
            for app, entry in reset.items():
                self._owed.setdefault(app, entry)
        put_back += self._settle_owed(streams)
        for app in reset:
            self._owed.pop(app, None)   # nothing at the lowered level: settled
        if ducked:
            logger.info(f"Put {put_back} sound(s) back to their volume")
        self._save()
        if self._owed:
            self._follow_up(self._round, 0)

    def _put_back(self, index, original, lowered, app, now, reset) -> int:
        """Stream *index*, turned down from *original* to *lowered*, back at
        its volume: 1 when that's done now. *now* is each stream's volume;
        *reset* gets an ended stream whose app's memory was put right."""
        entry = {"original": original, "lowered": lowered}
        if index not in now:
            # Ended while turned down: the desktop would keep it that quiet
            # for the app's next sound. Put that right now, or else owe it
            # until the app is seen again.
            if app and self._reset(app, original):
                reset[app] = entry
            elif app:
                self._owed[app] = entry
            return 0
        if now[index] != lowered:
            return 0        # the user set its volume themselves meanwhile
        if _set_volume(index, original, self._run):
            return 1
        if app:
            # Owed, so it's tried again: the desktop would keep it quiet.
            self._owed[app] = entry
        return 0

    def _follow_up(self, round_, step):
        """Look again for apps owed their volume, at FOLLOW_UP_S, until none
        is owed or a new dictation starts (which settles them itself)."""
        if step >= len(FOLLOW_UP_S):
            return
        wait = FOLLOW_UP_S[step] - (FOLLOW_UP_S[step - 1] if step else 0)

        def due():
            self._submit(lambda: self._settle_later(round_, step))
        timer = threading.Timer(wait, due)
        timer.daemon = True
        timer.start()

    def _settle_later(self, round_, step):
        if round_ != self._round or self._ducked or not self._owed:
            return
        self._settle_owed(list_streams(self._run))
        self._save()
        if self._owed:
            self._follow_up(round_, step + 1)

    def _settle_owed(self, streams):
        """Put back an app owed its volume, playing now at the lowered level;
        its streams are then counted at their real volume. How many."""
        fixed = 0
        for st in streams:
            owed = self._owed.get(st["app"])
            if owed is None or st["index"] in self._ducked:
                continue
            if st["volumes"] == owed["lowered"]:
                if not _set_volume(st["index"], owed["original"], self._run):
                    continue    # still owed: the next look tries again
                st["volumes"] = list(owed["original"])
                fixed += 1
            # Put back, or at a level the user set: it's settled.
            del self._owed[st["app"]]
        if fixed:
            logger.info(f"Put {fixed} app(s) back to the volume a dictation turned down")
        return fixed

    def _save(self) -> bool:
        """Write down what's turned down and owed, in one go (a stop halfway
        through a write would lose the record of the streams before); False
        when it couldn't be written."""
        if not self._ducked and not self._owed:
            self._clear()
            return True
        if self._state_path is None:
            return False
        from .config import write_text_atomic
        data = {str(i): {"original": o, "lowered": low, "app": app}
                for i, (o, low, app) in self._ducked.items()}
        data["owed"] = self._owed
        try:
            write_text_atomic(self._state_path, json.dumps(data), keep_backup=False)
            return True
        except OSError as e:
            logger.warning(f"Audio duck: could not save {self._state_path}: {e}")
            return False

    def _load_owed(self):
        try:
            with open(self._state_path, encoding="utf-8") as f:
                owed = json.load(f).get("owed", {})
            return {str(app): {"original": [int(v) for v in e["original"]],
                               "lowered": [int(v) for v in e["lowered"]]}
                    for app, e in owed.items()}
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            return {}

    def _clear(self):
        if self._state_path is None:
            return
        try:
            os.remove(self._state_path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.debug(f"Audio duck: could not remove {self._state_path}: {e}")


def restore_left_over(run=subprocess.run, state_path=None, reset=None):
    """Put back volumes a service that stopped mid-dictation left lowered."""
    path = state_path or _state_path()
    if path is None:
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return 0
    except (OSError, ValueError):
        data = {}
    ducker = AudioDucker(run=run, state_path=path, reset=reset)
    for index, entry in data.items():
        if index == "owed":
            continue
        try:
            ducker._ducked[int(index)] = ([int(v) for v in entry["original"]],
                                          [int(v) for v in entry["lowered"]],
                                          str(entry.get("app", "")))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    if ducker._ducked:
        logger.info("Putting back sounds a stopped dictation left turned down")
    count = len(ducker._ducked)
    ducker._restore_now()
    # Apps owed their volume from before: put the desktop's memory right now
    # rather than wait for them to play again.
    for app, owed in list(ducker._owed.items()):
        if ducker._reset(app, owed["original"]):
            del ducker._owed[app]
    ducker._save()
    return count
