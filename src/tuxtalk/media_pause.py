"""Pause music and videos while dictating (config pause_media).

Audio playing through the speakers reaches the microphone and gets mixed into
the dictation. When a recording starts, TuxTalk pauses
every media player that is playing; when it ends, it resumes only the players
it paused, and only if they are still paused (a player the user restarted or
stopped meanwhile is left alone).

Players are found through MPRIS, the D-Bus standard that browsers, Spotify,
VLC and the rest use for media keys.

pause() is called while every keyboard is grabbed, and a hung player can hold
a D-Bus call for 25 seconds, so all the D-Bus work happens on one background
thread with a short timeout per call. The thread also keeps pause and resume
in order, so a quick tap can never leave the music paused, and it alone
touches the list of paused players, the resume at exit included.

The music voice commands (music_control) run on the same thread through
run_command, so a pause, a command and a resume always happen in the order
they were asked, and a command can tell which players this recording paused.
"""
import queue
import threading

from .logger import setup_logger

logger = setup_logger(__name__)

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
CALL_TIMEOUT_MS = 500   # per D-Bus call; a healthy player answers in a few ms

# playerctld stands in for whichever player was used last and passes each call
# on to it. That player is listed too, so pausing playerctld would pause it
# behind TuxTalk's back: noted as playerctld, the real player was then never
# known to be paused for this recording.
PROXIES = frozenset({"org.mpris.MediaPlayer2.playerctld"})


class SessionBus:
    """The few MPRIS calls MediaPauser needs, over Gio (safe to use from a thread)."""

    def __init__(self):
        self._conn = None

    def _bus(self):
        if self._conn is None:
            from gi.repository import Gio
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return self._conn

    def _call(self, name, path, iface, method, args=None):
        from gi.repository import Gio
        return self._bus().call_sync(name, path, iface, method, args, None,
                                     Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None)

    def players(self):
        reply = self._call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                           "org.freedesktop.DBus", "ListNames")
        return [n for n in reply.unpack()[0] if n.startswith(MPRIS_PREFIX)]

    def playback_status(self, name):
        from gi.repository import GLib
        reply = self._call(name, MPRIS_PATH, "org.freedesktop.DBus.Properties", "Get",
                           GLib.Variant("(ss)", (PLAYER_IFACE, "PlaybackStatus")))
        return reply.unpack()[0]

    def identity(self, name):
        """The player's own name for itself ("Spotify"), "" when it doesn't say."""
        from gi.repository import GLib
        try:
            reply = self._call(name, MPRIS_PATH, "org.freedesktop.DBus.Properties", "Get",
                               GLib.Variant("(ss)", ("org.mpris.MediaPlayer2", "Identity")))
        except Exception as e:
            logger.info(f"Media: no name from {name}: {e}")
            return ""
        return reply.unpack()[0]

    def call(self, name, method):
        self._call(name, MPRIS_PATH, PLAYER_IFACE, method)


def status_of(bus, name):
    """*name*'s PlaybackStatus on *bus*, None when it doesn't answer."""
    try:
        return bus.playback_status(name)
    except Exception as e:
        logger.info(f"Media: no answer from {name}: {e}")
        return None


# How long exit waits for the worker's last resume: a pause already under
# way finishes first, and each call may take CALL_TIMEOUT_MS.
EXIT_WAIT_S = 3.0

# How long a music voice command waits for the worker: a pause queued before
# it finishes first, and each call may take CALL_TIMEOUT_MS.
COMMAND_WAIT_S = 3.0


