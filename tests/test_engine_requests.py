"""Requests to TuxTalk's own engines never go through a proxy.

The engines listen on 127.0.0.1, but Python's urllib sends even that through
a proxy set in http_proxy (common at work): dictated text, audio and the AI
engine's key would reach the proxy, and dictation would fail where it can't
reach back.
"""
import http.server
import threading

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
