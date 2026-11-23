"""Advanced → System check: one line while all is well, open when it isn't.

The graphics card, typing setup and GNOME extension had a section each,
read by new users as homework even when nothing needed doing.
"""
from types import SimpleNamespace

from tuxtalk.prefs_system_check import SystemCheck
from tuxtalk.ui_style import Fold


def _window():
    w = SimpleNamespace(fold=Fold("Show system check", "Hide system check"),
                        _system_problems=set())
    w.report = lambda part, ok: SystemCheck.report(w, part, ok)
    w.summary = lambda: w.fold.summary.get_text()
    return w


def test_all_well_stays_closed_and_says_so():
    w = _window()
    w.report(None, True)
    w.report("typing", True)
    assert "everything is set up" in w.summary()
    assert not w.fold.is_open()


def test_a_problem_opens_the_fold_and_fixing_it_clears_the_warning():
    w = _window()
    w.report("typing", False)
    assert "needs your attention" in w.summary() and w.fold.is_open()
    w.fold.button.clicked()          # the user closes it
    w.report("typing", False)                # the same problem, checked again
    assert not w.fold.is_open()      # not forced open a second time
    w.report("typing", True)
    assert "everything is set up" in w.summary()


def test_two_problems_need_both_fixed():
    w = _window()
    w.report("typing", False)
    w.report("extension", False)
    w.report("typing", True)
    assert "needs your attention" in w.summary()


def test_an_older_graphics_engine_is_offered_with_its_security_fix(monkeypatch):
    """With checking for updates at start off, build 1 of the graphics
    engine never updated by itself, and its keyless /load route stayed
    open to other accounts on the computer."""
    from gi.repository import Gtk
    from tuxtalk import whisper_vulkan
    w = _window()
    w.engine_update_label, w.engine_update_button = Gtk.Label(), Gtk.Button()
    monkeypatch.setattr(whisper_vulkan, "is_offered", lambda: True)
    monkeypatch.setattr(whisper_vulkan, "update_waiting", lambda: True)
    SystemCheck._check_engine_update(w)
    assert w.engine_update_button.get_visible() and "security" in w.engine_update_label.get_text()
    assert "needs your attention" in w.summary()
    monkeypatch.setattr(whisper_vulkan, "update_waiting", lambda: False)
    SystemCheck._check_engine_update(w)
    assert not w.engine_update_button.get_visible()
    assert "everything is set up" in w.summary()


def test_the_engine_waits_for_this_build_only_while_an_earlier_one_runs(tmp_path, monkeypatch):
    from tuxtalk import whisper_vulkan
    monkeypatch.setattr(whisper_vulkan, "_engines_root", lambda: str(tmp_path))
    monkeypatch.setattr(whisper_vulkan, "_can_run",
                        lambda folder, model=None: whisper_vulkan.ENGINE_VERSION + "/" in folder)
    (tmp_path / whisper_vulkan.ENGINE_VERSION).mkdir()          # build 1, as installed
    assert whisper_vulkan.update_waiting()
