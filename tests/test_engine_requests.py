"""Requests to TuxTalk's own engines never go through a proxy, and go only
to the engine TuxTalk started, never to another program on its port.

The engines listen on 127.0.0.1, but Python's urllib sends even that through
a proxy set in http_proxy (common at work): dictated text, audio and the AI
engine's key would reach the proxy, and dictation would fail where it can't
reach back.
"""
import http.server
import os
import subprocess
import sys
import threading
import time

from tuxtalk import engine_process


class _Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def test_an_engine_on_this_computer_is_reached_with_a_proxy_set(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # A proxy nobody listens on: going through it would fail.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    try:
        with engine_process.open_local(f"http://127.0.0.1:{server.server_port}/health",
                                       timeout=5) as response:
            assert response.read() == b"ok"
    finally:
        server.shutdown()
        server.server_close()


class _Loading(_Health):
    def do_GET(self):
        self.send_response(503)
        self.end_headers()


def _serve(handler):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_an_engine_still_loading_its_model_is_not_ready_yet():
    """llama-server and whisper-server answer 503 while they load: waiting
    goes on instead of sending the warm-up to an engine that can't take it."""
    loading, ready = _serve(_Loading), _serve(_Health)
    try:
        assert engine_process.answers(loading.server_port, os.getpid()) is False
        assert engine_process.answers(ready.server_port, os.getpid()) is True
    finally:
        for server in (loading, ready):
            server.shutdown()
            server.server_close()


def test_an_engine_not_listening_yet_is_not_ready():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Health)
    port = server.server_port
    server.server_close()           # the port is free: nothing listens there
    assert engine_process.answers(port, os.getpid()) is False


# --- the port must be the engine's own -------------------------------------------------------

_OTHER_SERVER = """
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
    def log_message(self, *a):
        pass
server = http.server.HTTPServer(("127.0.0.1", 0), H)
print(server.server_port, flush=True)
server.serve_forever()
"""


def test_a_port_another_program_answers_on_is_not_the_engine():
    """Another user's program can take the port TuxTalk picked (it's on the
    engine's command line for everyone to read) before the engine binds it.
    Taken for the engine, it would be sent the audio and choose the text."""
    other = subprocess.Popen([sys.executable, "-I", "-c", _OTHER_SERVER],
                             stdout=subprocess.PIPE, text=True)
    try:
        port = int(other.stdout.readline())
        engine_pid = os.getpid()             # stands for the engine TuxTalk started
        assert engine_process.answers(port, engine_pid) is False
        deadline = time.time() + 5
        while not engine_process.answers(port, other.pid) and time.time() < deadline:
            time.sleep(0.05)
        assert engine_process.answers(port, other.pid) is True
    finally:
        other.kill()
        other.wait()


def _fake_proc(tmp_path, listening_inode, engine_fds):
    """A /proc with one socket listening on 127.0.0.1:8080, held by
    *listening_inode*, and engine 42 with sockets *engine_fds*."""
    (tmp_path / "net").mkdir()
    (tmp_path / "net" / "tcp").write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
        "   uid  timeout inode\n"
        f"   0: {engine_process._LOOPBACK_HEX}:1F90 00000000:0000 0A 00000000:00000000"
        f" 00:00000000 00000000  1001        0 {listening_inode} 1 0000 100 0 0 10 0\n")
    fd = tmp_path / "42" / "fd"
    fd.mkdir(parents=True)
    for n, inode in enumerate(engine_fds):
        (fd / str(n)).symlink_to(f"socket:[{inode}]")
    return str(tmp_path)


def test_the_listening_socket_is_found_among_the_engines_own(tmp_path):
    proc = _fake_proc(tmp_path, listening_inode=777, engine_fds=[555, 777])
    assert engine_process.listens_on(42, 8080, proc=proc) is True


def test_a_listening_socket_held_by_someone_else_is_not_the_engines(tmp_path):
    proc = _fake_proc(tmp_path, listening_inode=999, engine_fds=[555, 777])
    assert engine_process.listens_on(42, 8080, proc=proc) is False
    assert engine_process.listens_on(42, 8081, proc=proc) is False      # nobody on that port


def test_an_engine_whose_socket_cant_be_checked_is_not_trusted(tmp_path, caplog):
    """Without /proc/net/tcp nothing shows the answer came from the engine
    TuxTalk started, so it's not used, and the log says why once."""
    import logging
    proc = _fake_proc(tmp_path, listening_inode=777, engine_fds=[777])
    (tmp_path / "net" / "tcp").unlink()
    with caplog.at_level(logging.WARNING, logger=engine_process.logger.name):
        assert engine_process.listens_on(42, 8080, proc=proc) is False
        assert engine_process.listens_on(42, 8080, proc=proc) is False
    assert sum("isn't used" in r.getMessage() for r in caplog.records) == 1
