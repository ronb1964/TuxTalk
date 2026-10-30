"""Asking a compositor a question over its local socket: Sway's and i3's IPC,
Hyprland's command socket and niri's. compositor_focus asks them which
window has focus and where it is, keyboard_layout which layout is on.

Each question is one short connection with a short timeout: Hyprland serves
its socket synchronously and freezes the desktop while a connection stays
open, and these answer in about a millisecond anyway.
"""
import json
import os
import socket
import struct

from .single_instance import session_runtime_dir

TIMEOUT = 0.25      # seconds

_I3_MAGIC = b"i3-ipc"
I3_GET_TREE = 4
I3_GET_INPUTS = 100


def exchange(path, request, read_reply):
    """Connect to *path*, send *request*, return read_reply(sock); always closes."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(TIMEOUT)
        sock.connect(path)
        sock.sendall(request)
        return read_reply(sock)


def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("the compositor hung up mid-reply")
        data += chunk
    return data


def _recv_until_closed(sock):
    chunks = []
    while chunk := sock.recv(65536):
        chunks.append(chunk)
    return b"".join(chunks)


def recv_line(sock):
    """One line of reply, newline included; less if the compositor hangs up."""
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    return data


def _i3_reply(sock):
    head = _recv_exact(sock, 14)
    if head[:6] != _I3_MAGIC:
        raise ValueError("not an i3 IPC reply")
    length, _kind = struct.unpack("<II", head[6:])
    return json.loads(_recv_exact(sock, length))


def i3_request(path, kind):
    """Sway's or i3's answer to message *kind* (I3_GET_TREE...) on the IPC
    socket at *path*, decoded from its JSON."""
    return exchange(path, _I3_MAGIC + struct.pack("<II", 0, kind), _i3_reply)


def hyprland_socket(signature):
    """The command socket of the Hyprland session *signature*."""
    return os.path.join(session_runtime_dir(), "hypr", signature, ".socket.sock")


def hyprland_request(signature, request):
    """Hyprland's JSON answer to *request* (b"j/activewindow", as hyprctl
    sends it); Hyprland replies and hangs up. {} for an empty reply."""
    return json.loads(exchange(hyprland_socket(signature), request, _recv_until_closed)
                      or b"{}")
