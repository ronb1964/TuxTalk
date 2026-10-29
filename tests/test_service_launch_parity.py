"""The dictation service must be launched identically from everywhere.

Two places start it: the tray (at startup, and on "Restart Service") and
Preferences (on Apply/OK). Both go through launch_dictation_service(), so there
is one place that knows how to start it.

Separate spawn code drifts apart. GDK_BACKEND is scoped to the service's spawn
so that dropdowns work on Wayland; a service started without it runs on native
Wayland, where gtk_window_move() is ignored, and the recording indicator snaps
to centre no matter what indicator_position says. The symptom then points at
the wrong subsystem: "changing the indicator position does nothing unless I
quit and restart the app".
"""


def test_service_launcher_module_exists():
    from tuxtalk import service_launcher

    assert hasattr(service_launcher, "launch_dictation_service"), (
        "service_launcher must expose launch_dictation_service()"
    )


def test_launcher_requests_xwayland_first_with_a_fallback():
    """XWayland for indicator positioning; fallback so it still starts without it."""
    from tuxtalk.service_launcher import build_service_env

    env = build_service_env({})
    backends = env.get("GDK_BACKEND", "").split(",")

    assert backends and backends[0] == "x11", (
        "The service must prefer XWayland or gtk_window_move() is ignored and "
        "indicator_position silently does nothing."
    )
    assert "wayland" in backends, (
        "Without a fallback the service cannot start at all on a machine with "
        "no XWayland — no dictation, to protect a cosmetic indicator position."
    )


def test_launcher_does_not_inherit_a_stale_backend(monkeypatch):
    """Preferences runs on native Wayland; its env must not leak into the service."""
    from tuxtalk.service_launcher import build_service_env

    env = build_service_env({"GDK_BACKEND": "wayland"})
    assert env["GDK_BACKEND"].split(",")[0] == "x11", (
        "build_service_env must override an inherited GDK_BACKEND, not defer to "
        "it — Preferences now runs natively on Wayland and would otherwise hand "
        "the service the wrong backend."
    )


def test_the_tray_and_preferences_start_the_service_the_same_way(monkeypatch, tmp_path):
    """A second hand-rolled spawn would let the two paths drift apart."""
    import types
    from tuxtalk import prefs, service_launcher, tray
    started = []
    monkeypatch.delenv("GDK_BACKEND", raising=False)
    class StillRunning:
        pid = 1

        def wait(self, timeout):
            raise service_launcher.subprocess.TimeoutExpired("tuxtalk.app", timeout)
    monkeypatch.setattr(service_launcher.subprocess, "Popen", lambda argv, env=None, **k:
                        started.append((argv, (env or {}).get("GDK_BACKEND"))) or StillRunning())
    monkeypatch.setattr(service_launcher, "end_dictation_service", lambda: True)
    monkeypatch.setattr("tuxtalk.single_instance.runtime_dir", lambda: str(tmp_path))
    tray.DictationTray._launch_service(types.SimpleNamespace())
    prefs._restart_without_tray()           # Preferences with no tray running
    assert len(started) == 2 and started[0] == started[1]
    argv, backend = started[0]
    assert argv[1:] == ["-m", "tuxtalk.app"] and backend.startswith("x11")
