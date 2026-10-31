"""Asking Sway, i3, Hyprland, niri and COSMIC which window has focus.

Each test runs a stand-in compositor on a real Unix socket that speaks that
compositor's real protocol, so the framing, the parsing and the clean-up are
all exercised. The real compositors were checked by hand (headless, in a
container; COSMIC in the Pop!_OS test VM).
"""
import json
import socket
import struct
import threading

import pytest

from tuxtalk import compositor_focus as cf


def _serve(path, handler):
    """Answer one connection on *path* with *handler(conn)*, in the background."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    def run():
        conn, _ = server.accept()
        with conn:
            handler(conn)
        server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _clear_env(monkeypatch):
    for var in ("SWAYSOCK", "I3SOCK", "HYPRLAND_INSTANCE_SIGNATURE", "NIRI_SOCKET",
                "XDG_SESSION_TYPE", "DISPLAY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP",
                "DESKTOP_SESSION"):
        monkeypatch.delenv(var, raising=False)


# --- Sway and i3 -------------------------------------------------------------------

# Trimmed from a real `swaymsg -t get_tree`: the focused window can sit deep in
# the tree, in floating_nodes, and an XWayland window has no app_id.
def _tree(focused):
    foot = {"type": "con", "id": 7, "app_id": "foot", "focused": focused == "foot",
            "nodes": [], "floating_nodes": []}
    xterm = {"type": "con", "app_id": None, "focused": focused == "xterm",
             "window_properties": {"class": "XTerm", "instance": "xterm"},
             "nodes": [], "floating_nodes": []}
    workspace = {"type": "workspace", "focused": focused == "workspace",
                 "nodes": [{"type": "con", "focused": False, "nodes": [foot],
                            "floating_nodes": []}],
                 "floating_nodes": [xterm]}
    return {"type": "root", "focused": False, "nodes": [
        {"type": "output", "focused": False, "nodes": [workspace], "floating_nodes": []}]}


def _i3_server(tree):
    def handler(conn):
        head = conn.recv(14)
        assert head[:6] == b"i3-ipc"
        length, kind = struct.unpack("<II", head[6:])
        assert kind == 4                                   # GET_TREE
        body = json.dumps(tree).encode()
        conn.sendall(b"i3-ipc" + struct.pack("<II", len(body), 4) + body)
    return handler


@pytest.mark.parametrize("focused, expected", [
    ("foot", "foot"),                   # native Wayland: app_id
    ("xterm", "XTerm"),                 # XWayland: the X11 class
    ("workspace", None),                # an empty workspace has focus, no window
])
def test_sway_reports_the_focused_window(tmp_path, monkeypatch, focused, expected):
    _clear_env(monkeypatch)
    path = tmp_path / "sway.sock"
    done = _serve(path, _i3_server(_tree(focused)))
    monkeypatch.setenv("SWAYSOCK", str(path))
    assert cf.focused_window()[0] == expected
    done.join(1)


def test_sway_names_which_window_has_focus(tmp_path, monkeypatch):
    """Its con id tells two windows of one app apart, and a re-tile keeps it."""
    _clear_env(monkeypatch)
    path = tmp_path / "sway.sock"
    done = _serve(path, _i3_server(_tree("foot")))
    monkeypatch.setenv("SWAYSOCK", str(path))
    assert cf.focused_window() == ("foot", 7)
    done.join(1)


def test_i3_uses_the_same_protocol(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    path = tmp_path / "i3.sock"
    done = _serve(path, _i3_server(_tree("foot")))
    monkeypatch.setenv("I3SOCK", str(path))
    assert cf.focused_window()[0] == "foot"
    done.join(1)


# --- Hyprland ----------------------------------------------------------------------

def test_hyprland_reports_the_focused_window_and_hangs_up(tmp_path, monkeypatch):
    """Hyprland answers its socket synchronously and freezes the desktop while
    a connection stays open, so the request must be one write and the
    connection closed straight after the reply."""
    _clear_env(monkeypatch)
    signature = "abc123_1700000000_42"
    folder = tmp_path / "hypr" / signature
    folder.mkdir(parents=True)
    got = []

    def handler(conn):
        got.append(conn.recv(100))
        conn.sendall(json.dumps({"class": "com.mitchellh.ghostty",
                                 "initialClass": "com.mitchellh.ghostty",
                                 "address": "0x5f1e2a40"}).encode())

    done = _serve(folder / ".socket.sock", handler)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", signature)
    assert cf.focused_window() == ("com.mitchellh.ghostty", "0x5f1e2a40")
    assert got == [b"j/activewindow"]
    done.join(1)


def test_hyprland_with_nothing_focused(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    folder = tmp_path / "hypr" / "sig"
    folder.mkdir(parents=True)
    done = _serve(folder / ".socket.sock", lambda conn: (conn.recv(100), conn.sendall(b"{}")))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "sig")
    assert cf.focused_window()[0] is None
    done.join(1)


# --- niri --------------------------------------------------------------------------

def test_niri_reports_the_focused_window(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    got = []

    def handler(conn):
        got.append(conn.recv(100))
        conn.sendall(b'{"Ok":{"FocusedWindow":{"id":12,"title":"~","app_id":"Alacritty",'
                     b'"workspace_id":6,"is_focused":true}}}\n')

    path = tmp_path / "niri.sock"
    done = _serve(path, handler)
    monkeypatch.setenv("NIRI_SOCKET", str(path))
    assert cf.focused_window() == ("Alacritty", 12)
    assert got == [b'"FocusedWindow"\n']
    done.join(1)


def test_niri_with_nothing_focused(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    path = tmp_path / "niri.sock"
    done = _serve(path, lambda conn: (conn.recv(100),
                                      conn.sendall(b'{"Ok":{"FocusedWindow":null}}\n')))
    monkeypatch.setenv("NIRI_SOCKET", str(path))
    assert cf.focused_window()[0] is None
    done.join(1)


# --- never in the way --------------------------------------------------------------

def test_no_compositor_socket_means_unknown(monkeypatch):
    _clear_env(monkeypatch)
    assert cf.focused_window()[0] is None


def test_a_dead_socket_means_unknown(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SWAYSOCK", str(tmp_path / "gone.sock"))
    assert cf.focused_window()[0] is None


def test_a_compositor_that_never_answers_is_given_up_on_quickly(tmp_path, monkeypatch):
    import time
    _clear_env(monkeypatch)
    path = tmp_path / "stuck.sock"
    release = threading.Event()
    done = _serve(path, lambda conn: release.wait(5))
    monkeypatch.setenv("SWAYSOCK", str(path))
    started = time.monotonic()
    assert cf.focused_window()[0] is None
    assert time.monotonic() - started < 1.0
    release.set()
    done.join(1)


# --- where the dictation service uses it ---------------------------------------------

def test_the_service_asks_the_compositor_when_nobody_reported_focus(monkeypatch):
    from tuxtalk import focused_window
    monkeypatch.setattr(focused_window, "reported", lambda: None)
    monkeypatch.setattr(cf, "focused_window", lambda: ("foot", None))
    assert focused_window.window_class() == "foot"


def test_a_reported_window_wins_over_asking_the_compositor(monkeypatch):
    from tuxtalk import focused_window
    monkeypatch.setattr(focused_window, "reported", lambda: ("org.kde.konsole", "", ""))
    monkeypatch.setattr(cf, "focused_window", lambda: ("Xwayland", 0x400001))
    assert focused_window.window_class() == "org.kde.konsole"


@pytest.mark.parametrize("app_id", [
    "com.mitchellh.ghostty", "org.gnome.Console", "xfce4-terminal",
    "com.system76.CosmicTerm", "Kitty", "foot",
])
def test_terminals_common_on_tiling_desktops_get_terminal_paste(app_id):
    from tuxtalk.window_classes import is_terminal_class
    assert is_terminal_class(app_id)


# --- plain X11 desktops (XFCE, MATE, Cinnamon, LXQt, Openbox...) ---------------------

@pytest.mark.parametrize("raw, expected", [
    (b"xfce4-terminal\x00Xfce4-terminal\x00", "Xfce4-terminal"),   # instance, class
    (b"xterm\x00XTerm\x00", "XTerm"),
    (b"lonely\x00", "lonely"),                                     # class missing
    (b"", None),
])
def test_wm_class_gives_the_class_part(raw, expected):
    assert cf.parse_wm_class(raw) == expected


def test_an_x11_session_asks_the_x_server(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(cf, "_from_x11", lambda: ("Xfce4-terminal", 0x1e00007))
    assert cf.focused_window()[0] == "Xfce4-terminal"
    assert cf.focused_window() == ("Xfce4-terminal", 0x1e00007)


def test_a_wayland_session_never_asks_xwayland(monkeypatch):
    """The dictation service has DISPLAY even on Wayland (XWayland, for the
    recording indicator), but XWayland only knows the X11 apps, so its
    answer could name a window that doesn't have focus."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(cf, "_from_x11", lambda: pytest.fail("asked XWayland"))
    assert cf.focused_window()[0] is None


