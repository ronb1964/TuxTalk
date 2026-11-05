#!/usr/bin/python3
"""Listen to the hotkey helper as the session user, for the VM release tests.

Says HELLO 1, then WATCH with the given specs, and writes every line the
helper sends back to OUTPUT_FILE, one per line, flushed at once, until it is
killed. What the helper reports is then exactly what the file holds. Writes
nothing when no helper answers. Standard library only.

Usage: helper_listen.py OUTPUT_FILE SPEC [SPEC...]    (SPEC as in WATCH: "L:57", "66")
"""
import socket
import sys

SOCKET_PATH = "/run/tuxtalk-hotkeys/hotkeys.sock"


def main():
    out_path, specs = sys.argv[1], sys.argv[2:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(SOCKET_PATH)
    sock.sendall(b"HELLO 1\n")
    # The WATCH waits for the hello, so the answers come in order.
    sock.sendall(("WATCH " + " ".join(specs) + "\n").encode())
    with open(out_path, "w", encoding="utf-8") as out:
        for line in sock.makefile("r", encoding="utf-8"):
            out.write(line.rstrip("\n") + "\n")
            out.flush()


if __name__ == "__main__":
    main()
