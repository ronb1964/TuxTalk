"""GDK_BACKEND=x11 must be scoped to the dictation service, never set globally.

TuxTalk forces the X11 backend so the recording indicator can place itself:
gtk_window_move() is honoured under XWayland and ignored under native Wayland,
which centres the window instead. That was measured on a real GNOME/Wayland
session — top-left was honoured as (20, 20) under x11 and ignored under wayland.

The cost of setting it globally is severe: under XWayland, GTK3 combo popups
dismiss on button-release, so EVERY dropdown in the app is unusable. The user
cannot pick a model in the welcome dialog, or change model, device or hotkey in
Preferences. A plain GtkComboBox containing no TuxTalk code behaves the same
under x11 and works natively.

The split works because the two live in different processes: the tray owns the
welcome dialog and spawns Preferences (dropdowns, needs native Wayland), while
the dictation service owns the recording indicator (needs XWayland) and shows
no dropdowns at all — only progress and notice dialogs.
"""

import pathlib
import re
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Launchers that start the TRAY. Setting the backend here leaks it to the
# welcome dialog and to Preferences, which breaks every dropdown.
TRAY_LAUNCHERS = [
    "container-build.sh",  # writes the AppImage's AppRun
    "run-dev.sh",          # dev launcher
]


@pytest.mark.parametrize("script", TRAY_LAUNCHERS)
def test_tray_launchers_do_not_force_x11_globally(script):
    """A global export reaches the tray, so its dropdowns stop working."""
    text = (ROOT / script).read_text()
    offenders = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"^\s*export\s+GDK_BACKEND\s*=", line)
    ]
    assert not offenders, (
        f"{script} sets GDK_BACKEND globally ({offenders}). That reaches the "
        f"tray process, and under XWayland every GTK3 dropdown dismisses on "
        f"button-release — the model picker in the welcome dialog and every "
        f"combo in Preferences become unusable. Scope it to the dictation "
        f"service in tray.py's _launch_service() instead."
    )


def _load_tray_module():
    """Import tray.py, skipping if the GTK stack isn't importable."""
    try:
        from tuxtalk import tray
    except (ImportError, ValueError) as exc:  # missing gi / typelib
        pytest.skip(f"GTK stack unavailable: {exc}")
    return tray


def _capture_service_env(monkeypatch):
    """Run _launch_service with Popen stubbed, and return the env it passed."""
    tray = _load_tray_module()
    captured = {}

    class FakeProc:
        pid = 4242

    def fake_popen(argv, env=None, **kwargs):
        captured["argv"] = argv
        captured["env"] = env or {}
        return FakeProc()

    # The launching environment usually HAS GDK_BACKEND set (a terminal started
    # from a launcher that exports it, for instance). os.environ.copy() would
    # then carry it through and this test would pass without tray.py setting
    # anything. Clear it so the assertion can only be satisfied by
    # _launch_service itself.
    monkeypatch.delenv("GDK_BACKEND", raising=False)

    monkeypatch.setattr(tray.subprocess, "Popen", fake_popen)

    stub = types.SimpleNamespace(_service_pid=None)
    tray.DictationTray._launch_service(stub)
    return captured


def test_service_prefers_xwayland(monkeypatch):
    """The recording indicator cannot position itself without XWayland."""
    captured = _capture_service_env(monkeypatch)
    backends = captured["env"].get("GDK_BACKEND", "").split(",")
    assert backends and backends[0] == "x11", (
        "The dictation service must try XWayland FIRST or the recording "
        "indicator ignores indicator_position and is always centred, making "
        "that setting silently do nothing."
    )


def test_service_falls_back_when_xwayland_is_absent(monkeypatch):
    """A bare 'x11' would make the service refuse to start without XWayland.

    GDK_BACKEND accepts a comma-separated preference list. Pinning it to x11
    alone means GTK cannot initialise at all on a Wayland system that has no
    XWayland installed — the dictation service dies on launch and there is no
    dictation, to protect a cosmetic indicator position. Degrade instead.
    """
    captured = _capture_service_env(monkeypatch)
    backends = captured["env"].get("GDK_BACKEND", "").split(",")
    assert "wayland" in backends, (
        f"GDK_BACKEND={captured['env'].get('GDK_BACKEND')!r} has no fallback. "
        f"On a machine without XWayland the dictation service would fail to "
        f"start entirely instead of just centring the indicator."
    )