class MediaPauser:
    """Pause playing players on pause(); resume the ones we paused on resume()."""

    def __init__(self, bus=None):
        self._bus = bus or SessionBus()
        self._paused = []          # players we paused; touched only by the worker
        self._jobs = queue.Queue()
        self._worker = None
        # A recording's start or end and a music command on the dictation
        # worker can be the first to submit: without it each could start a
        # worker, and two would no longer keep the jobs in order.
        self._start_lock = threading.Lock()
        self._closing = False      # exiting: a pause not started yet does nothing

    # -- called from the dictation service; never block -------------------

    def pause(self):
        """Pause every playing player, on the worker thread."""
        self._submit(self._pause_now)

    def resume(self):
        """Resume the players pause() paused, on the worker thread."""
        self._submit(self._resume_now)

    def close(self, timeout=EXIT_WAIT_S):
        """For exit: resume what was paused, as the worker's last job. A pause
        already under way finishes first and is undone by it; one not started
        yet does nothing. Resumed on the worker, never here: a Pause the worker
        is inside would otherwise be noted after this resumed, and the music
        stayed paused after TuxTalk. Past *timeout* the exit goes on without."""
        self._closing = True
        done = threading.Event()

        def last_job():
            try:
                self._resume_now()
            finally:
                done.set()
        self._submit(last_job)
        if not done.wait(timeout):
            logger.warning("Media pause: a player didn't answer in time at exit; "
                           "it may stay paused")

    def run_command(self, act, timeout=COMMAND_WAIT_S):
        """Run act(bus, paused, go) on the worker, after any pause or resume
        asked for before it, and return its result. *paused* holds the
        players this recording paused; act returns (result, forget), and the
        players in *forget* aren't resumed (the user paused them by voice).

        act calls go() right before it calls the players, and calls none
        when it says False: the caller gave up waiting and has told the user
        so, and a command acting later ("next track" said twice skipping two
        tracks) would be worse than none. A job given up before the worker
        reached it isn't run at all. None past *timeout*; once go() has said
        True, the calls (each bounded by CALL_TIMEOUT_MS) are waited for up
        to one more *timeout*, since they are being done."""
        lock = threading.Lock()
        state = {"gave_up": False, "going": False}
        answer, done = [], threading.Event()

        def go():
            with lock:
                state["going"] = not state["gave_up"]
                return state["going"]

        def job():
            try:
                with lock:
                    if state["gave_up"]:
                        return
                result, forget = act(self._bus, tuple(self._paused), go)
                answer.append(result)
                if state["going"]:
                    self._paused = [n for n in self._paused if n not in forget]
            finally:
                done.set()
        self._submit(job)
        if done.wait(timeout):
            return answer[0] if answer else None
        with lock:
            state["gave_up"] = not state["going"]
        if state["gave_up"] or not done.wait(timeout):
            logger.warning("Media command: the players didn't answer in time")
            return None
        return answer[0] if answer else None

    def wait_idle(self, timeout):
        """Wait for queued jobs to finish. True if they did. Only the tests
        need it: they wait on the worker without reaching into its queue."""
        done = threading.Event()
        self._submit(done.set)
        return done.wait(timeout)

    # -- the worker -------------------------------------------------------

    def _submit(self, job):
        with self._start_lock:
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="media-pause",
                                                daemon=True)
                self._worker.start()
        self._jobs.put(job)

    def _run(self):
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception as e:      # one failed job mustn't end the worker
                logger.info(f"Couldn't pause, resume or control other media: {e}")

    def _pause_now(self):
        """Pause every player that's playing, noting each, for _resume_now."""
        if self._closing:
            return
        for name in self._bus.players():
            if name in PROXIES or status_of(self._bus, name) != "Playing":
                continue
            try:
                self._bus.call(name, "Pause")
            except Exception as e:
                logger.info(f"Media pause: could not pause {name}: {e}")
                continue
            if name not in self._paused:
                self._paused.append(name)
            logger.info(f"Paused {name[len(MPRIS_PREFIX):]} while dictating")

    def _resume_now(self):
        names, self._paused = self._paused, []
        for name in names:
            if status_of(self._bus, name) != "Paused":
                continue
            try:
                self._bus.call(name, "Play")
                logger.info(f"Resumed {name[len(MPRIS_PREFIX):]}")
            except Exception as e:
                logger.info(f"Media pause: could not resume {name}: {e}")
