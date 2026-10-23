"""One-time tray paths that must not freeze the menu by waiting on the GTK
main loop.

* Replacing a pre-rename service can wait up to 3 s for it to exit.
* After onboarding, KDE's menu cache rebuild (kbuildsycoca) can take up to 15 s.
"""
import types

from tuxtalk import tray as tray_mod


class FakeGLib:
    def __init__(self):
        self.timers = []

    def timeout_add(self, _ms, fn, *args):
        self.timers.append(lambda: fn(*args))


def test_waiting_for_the_old_service_does_not_sleep(monkeypatch):
    loop = FakeGLib()
    monkeypatch.setattr(tray_mod, "GLib", loop)
    monkeypatch.setattr(tray_mod.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("slept")))
    old = [[111], [111], []]          # still there for two checks, then gone
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids",
                        lambda: old[0] if len(old) == 1 else old.pop(0))
    stops = []
    monkeypatch.setattr("tuxtalk.service_launcher.stop_dictation_service", lambda: stops.append(1))
    started = []
    t = types.SimpleNamespace(onboarding_in_progress=False,
                              is_service_running=lambda: False,
                              start_service=lambda _: started.append(1))
    t._auto_start_service = lambda w=0: tray_mod.DictationTray._auto_start_service(t, w)

    assert t._auto_start_service() is False
    while loop.timers:
        loop.timers.pop(0)()
    assert stops == [1] and started == [1]


def test_the_wait_gives_up_after_three_seconds(monkeypatch):
    loop = FakeGLib()
    monkeypatch.setattr(tray_mod, "GLib", loop)
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids", lambda: [111])
    monkeypatch.setattr("tuxtalk.service_launcher.stop_dictation_service", lambda: None)
    started = []
    t = types.SimpleNamespace(onboarding_in_progress=False,
                              is_service_running=lambda: True,
                              start_service=lambda _: started.append(1))
    t._auto_start_service = lambda w=0: tray_mod.DictationTray._auto_start_service(t, w)
    t._auto_start_service()
    checks = 0
    while loop.timers:
        loop.timers.pop(0)()
        checks += 1
    assert checks == 30


def test_the_kde_cache_rebuild_falls_back_to_kbuildsycoca5(monkeypatch):
    ran = []

    def fake_run(argv, **_):
        ran.append(argv[0])
        if argv[0] == "kbuildsycoca6":
            raise FileNotFoundError(argv[0])

    monkeypatch.setattr(tray_mod.subprocess, "run", fake_run)
    tray_mod._refresh_kde_menu_cache()
    assert ran == ["kbuildsycoca6", "kbuildsycoca5"]


def test_the_kde_cache_rebuild_runs_off_the_main_loop(tray_main, monkeypatch):
    """After first-run setup the rebuild runs on its own thread, so the menu
    never waits for it."""
    import threading
    ran = []
    done = threading.Event()
    monkeypatch.setattr(tray_mod, "_refresh_kde_menu_cache",
                        lambda: ran.append(threading.current_thread()) or done.set())
    tray_main(first_run=True).run_timers()
    assert done.wait(5)
    assert ran[0] is not threading.main_thread()
