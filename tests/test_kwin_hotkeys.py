"""KWin holds TuxTalk's hotkeys back from the focused app (the F8 "~" leak).

The behaviour itself was proven by hand on Plasma 6.7.4 (see the module
docstring). These pin the parts that are easy to break: which keys are claimed,
that a bare letter never is, that a changed hotkey is registered fresh, and
that nothing here can raise into the service or the tray.
"""
import types

import pytest

from tuxtalk import kwin_hotkeys as kh

# The real one, before conftest swaps it for one that refuses: the test of the
# calls it makes hands it a fake dbus.
_REAL_SESSION_BUS = kh.session_bus


def _cfg(hotkey="F8", toggle="F9", vc="Ctrl+Alt+V"):
    return types.SimpleNamespace(hotkey=hotkey, toggle_hotkey=toggle,
                                 voice_commands_hotkey=vc)


class FakeBus:
    """Records calls; keeps a set of registered tuxtalk-* names like KWin."""

    def __init__(self, existing=(), refuse=(), fail=()):
        self.calls = []
        self.names = list(existing)
        self.refuse = set(refuse)   # names KDE gives no key (taken elsewhere)
        self.fail = set(fail)       # methods that raise
        self.script = None

    def call(self, method, *args):
        self.calls.append((method, *args))
        if method in self.fail:
            raise RuntimeError(method)
        if method == "loadScript":
            with open(args[0]) as f:
                self.script = f.read()
            for line in self.script.splitlines():
                if line.startswith("registerShortcut("):
                    self.names.append(line.split('"')[1])
            return 7
        if method == "unloadScript":
            self.script = None
            return True
        if method == "shortcutNames":
            return list(self.names)
        if method == "unregister":
            self.names.remove(args[1])
            return True
        if method == "allShortcutInfos":
            return [(n, "", "kwin", "KWin", "default", "", [] if n in self.refuse else [1], [])
                    for n in self.names]
        return None


@pytest.fixture
def on_kde(monkeypatch, tmp_path):
    monkeypatch.setattr(kh, "should_claim", lambda: True)
    monkeypatch.setattr(kh, "script_path", lambda: str(tmp_path / "kwin" / "hk.js"))
    # claim() saves its result next to the settings: never the real ones.
    monkeypatch.setattr(kh, "result_path", lambda: str(tmp_path / "kde-hotkeys.json"))
    monkeypatch.setattr(kh.time, "sleep", lambda s: None)   # tests never really wait


class TestKeySequences:
    @pytest.mark.parametrize("name,qt", [
        ("F8", "F8"), ("f12", "F12"), (" F1 ", "F1"),
        ("Ctrl+Alt+V", "Ctrl+Alt+V"), ("ctrl+shift+h", "Ctrl+Shift+H"),
        ("Super+F5", "Meta+F5"),
    ])
    def test_translates(self, name, qt):
        assert kh.qt_key_sequence(name) == qt

    @pytest.mark.parametrize("name", ["", "   ", None, "A", "q", "F13", "Hyper+V",
                                      "Ctrl+", "Ctrl+Esc"])
    def test_refuses(self, name):
        """A bare letter would stop the user typing it anywhere."""
        assert kh.qt_key_sequence(name) is None

    def test_one_shortcut_per_key(self):
        names = [k for _n, _t, k in kh.wanted_shortcuts(_cfg("F8", "F8", ""))]
        assert names == ["F8"]

    def test_key_is_part_of_the_name(self):
        """kglobalaccel keeps a saved key over a new default, so a changed
        hotkey must come in under a new name."""
        assert kh.wanted_shortcuts(_cfg("F8"))[0][0] != kh.wanted_shortcuts(_cfg("F10"))[0][0]
        assert all(n.startswith(kh.SHORTCUT_PREFIX) for n, _t, _k in kh.wanted_shortcuts(_cfg()))


class TestScript:
    def test_registers_every_hotkey_with_a_do_nothing_callback(self):
        script = kh.build_script(kh.wanted_shortcuts(_cfg()))
        for key in ("F8", "F9", "Ctrl+Alt+V"):
            assert f'"{key}", function() {{}});' in script


