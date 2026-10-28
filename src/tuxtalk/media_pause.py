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
in order, so a quick tap can never leave the music paused.
"""
import queue
import threading

from .logger import setup_logger

logger = setup_logger(__name__)

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
CALL_TIMEOUT_MS = 500   # per D-Bus call; a healthy player answers in a few ms


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

    def call(self, name, method):
        self._call(name, MPRIS_PATH, PLAYER_IFACE, method)


# How long exit waits for a pause or resume already queued.
EXIT_WAIT_S = 2.0


class MediaPauser:
    """Pause playing players on pause(); resume the ones we paused on resume()."""

    def __init__(self, bus=None):
        self._bus = bus or SessionBus()
        self._paused = []          # players we paused; touched only under _lock
        self._lock = threading.Lock()
        self._jobs = queue.Queue()
        self._worker = None
        self._closing = False      # exiting: a pause still queued mustn't run

    # -- called from the dictation service; never block -------------------

    def pause(self):
        """Pause every playing player, on the worker thread."""
        self._submit(self._pause_now)

    def resume(self):
        """Resume the players pause() paused, on the worker thread."""
        self._submit(self._resume_now)

    def resume_at_exit(self, timeout=EXIT_WAIT_S):
        """For exit: let a pause still queued run first (quitting right after
        a dictation started, it ran after this and the music stayed paused),
        then resume on the calling thread, as the worker may not get a turn.
        A pause the worker hasn't got to by then does nothing."""
        self._closing = True
        self.wait_idle(timeout)
        self._resume_now()

    def wait_idle(self, timeout):
        """Wait for queued jobs to finish. True if they did."""
        done = threading.Event()
        self._submit(done.set)
        return done.wait(timeout)

    # -- the worker -------------------------------------------------------

    def _submit(self, job):
        if self._worker is None:
            self._worker = threading.Thread(target=self._run, name="media-pause", daemon=True)
            self._worker.start()
        self._jobs.put(job)

    def _run(self):
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception as e:
                logger.debug(f"Media pause: {e}")

    def _status(self, name):
        try:
            return self._bus.playback_status(name)
        except Exception as e:
            logger.debug(f"Media pause: no answer from {name}: {e}")
            return None

    def _pause_now(self):
        """Pause every player that's playing, noting each, for _resume_now."""
        for name in self._bus.players():
            # Asked again after the status, which can take a while: exit
            # (resume_at_exit) may have begun meanwhile.
            if self._status(name) != "Playing" or self._closing:
                continue
            try:
                self._bus.call(name, "Pause")
            except Exception as e:
                logger.debug(f"Media pause: could not pause {name}: {e}")
                continue
            with self._lock:
                if name not in self._paused:
                    self._paused.append(name)
            logger.info(f"Paused {name[len(MPRIS_PREFIX):]} while dictating")

    def _resume_now(self):
        with self._lock:
            names, self._paused = self._paused, []
        for name in names:
            if self._status(name) != "Paused":
                continue
            try:
                self._bus.call(name, "Play")
                logger.info(f"Resumed {name[len(MPRIS_PREFIX):]}")
            except Exception as e:
                logger.debug(f"Media pause: could not resume {name}: {e}")