def test_without_a_session_type_x11_is_used_only_with_no_wayland(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(cf, "_from_x11", lambda: pytest.fail("asked XWayland"))
    assert cf.focused_window()[0] is None
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.setattr(cf, "_from_x11", lambda: ("XTerm", 0x2a00003))
    assert cf.focused_window()[0] == "XTerm"


def test_i3_on_x11_uses_its_own_socket_first(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    path = tmp_path / "i3.sock"
    done = _serve(path, _i3_server(_tree("foot")))
    monkeypatch.setenv("I3SOCK", str(path))
    monkeypatch.setattr(cf, "_from_x11", lambda: pytest.fail("asked X11 first"))
    assert cf.focused_window()[0] == "foot"
    done.join(1)


def test_an_x_server_problem_means_unknown(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":99")

    def broken():
        raise OSError("cannot connect")

    monkeypatch.setattr(cf, "_from_x11", broken)
    assert cf.focused_window()[0] is None


def test_no_x_server_at_all_is_unknown_not_an_error(monkeypatch):
    """The real XCB code, pointed at a display that doesn't exist."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":987")
    assert cf.focused_window()[0] is None


@pytest.mark.parametrize("wm_class", ["Guake", "Tilda", "Roxterm", "cool-retro-term", "Mate-terminal"])
def test_x11_terminals_get_terminal_paste(wm_class):
    from tuxtalk.window_classes import is_terminal_class
    assert is_terminal_class(wm_class)


# --- COSMIC ------------------------------------------------------------------------

def _wl_message(obj, opcode, payload=b""):
    return struct.pack("<IHH", obj, opcode, 8 + len(payload)) + payload


def _wl_str(text):
    raw = text.encode() + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


class FakeCosmic:
    """cosmic-comp's side of the window list, as the Pop!_OS VM showed it:
    each window and its app_id on binding the ext list, and the states only
    a moment after they're asked for, closed by zcosmic_toplevel_info's done."""

    def __init__(self, path, windows, active=None, info_version=3):
        self.windows = list(windows)            # app_ids, in the order they opened
        self.handles = {}                       # app_id -> (ext handle, cosmic handle)
        self.active = active
        self.info_version = info_version
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(1)
        self.conn = None
        self.lock = threading.Lock()
        threading.Thread(target=self._serve, daemon=True).start()

    def _send(self, data):
        with self.lock:
            if self.conn is not None:       # a late send_states after stop()
                self.conn.sendall(data)

    def _serve(self):
        self.conn, _ = self.server.accept()
        self.server.close()
        globals_ = [(1, "ext_foreign_toplevel_list_v1", 1)]
        if self.info_version:
            globals_.append((2, "zcosmic_toplevel_info_v1", self.info_version))
        bound, unread = {}, b""
        while True:
            try:
                chunk = self.conn.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            unread += chunk
            while len(unread) >= 8 and len(unread) >= struct.unpack_from("<H", unread, 6)[0]:
                obj, opcode, size = struct.unpack_from("<IHH", unread)
                body, unread = unread[8:size], unread[size:]
                if (obj, opcode) == (1, 1):                       # get_registry
                    registry = struct.unpack_from("<I", body)[0]
                    for name, interface, version in globals_:
                        self._send(_wl_message(registry, 0, struct.pack("<I", name)
                                               + _wl_str(interface) + struct.pack("<I", version)))
                elif (obj, opcode) == (1, 0):                     # sync
                    self._send(_wl_message(struct.unpack_from("<I", body)[0], 0, b"\0" * 4))
                elif obj == registry and opcode == 0:             # bind
                    name = struct.unpack_from("<I", body)[0]
                    new = struct.unpack_from("<I", body, len(body) - 4)[0]
                    bound[name] = new
                    if name == 1:
                        for i, app_id in enumerate(self.windows):
                            self._open(new, 0xff000000 + i, app_id)
                elif obj == bound.get(2) and opcode == 1:         # get_cosmic_toplevel
                    cosmic, ext = struct.unpack_from("<II", body)
                    app_id = next(a for a, (e, _c) in self.handles.items() if e == ext)
                    self.handles[app_id] = (ext, cosmic)
                    self.info = obj
                    threading.Timer(0.05, self.send_states).start()

    def _open(self, toplevel_list, ext, app_id):
        self.handles[app_id] = (ext, None)
        self._send(_wl_message(toplevel_list, 0, struct.pack("<I", ext))
                   + _wl_message(ext, 3, _wl_str(app_id)) + _wl_message(ext, 1))

    def send_states(self):
        for app_id, (_ext, cosmic) in list(self.handles.items()):
            if cosmic is not None:
                states = [0, 2] if app_id == self.active else [0]   # maximized, activated
                self._send(_wl_message(cosmic, 8, struct.pack("<I", 4 * len(states))
                                       + struct.pack(f"<{len(states)}I", *states)))
        self._send(_wl_message(self.info, 2))

    def focus(self, app_id):
        self.active = app_id
        self.send_states()

    def close_window(self, app_id):
        ext, _cosmic = self.handles.pop(app_id)
        self._send(_wl_message(ext, 0))

    def stop(self):
        with self.lock:
            if self.conn is not None:
                self.conn.shutdown(socket.SHUT_RDWR)
                self.conn.close()
                self.conn = None


@pytest.fixture
def cosmic(tmp_path, monkeypatch):
    """A COSMIC session whose compositor is started by calling the fixture."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(cf, "_cosmic", None)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "COSMIC")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    started = []

    def start(*windows, **kw):
        started.append(FakeCosmic(tmp_path / "wayland-1", windows, **kw))
        return started[-1]

    yield start
    for compositor in started:
        compositor.stop()


def _wait_for(expected):
    import time
    deadline = time.monotonic() + 2
    while cf.focused_window()[0] != expected and time.monotonic() < deadline:
        time.sleep(0.01)
    return cf.focused_window()[0]


def test_cosmic_names_the_activated_window(cosmic):
    cosmic("org.mozilla.firefox", "gnome-terminal-server", active="gnome-terminal-server")
    assert cf.focused_window()[0] == "gnome-terminal-server"


def test_cosmic_answers_at_once_after_the_first_question(cosmic):
    """cosmic-comp sends states late, so the connection stays open: a paste
    mustn't wait for them every time."""
    import time
    cosmic("org.gnome.TextEditor", active="org.gnome.TextEditor")
    cf.focused_window()[0]
    started = time.monotonic()
    assert cf.focused_window()[0] == "org.gnome.TextEditor"
    # FakeCosmic takes one connection only, so asking anew fails the answer;
    # waiting for the states again would cost up to COSMIC_FIRST_ANSWER.
    assert time.monotonic() - started < cf.COSMIC_FIRST_ANSWER / 2


def test_cosmic_follows_a_switch_to_another_window(cosmic):
    """What the send countdown needs: switching windows during the wait."""
    compositor = cosmic("org.gnome.TextEditor", "gnome-terminal-server",
                        active="org.gnome.TextEditor")
    assert cf.focused_window()[0] == "org.gnome.TextEditor"
    compositor.focus("gnome-terminal-server")
    assert _wait_for("gnome-terminal-server") == "gnome-terminal-server"


def test_cosmic_with_the_focused_window_closed_is_unknown(cosmic):
    compositor = cosmic("org.gnome.TextEditor", "gnome-terminal-server",
                        active="gnome-terminal-server")
    assert cf.focused_window()[0] == "gnome-terminal-server"
    compositor.close_window("gnome-terminal-server")
    assert _wait_for(None) is None


def test_cosmic_with_no_windows_is_unknown_without_waiting(cosmic):
    import time
    cosmic()
    started = time.monotonic()
    assert cf.focused_window()[0] is None
    assert time.monotonic() - started < 0.5


def test_a_cosmic_without_window_states_is_unknown(cosmic):
    """Before zcosmic_toplevel_info version 2 there was no ext list to pair
    it with; nothing to ask, so no answer, quickly."""
    import time
    cosmic("org.gnome.TextEditor", active="org.gnome.TextEditor", info_version=1)
    started = time.monotonic()
    assert cf.focused_window()[0] is None
    assert time.monotonic() - started < 0.5


def test_a_cosmic_without_window_states_is_asked_only_once(cosmic):
    """Every paste asks which window has focus; a cosmic-comp that can't say
    isn't connected to again each time."""
    cosmic("org.gnome.TextEditor", active="org.gnome.TextEditor", info_version=1)
    assert cf.focused_window()[0] is None
    asked = cf._cosmic
    asked.join(1)
    assert cf.focused_window()[0] is None
    assert cf._cosmic is asked


def test_a_cosmic_that_never_sends_the_states_costs_one_wait_only(cosmic, monkeypatch):
    import time
    monkeypatch.setattr(cf, "COSMIC_FIRST_ANSWER", 0.4)
    monkeypatch.setattr(FakeCosmic, "send_states", lambda self: None)
    cosmic("org.gnome.TextEditor", active="org.gnome.TextEditor")
    assert cf.focused_window()[0] is None
    started = time.monotonic()
    assert cf.focused_window()[0] is None
    assert time.monotonic() - started < 0.2


def test_cosmic_is_asked_again_after_the_compositor_restarts(cosmic, tmp_path):
    first = cosmic("org.gnome.TextEditor", active="org.gnome.TextEditor")
    assert cf.focused_window()[0] == "org.gnome.TextEditor"
    first.stop()
    (tmp_path / "wayland-1").unlink()
    assert _wait_for(None) is None
    cosmic("gnome-terminal-server", active="gnome-terminal-server")
    assert _wait_for("gnome-terminal-server") == "gnome-terminal-server"


def test_a_malformed_message_ends_the_connection_instead_of_spinning():
    """A message claiming zero bytes would otherwise be read again forever,
    a thread spinning at full speed."""
    ours, theirs = socket.socketpair()
    with ours, theirs:
        ours.settimeout(1)
        theirs.sendall(struct.pack("<IHH", 7, 0, 0))
        with pytest.raises(ConnectionError):
            cf._Wayland(ours).read_until(None, None, lambda *event: None)


def test_gnome_terminal_on_cosmic_gets_terminal_paste():
    """COSMIC names GNOME Terminal's windows after its server process."""
    from tuxtalk.window_classes import is_terminal_class
    assert is_terminal_class("gnome-terminal-server")


def test_a_compositor_that_stops_answering_is_logged_once_not_at_every_check(
        monkeypatch, caplog):
    """The focus is asked before every typed chunk and during a send's
    countdown: the same failure is logged the first time, and its end once."""
    import logging
    state = {"broken": True}

    def from_hyprland(_signature):
        if state["broken"]:
            raise ConnectionRefusedError("hyprland socket refused")
        return "kitty", "0xabc"
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "sig")
    monkeypatch.setattr(cf, "_from_hyprland", from_hyprland)
    with caplog.at_level(logging.INFO, logger=cf.logger.name):
        for _ in range(3):
            assert cf.focused_window() == (None, None)
        state["broken"] = False
        for _ in range(2):
            assert cf.focused_window() == ("kitty", "0xabc")
    lines = [r.getMessage() for r in caplog.records]
    assert sum("refused" in line for line in lines) == 1
    assert sum("answers again" in line for line in lines) == 1
