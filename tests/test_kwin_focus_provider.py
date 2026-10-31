"""On KDE, KWin must report the focused window so paste can pick the right keys.

TuxTalk chooses Ctrl+Shift+V for terminals and plain Ctrl+V everywhere else, and
routes Electron apps to a typing fallback. Both decisions read one cache, which
the GNOME Shell extension fills on GNOME. Without a provider the cache stays
empty on KDE: every paste uses Ctrl+V, which a terminal treats as readline's
quoted-insert, so dictating into a terminal puts nothing on screen while
TuxTalk reports success.

The provider is a KWin script loaded over D-Bus. It connects to
workspace.windowActivated and pushes window.resourceClass into the same
SetFocusedWindow method the GNOME extension calls, so everything downstream
is unchanged. Checked by hand on a live Plasma 6.7.4 Wayland session, where the
cache follows focus from window to window.

Nothing here may be fatal. A missing KWin, a refused D-Bus call or a read-only
data directory must cost the paste hint, never the tray.
"""

import pytest

from gnome_extension_js import run_kwin_script
from tuxtalk import kwin_focus as K


class FakeBus:
    """Records D-Bus calls the provider makes, and can be told to fail."""

    def __init__(self, load_result=7, fail_on=None):
        self.calls = []
        self.load_result = load_result
        self.fail_on = fail_on or set()

    def call(self, method, *args):
        self.calls.append((method, args))
        if method in self.fail_on:
            raise RuntimeError(f"simulated D-Bus failure in {method}")
        if method == "loadScript":
            return self.load_result
        return True

    def methods(self):
        return [m for m, _ in self.calls]


@pytest.fixture
def on_kde(monkeypatch):
    monkeypatch.setattr(K.desktop_detect, "get_desktop_environment", lambda: "kde")
    monkeypatch.delenv("FLATPAK_ID", raising=False)


class TestWhenItRuns:
    def test_it_runs_on_kde(self, on_kde):
        assert K.should_provide() is True

    @pytest.mark.parametrize("desktop", ["gnome", "xfce", "cinnamon", "unknown"])
    def test_it_stays_out_of_the_way_elsewhere(self, monkeypatch, desktop):
        """On GNOME the extension fills the cache already; two would fight."""
        monkeypatch.setattr(K.desktop_detect, "get_desktop_environment",
                            lambda: desktop)
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        assert K.should_provide() is False


KWRITE = 'makeWindow("kwrite", "{1a2b}", 100, 50, 800, 600)'
KONSOLE = 'makeWindow("org.kde.konsole", "{3c4d}", 0, 0, 1920, 1080)'
SCREEN = "|DP-1|0,0,1920,1080"


def _focus_report(cls, window_id, frame):
    """The call the script makes when *cls* gets the focus."""
    from tuxtalk import dbus_service
    return [dbus_service.DBUS_NAME, dbus_service.DBUS_PATH, dbus_service.DBUS_INTERFACE,
            "SetFocusedWindow", cls, window_id, frame]


class TestTheScript:
    """The script runs under Node, against tests/kwin's stand-in workspace."""

    def test_it_reports_the_window_focused_when_it_loads(self):
        """Otherwise the hint is wrong until the user switches windows once.
        Class, which window and where go in one call: sent apart, a look
        between the calls paired one window's class with the next one's id."""
        from tuxtalk import dbus_service
        calls = run_kwin_script(K.build_script(), f"""
            workspace.activeWindow = {KWRITE};
            load();
            report(dbusCalls);
        """)
        assert calls == [_focus_report("kwrite", "{1a2b}", "100,50,800,600" + SCREEN)]
        assert hasattr(dbus_service.TuxTalkDBusService, "SetFocusedWindow")

    def test_it_follows_the_focus_from_window_to_window(self):
        """Reading only at load time would freeze on whatever was focused then."""
        calls = run_kwin_script(K.build_script(), f"""
            workspace.activeWindow = {KWRITE};
            load();
            activate({KONSOLE});
            report(dbusCalls);
        """)
        assert calls[1:] == [_focus_report("org.kde.konsole", "{3c4d}",
                                           "0,0,1920,1080" + SCREEN)]

    def test_no_window_focused_is_reported_as_none(self):
        calls = run_kwin_script(K.build_script(), """
            load();
            report(dbusCalls);
        """)
        assert calls == [_focus_report("", "", "")]


def _geometry_reports(scenario):
    """The frames the script reported for the window KWrite after loading
    with it focused and running *scenario* (`kwrite` is the window)."""
    calls = run_kwin_script(K.build_script(), f"""
        const kwrite = {KWRITE};
        workspace.activeWindow = kwrite;
        load();
        {scenario}
        report(dbusCalls);
    """)
    return [call[4] for call in calls if call[3] == "SetFocusedWindowGeometry"]


def test_window_drags_are_reported_once_they_end():
    """frameGeometryChanged fires on every frame of a drag: 60+ D-Bus calls a
    second into the tray, for a value read only when a message is shown."""
    assert _geometry_reports("""
        kwrite.move = true;
        for (const x of [110, 120, 130]) {
            kwrite.frameGeometry.x = x;
            kwrite.frameGeometryChanged.emit();
        }
        kwrite.move = false;
        kwrite.interactiveMoveResizeFinished.emit();
    """) == ["130,50,800,600" + SCREEN]