class TestClaim:
    def test_loads_and_runs_the_script(self, on_kde):
        bus = FakeBus()
        assert kh.claim(_cfg(), bus) is True
        assert ("loadScript", kh.script_path(), kh.SCRIPT_PLUGIN_NAME) in bus.calls
        assert ("start",) in bus.calls
        assert not any(c[0] == "run" for c in bus.calls)
        assert sorted(bus.names) == sorted(n for n, _t, _k in kh.wanted_shortcuts(_cfg()))

    def test_a_changed_hotkey_replaces_the_old_one(self, on_kde):
        bus = FakeBus()
        kh.claim(_cfg("F8", "", ""), bus)
        kh.claim(_cfg("F10", "", ""), bus)
        assert bus.names == ["tuxtalk-hold-F10"]

    def test_clears_leftovers_from_an_older_run(self, on_kde):
        bus = FakeBus(existing=["talktype-hold-F3", "Window Close"])
        kh.claim(_cfg("F8", "", ""), bus)
        assert sorted(bus.names) == ["Window Close", "tuxtalk-hold-F8"]

    def test_a_key_kde_refuses_is_logged(self, on_kde, caplog):
        bus = FakeBus(refuse={"tuxtalk-hold-F8"})
        kh.claim(_cfg("F8", "", ""), bus)
        assert "KDE already uses F8" in caplog.text

    def test_keys_kwin_is_still_registering_are_not_reported(self, on_kde, caplog, monkeypatch):
        """Restarting the service re-claims the keys within a second, and the
        first read-back can come before KWin has finished, which would wrongly
        log "KDE already uses F8" while KWin really holds F8, F9 and Ctrl+Alt+V."""
        waits = []
        monkeypatch.setattr(kh.time, "sleep", waits.append)
        bus = FakeBus()
        real_call, empty_reads = bus.call, [2]

        def slow_kwin(method, *args):
            result = real_call(method, *args)
            if method == "allShortcutInfos" and empty_reads[0]:
                empty_reads[0] -= 1
                return [(i[0], *i[1:6], [], i[7]) for i in result]
            return result
        bus.call = slow_kwin
        kh.claim(_cfg(), bus)
        assert "already uses" not in caplog.text
        assert len(waits) == 2

    def test_a_refused_key_is_reported_after_waiting(self, on_kde, caplog, monkeypatch):
        waits = []
        monkeypatch.setattr(kh.time, "sleep", waits.append)
        kh.claim(_cfg("F8", "", ""), FakeBus(refuse={"tuxtalk-hold-F8"}))
        assert "KDE already uses F8" in caplog.text
        assert 0 < sum(waits) <= 1.5          # it waits a little, never long

    def test_no_keys_means_no_script(self, on_kde):
        bus = FakeBus()
        assert kh.claim(_cfg("", "", ""), bus) is False
        assert not any(c[0] == "loadScript" for c in bus.calls)

    def test_kwin_failing_is_not_fatal(self, on_kde):
        bus = FakeBus(fail={"loadScript"})
        assert kh.claim(_cfg(), bus) is False

    def test_off_kde_does_nothing(self, monkeypatch):
        monkeypatch.setattr(kh, "should_claim", lambda: False)
        bus = FakeBus()
        assert kh.claim(_cfg(), bus) is False
        assert bus.calls == []


class TestRelease:
    def test_unloads_and_forgets_only_ours(self, on_kde):
        bus = FakeBus()
        kh.claim(_cfg(), bus)
        bus.names.append("Window Close")
        kh.release(bus)
        assert ("unloadScript", kh.SCRIPT_PLUGIN_NAME) in bus.calls
        assert bus.names == ["Window Close"]

    def test_never_raises(self, on_kde):
        kh.release(FakeBus(fail={"unloadScript", "shortcutNames", "unregister"}))

    def test_no_bus_is_fine(self, on_kde):
        kh.release()   # conftest makes session_bus() raise


class TestShouldClaim:
    def test_flatpak_never_claims(self, monkeypatch):
        monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
        monkeypatch.setattr(kh.desktop_detect, "get_desktop_environment", lambda: "kde")
        assert kh.should_claim() is False

    def test_only_kde(self, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        monkeypatch.setattr(kh.desktop_detect, "get_desktop_environment", lambda: "gnome")
        assert kh.should_claim() is False


def test_stopping_the_service_releases_the_hotkeys(monkeypatch):
    """SIGTERM skips the service's exit hooks, so the stop path must do it."""
    from tuxtalk import service_launcher
    released = []
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [])
    monkeypatch.setattr(kh, "release", lambda *a: released.append(True))
    service_launcher.stop_dictation_service()
    assert released == [True]


