"""Advanced → System check: one line while all is well, open when it isn't.

The graphics card, typing setup and GNOME extension had a section each,
read by new users as homework even when nothing needed doing.
"""
from types import SimpleNamespace

import pytest

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


@pytest.fixture
def engine_check(monkeypatch):
    """System check's graphics engine row, on a computer where build 1 is
    what's installed; returns the window, whose config the test sets."""
    from gi.repository import Gtk
    from tuxtalk import whisper_vulkan
    w = _window()
    w.engine_update_label, w.engine_update_button = Gtk.Label(), Gtk.Button()
    w.win = SimpleNamespace(vulkan_offered=lambda: True)
    w.config = {"device": "vulkan", "model": "large-v3", "auto_check_updates": False}
    monkeypatch.setattr(whisper_vulkan, "update_waiting", lambda: True)
    return w


def test_an_older_graphics_engine_is_offered_with_its_security_fix(engine_check, monkeypatch):
    """With checking for updates at start off, build 1 of the graphics
    engine never updated by itself, and its keyless /load route stayed
    open to other accounts on the computer."""
    from tuxtalk import whisper_vulkan
    w = engine_check
    SystemCheck._check_engine_update(w)
    assert w.engine_update_button.get_visible() and "security" in w.engine_update_label.get_text()
    assert "needs your attention" in w.summary()
    monkeypatch.setattr(whisper_vulkan, "update_waiting", lambda: False)
    SystemCheck._check_engine_update(w)
    assert not w.engine_update_button.get_visible()
    assert "everything is set up" in w.summary()


@pytest.mark.parametrize("change", [{"model": "parakeet-v3"}, {"device": "cpu"},
                                    {"auto_check_updates": True}])
def test_the_engine_update_isnt_offered_where_the_old_server_never_runs(engine_check, change):
    """Parakeet talks to its engine over pipes, a device left Vulkan doesn't
    start it, and with update checks on the service updates by itself:
    System check opened itself with a warning that didn't apply."""
    engine_check.config.update(change)
    SystemCheck._check_engine_update(engine_check)
    assert not engine_check.engine_update_button.get_visible()
    assert "everything is set up" in engine_check.summary()


def test_the_engine_waits_for_this_build_only_while_an_earlier_one_runs(tmp_path, monkeypatch):
    from tuxtalk import whisper_vulkan
    monkeypatch.setattr(whisper_vulkan, "_engines_root", lambda: str(tmp_path))
    monkeypatch.setattr(whisper_vulkan, "_can_run",
                        lambda folder, model=None: whisper_vulkan.ENGINE_VERSION + "/" in folder)
    (tmp_path / whisper_vulkan.ENGINE_VERSION).mkdir()          # build 1, as installed
    assert whisper_vulkan.update_waiting()


def test_a_download_that_lost_the_race_leaves_the_earlier_build(tmp_path, monkeypatch):
    """System check's download and the service's own ran at once: the one
    that finished second took the new build for the earlier one and deleted
    build 1, which the service had chosen and was about to start."""
    import contextlib
    from tuxtalk import download_utils, engine_process, whisper_vulkan
    monkeypatch.setattr(whisper_vulkan, "_engines_root", lambda: str(tmp_path))
    asked = []          # not there when the download starts; there by its install
    monkeypatch.setattr(whisper_vulkan, "_can_run",
                        lambda folder, model=None: bool(asked.append(folder) or len(asked) > 1))
    monkeypatch.setattr(download_utils, "recover_cut_off_swaps", lambda *a: None)
    monkeypatch.setattr(download_utils, "download_area",
                        lambda *a, **k: contextlib.nullcontext(("archive", "folder")))
    monkeypatch.setattr(download_utils, "download_file", lambda *a, **k: True)
    monkeypatch.setattr(download_utils, "unpack_tar", lambda *a: None)
    monkeypatch.setattr(download_utils, "install_turn", lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(download_utils, "restore_cut_off_swap", lambda *a: None)
    monkeypatch.setattr(download_utils, "replace_folder", lambda *a: pytest.fail("replaced"))
    removed = []
    monkeypatch.setattr(engine_process, "remove_other_versions", lambda *a: removed.append(a))
    assert whisper_vulkan.make_engine_download_func()(lambda *report: None, None)
    assert removed == []
