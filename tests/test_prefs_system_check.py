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
