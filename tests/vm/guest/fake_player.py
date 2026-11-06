#!/usr/bin/python3
"""A pretend music player for the "pause music and videos" check: it shows
up on the session bus the way Brave or Spotify do (MPRIS), starts Playing or
Paused, and writes every call it gets (Pause, Play, Next...) to a file.

Usage: fake_player.py CALL_LOG [Playing|Paused]
"""
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

NAME = "org.mpris.MediaPlayer2.ttplayer"
XML = """<node>
  <interface name="org.mpris.MediaPlayer2">
    <property name="Identity" type="s" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Pause"/><method name="Play"/><method name="PlayPause"/>
    <method name="Next"/><method name="Previous"/>
    <property name="PlaybackStatus" type="s" access="read"/>
  </interface>
</node>"""


def main():
    log_path = sys.argv[1]
    state = {"status": sys.argv[2] if len(sys.argv) > 2 else "Playing"}
    open(log_path, "w").close()

    def on_call(_conn, _sender, _path, _iface, method, _params, invocation):
        if method == "Pause":
            state["status"] = "Paused"
        elif method == "Play":
            state["status"] = "Playing"
        elif method == "PlayPause":
            state["status"] = "Paused" if state["status"] == "Playing" else "Playing"
        with open(log_path, "a") as f:
            f.write(method + "\n")
        invocation.return_value(None)

    def on_get(_conn, _sender, _path, iface, prop):
        if prop == "Identity":
            return GLib.Variant("s", "TT Player")
        return GLib.Variant("s", state["status"])

    def on_bus(conn, _name):
        for iface in Gio.DBusNodeInfo.new_for_xml(XML).interfaces:
            conn.register_object("/org/mpris/MediaPlayer2", iface, on_call, on_get, None)

    Gio.bus_own_name(Gio.BusType.SESSION, NAME, Gio.BusNameOwnerFlags.NONE, on_bus, None, None)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