class TestRefusedKeysReachPreferences:
    """The service saves which keys KDE refused, so Preferences can say so
    after a hotkey change rather than leaving it in the log."""

    @pytest.fixture
    def result_file(self, on_kde, monkeypatch, tmp_path):
        path = tmp_path / "kde-hotkeys.json"
        monkeypatch.setattr(kh, "result_path", lambda: str(path))
        return path

    def test_a_refused_key_is_saved(self, result_file):
        before = kh.time.time() - 1
        kh.claim(_cfg("F8", "F9", ""), FakeBus(refuse={"tuxtalk-hold-F8"}))
        assert kh.refused_since(before) == ["F8"]

    def test_all_keys_held_is_saved_as_none_refused(self, result_file):
        before = kh.time.time() - 1
        kh.claim(_cfg(), FakeBus())
        assert kh.refused_since(before) == []

    def test_no_result_yet_or_an_old_one_is_unknown(self, result_file):
        assert kh.refused_since(0) is None
        kh.claim(_cfg(), FakeBus())
        assert kh.refused_since(kh.time.time() + 60) is None    # checked before that

    def test_a_broken_result_file_is_unknown_not_an_error(self, result_file):
        result_file.write_text("{not json")
        assert kh.refused_since(0) is None


class TestRenameToTuxTalk:
    """0.15.0: TalkType became TuxTalk. Shortcuts left by a TalkType run must
    go, or KDE would hold F8 twice (once per name) and stray keys could leak."""

    def test_new_shortcuts_carry_the_new_name(self):
        for name, text, _key in kh.wanted_shortcuts(_cfg()):
            assert name.startswith("tuxtalk-")
            assert text.startswith("TuxTalk: ") and text.endswith("managed by TuxTalk")

    def test_release_removes_talktype_and_tuxtalk_shortcuts_only(self, on_kde):
        bus = FakeBus(existing=["talktype-hold-F8", "talktype-toggle-F9",
                                "tuxtalk-hold-F8", "Window Close"])
        kh.release(bus)
        assert bus.names == ["Window Close"]

    def test_release_unloads_the_old_script_too(self, on_kde):
        bus = FakeBus()
        kh.release(bus)
        unloaded = [c[1] for c in bus.calls if c[0] == "unloadScript"]
        assert "talktype-hotkeys" in unloaded and "tuxtalk-hotkeys" in unloaded