def test_a_resize_by_the_desktop_is_reported_at_once():
    """Maximizing isn't a drag: no 'finished' signal follows it."""
    assert _geometry_reports("""
        kwrite.frameGeometry = {x: 0, y: 0, width: 1920, height: 1080};
        kwrite.frameGeometryChanged.emit();
    """) == ["0,0,1920,1080" + SCREEN]


def test_a_window_that_lost_the_focus_is_no_longer_followed():
    assert _geometry_reports(f"""
        activate({KONSOLE});
        kwrite.frameGeometry.x = 300;
        kwrite.frameGeometryChanged.emit();
        kwrite.interactiveMoveResizeFinished.emit();
    """) == []


class TestStartAndStop:
    def test_starting_writes_the_script_and_loads_it(self, tmp_path, monkeypatch, on_kde):
        """A stale copy goes first: KWin keeps a script loaded across tray
        restarts, and two would double-report."""
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))
        bus = FakeBus()

        assert K.start(bus) is True
        assert (tmp_path / "focus.js").read_text() == K.build_script()
        assert bus.methods() == ["unloadScript", "unloadScript", "loadScript", "start"]

    def test_it_does_not_start_off_kde(self, tmp_path, monkeypatch):
        monkeypatch.setattr(K.desktop_detect, "get_desktop_environment",
                            lambda: "gnome")
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))
        bus = FakeBus()

        assert K.start(bus) is False
        assert bus.calls == []


class TestItIsNeverFatal:
    def test_a_kwin_that_will_not_load_is_survivable(self, tmp_path, monkeypatch, on_kde):
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))
        bus = FakeBus(fail_on={"loadScript"})

        assert K.start(bus) is False  # must not raise

    def test_a_failing_start_is_survivable(self, tmp_path, monkeypatch, on_kde):
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))
        bus = FakeBus(fail_on={"start"})

        assert K.start(bus) is False

    def test_a_refused_load_is_reported(self, tmp_path, monkeypatch, on_kde):
        """loadScript returns -1 when a script by that name is still loaded."""
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))
        bus = FakeBus(load_result=-1)

        assert K.start(bus) is False
        assert "start" not in bus.methods()

    def test_it_never_starts_a_script_by_its_number(self, tmp_path, monkeypatch, on_kde):
        """KWin numbers scripts by list length, so after an unload a new one can
        share a number with one still loaded, and run() on that number starts
        the wrong script, leaving the focus script dead and terminals showing
        "^V"."""
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))
        bus = FakeBus()
        K.start(bus)
        assert "run" not in bus.methods()

    def test_an_unwritable_script_path_is_survivable(self, monkeypatch, on_kde):
        monkeypatch.setattr(K, "script_path",
                            lambda: "/proc/cannot/write/here/focus.js")
        bus = FakeBus()

        assert K.start(bus) is False

    def test_a_failing_stop_is_survivable(self, monkeypatch, on_kde):
        bus = FakeBus(fail_on={"unloadScript"})
        K.stop(bus)  # must not raise

    def test_no_bus_at_all_is_survivable(self, tmp_path, monkeypatch, on_kde):
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))

        assert K.start(None) is False
        K.stop(None)


class TestRenameToTuxTalk:
    def test_a_talktype_focus_script_is_unloaded_too(self, tmp_path, monkeypatch, on_kde):
        """A 'talktype-focus' script left loaded by a TalkType run would report
        every focus change twice alongside ours."""
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))
        bus = FakeBus()
        K.start(bus)
        unloaded = [c[1][0] for c in bus.calls if c[0] == "unloadScript"]
        assert "talktype-focus" in unloaded and "tuxtalk-focus" in unloaded

    def test_stopping_unloads_both_names(self, on_kde):
        bus = FakeBus()
        K.stop(bus)
        unloaded = [c[1][0] for c in bus.calls if c[0] == "unloadScript"]
        assert "talktype-focus" in unloaded and "tuxtalk-focus" in unloaded


def test_a_frozen_kwin_holds_tray_start_and_quit_only_seconds(monkeypatch):
    """Without a timeout each call waits dbus-python's 25 s default, and the
    tray starts and quits through several."""
    dbus = pytest.importorskip("dbus")
    calls = []

    class Scripting:
        def __getattr__(self, method):
            return lambda *args, **kwargs: calls.append((method, kwargs))

    class Bus:
        def get_object(self, *args, **kwargs):
            # Introspecting is a call of its own, waited for with no timeout.
            assert kwargs.get("introspect") is False
            return Scripting()
    monkeypatch.setattr(dbus, "SessionBus", Bus)
    bus = K.session_bus()
    bus.call("loadScript", "/x.js", "tuxtalk")
    bus.call("start")
    assert [kwargs.get("timeout") for _method, kwargs in calls] == [3.0, 3.0]
    assert all("signature" in kwargs for _method, kwargs in calls)
