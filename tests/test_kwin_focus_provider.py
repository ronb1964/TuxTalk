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

import pathlib

import pytest

from tuxtalk import kwin_focus as K

EXTENSION_JS = (pathlib.Path(__file__).resolve().parent.parent / "gnome-extension"
                / "talktype@ronb1964.github.io" / "extension.js").read_text()


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
    def test_it_runs_on_kde(self, monkeypatch, on_kde):
        assert K.should_provide() is True

    @pytest.mark.parametrize("desktop", ["gnome", "xfce", "cinnamon", "unknown"])
    def test_it_stays_out_of_the_way_elsewhere(self, monkeypatch, desktop):
        monkeypatch.setattr(K.desktop_detect, "get_desktop_environment",
                            lambda: desktop)
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        assert K.should_provide() is False

    def test_gnome_keeps_its_own_provider(self, monkeypatch):
        """The extension already fills the cache; two writers would fight."""
        monkeypatch.setattr(K.desktop_detect, "get_desktop_environment",
                            lambda: "gnome")
        assert K.should_provide() is False


class TestTheScript:
    def test_it_targets_the_same_dbus_method_the_extension_uses(self):
        from tuxtalk import dbus_service

        script = K.build_script()

        assert dbus_service.DBUS_INTERFACE in script
        assert '"SetFocusedWindow"' in script
        assert "SetFocusedWindowRemote" in EXTENSION_JS

    def test_it_reports_the_window_class(self):
        script = K.build_script()
        assert "resourceClass" in script

    def test_it_follows_focus_rather_than_reading_once(self):
        """Reading only at load time would freeze on whatever was focused then."""
        script = K.build_script()
        assert "windowActivated" in script

    def test_it_reports_the_window_focused_at_load_time_too(self):
        """Otherwise the hint is wrong until the user switches windows once."""
        script = K.build_script()
        assert "activeWindow" in script


class TestStartAndStop:
    def test_starting_writes_the_script_and_loads_it(self, tmp_path, monkeypatch, on_kde):
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))
        bus = FakeBus()

        assert K.start(bus) is True
        assert (tmp_path / "focus.js").exists()
        assert bus.methods() == ["unloadScript", "unloadScript", "loadScript", "start"]

    def test_a_stale_copy_is_unloaded_before_loading(self, tmp_path, monkeypatch, on_kde):
        """KWin keeps a script loaded across tray restarts; two would double-report."""
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))
        bus = FakeBus()

        K.start(bus)

        assert bus.methods()[0] == "unloadScript"

    def test_it_does_not_start_off_kde(self, tmp_path, monkeypatch):
        monkeypatch.setattr(K.desktop_detect, "get_desktop_environment",
                            lambda: "gnome")
        monkeypatch.setattr(K, "script_path", lambda: str(tmp_path / "focus.js"))
        bus = FakeBus()

        assert K.start(bus) is False
        assert bus.calls == []

    def test_stopping_unloads_the_script(self, monkeypatch, on_kde):
        bus = FakeBus()
        K.stop(bus)
        assert "unloadScript" in bus.methods()


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


def test_window_drags_are_reported_once_they_end():
    """frameGeometryChanged fires on every frame of a drag: 60+ D-Bus calls a
    second into the tray, for a value read only when a message is shown."""
    from tuxtalk import kwin_focus

    script = kwin_focus.build_script()
    assert "w.frameGeometryChanged.connect(tuxtalkGeometryChanged)" in script
    assert "if (w && (w.move || w.resize))" in script
    assert "w.interactiveMoveResizeFinished.connect(tuxtalkGeometry)" in script
    assert "tuxtalkWatched.interactiveMoveResizeFinished.disconnect(tuxtalkGeometry)" in script


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