class TestAppLaunchShortcuts:
    """2026-10-13: F6 opened KDE's Emoji Selector AND started dictation, with
    no warning. App-launch shortcuts live in their own kglobalaccel component
    (org.kde.plasma.emojier.desktop), which kglobalaccel doesn't weigh against
    a KWin script's shortcut, so our F6 was accepted and the read-back looked
    fine. claim() now looks through the other components for our keys."""

    F6 = 0x01000035                       # Qt::Key_F6
    META_DOT = 0x10000000 | 0x2E          # Meta+.

    class AppsBus(FakeBus):
        def __init__(self, components, desktop_files=(), **kw):
            super().__init__(**kw)
            # path -> (unique name, friendly name, [Qt key codes], active)
            self.components = components
            self.desktop_files = set(desktop_files)

        def call(self, method, *args):
            if method == "allComponents":
                return ["/component/kwin", *self.components]
            if method == "componentShortcuts":
                unique, friendly, keys, _active = self.components[args[0]]
                return [("_launch", friendly, unique, friendly, "default", "", keys, keys)]
            if method == "componentIsActive":
                return self.components[args[0]][3]
            return super().call(method, *args)

    @pytest.fixture
    def apps(self, on_kde, monkeypatch):
        def make(components, desktop_files=()):
            bus = self.AppsBus(components)
            monkeypatch.setattr(kh, "_desktop_file_exists",
                                lambda name: name in set(desktop_files))
            return bus
        return make

    EMOJI = {"/component/org_kde_plasma_emojier_desktop":
             ("org.kde.plasma.emojier.desktop", "Emoji Selector", [F6, META_DOT], False)}

    @pytest.mark.parametrize("name,code", [
        ("F6", 0x01000035), ("F1", 0x01000030), ("F12", 0x0100003B),
        ("Shift+F8", 0x02000000 | 0x01000037), ("Ctrl+Alt+V", 0x04000000 | 0x08000000 | 0x56),
        ("Super+F9", 0x10000000 | 0x01000038), ("", None), ("Space", None),
    ])
    def test_qt_key_codes(self, name, code):
        assert kh.qt_key_code(kh.qt_key_sequence(name)) == code

    @pytest.mark.parametrize("hold,vc,qt_code", [
        ("Scroll Lock", "", 0x01000026), ("F8", "Super+F9", 0x10000000 | 0x01000038)])
    def test_an_app_on_scroll_lock_or_a_super_shortcut_is_reported(self, apps, hold, vc,
                                                                    qt_code):
        """Qt names them ScrollLock and Meta, which the check used to read
        as TuxTalk names: it never matched either."""
        before = kh.time.time() - 1
        comps = {"/component/kmix": ("kmix", "Audio Volume", [qt_code], True)}
        kh.claim(_cfg(hold, "F10", vc), apps(comps))
        assert kh.app_shortcuts_since(before) == {kh.qt_key_sequence(hold if not vc else vc):
                                                  "Audio Volume"}

    def test_an_app_launched_by_our_key_is_reported(self, apps, caplog):
        before = kh.time.time() - 1
        kh.claim(_cfg("F6", "", ""), apps(self.EMOJI, {"org.kde.plasma.emojier.desktop"}))
        assert "Emoji Selector" in caplog.text and "F6" in caplog.text
        assert kh.refused_since(before) == ["F6"]
        assert kh.app_shortcuts_since(before) == {"F6": "Emoji Selector"}

    def test_another_key_of_that_app_is_no_clash(self, apps, caplog):
        kh.claim(_cfg("F8", "F9", ""), apps(self.EMOJI, {"org.kde.plasma.emojier.desktop"}))
        assert "Emoji Selector" not in caplog.text

    def test_a_leftover_from_an_uninstalled_app_is_ignored(self, apps, caplog):
        before = kh.time.time() - 1
        kh.claim(_cfg("F6", "", ""), apps(self.EMOJI, desktop_files=()))
        assert kh.refused_since(before) == []

    def test_a_running_app_with_its_own_shortcut_counts(self, apps):
        before = kh.time.time() - 1
        comps = {"/component/kmix": ("kmix", "Audio Volume", [self.F6], True)}
        kh.claim(_cfg("F6", "", ""), apps(comps))
        assert kh.app_shortcuts_since(before) == {"F6": "Audio Volume"}

    def test_an_old_talktype_component_is_ours_not_a_clash(self, apps):
        """TalkType's portal component still lists F3/F4 on Ron's machine."""
        before = kh.time.time() - 1
        comps = {"/component/io_github_ronb1964_TalkType":
                 ("io.github.ronb1964.TalkType", "TalkType", [0x01000032], True)}
        kh.claim(_cfg("F3", "", ""), apps(comps))
        assert kh.refused_since(before) == []

    def test_a_kde_that_cannot_list_components_is_fine(self, on_kde, caplog):
        before = kh.time.time() - 1
        kh.claim(_cfg("F6", "", ""), FakeBus(fail={"allComponents"}))
        assert kh.refused_since(before) == []
        assert kh.app_shortcuts_since(before) == {}


def test_every_kwin_call_is_bounded_and_scripting_goes_through_kwin_focus(monkeypatch):
    """A frozen KWin held each call 25 s; the scripting calls kept a copy of
    kwin_focus's proxy code, overloaded loadScript's signature included."""
    dbus = pytest.importorskip("dbus")
    calls = []

    class Remote:
        def __init__(self, path):
            self.path = path

        def __getattr__(self, method):
            return lambda *args, **kwargs: calls.append((self.path, method, kwargs)) or []

    class Bus:
        def get_object(self, service, path, **kwargs):
            assert kwargs.get("introspect") is False    # a call of its own, unbounded
            return Remote(path)
    monkeypatch.setattr(dbus, "SessionBus", Bus)
    bus = _REAL_SESSION_BUS()
    bus.call("loadScript", "/x.js", "tuxtalk-hotkeys")
    bus.call("unregister", "kwin", "tuxtalk-F8")
    bus.call("shortcutNames")
    assert all(kwargs["timeout"] == 3.0 for _path, _method, kwargs in calls)
    assert calls[0][:2] == ("/Scripting", "loadScript") and calls[0][2]["signature"] == "ss"
