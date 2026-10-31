"""The VM release runner (tests/vm/run.py) never passes a run that ran
nothing, and never mixes one run's results with another's.

A mistyped --only id used to filter every check away: the run touched the
VM, ran nothing and exited 0, which reads as "checked and fine".
"""
from datetime import datetime

import pytest
from conftest import import_vm_tool

# The harness reads its settings with tomllib, new in Python 3.11.
pytest.importorskip("tomllib", reason="the VM harness needs Python 3.11 or newer")
run = import_vm_tool("run")


def test_a_check_id_that_doesnt_exist_is_refused():
    assert run.selection_problem(["kde"], {"basic_typo"}) == "no check called basic_typo"


def test_a_check_for_another_desktop_alone_is_refused():
    """The GNOME extension's checks don't run on KDE: nothing would."""
    assert run.selection_problem(["kde"], {"ext_install"}) == "ext_install doesn't run on kde"
    assert run.selection_problem(["ubuntu"], {"ext_install"}) is None


def test_a_full_run_and_a_real_selection_are_fine():
    assert run.selection_problem(["kde", "ubuntu"], None) is None
    assert run.selection_problem(["kde"], {"welcome"}) is None
    assert [c.id for c in run.selected(run.VMS["kde"], {"welcome"})] == ["welcome"]


def test_the_command_line_stops_before_any_vm_is_touched(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(run, "test", lambda *a: started.append(a) or [])
    monkeypatch.setattr("sys.argv", ["run.py", "test", "kde", "--appimage", str(tmp_path),
                                     "--as-is", "--only", "welcome,basic_typo"])
    with pytest.raises(SystemExit) as stop:
        run.main()
    assert stop.value.code == 2 and started == []


def test_a_run_that_ran_nothing_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "test", lambda *a: [])
    monkeypatch.setattr("sys.argv", ["run.py", "test", "kde", "--appimage", str(tmp_path)])
    with pytest.raises(SystemExit) as stop:
        run.main()
    assert stop.value.code == 1


def test_two_runs_started_in_the_same_second_get_folders_of_their_own(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "RUNS", tmp_path / "runs")
    at = datetime(2026, 10, 31, 12, 0, 1)
    first, second = run.new_run_folder(at), run.new_run_folder(at)
    assert first != second and first.is_dir() and second.is_dir()
    assert first.name == "2026-10-31-120001"


def test_the_harness_modules_dont_stay_importable_by_their_plain_names(monkeypatch):
    """"run" and "report" are common names: another test's import must not
    get the VM harness's, and an earlier module of that name stays."""
    import sys
    import types
    earlier = types.ModuleType("report")
    monkeypatch.setitem(sys.modules, "report", earlier)
    again = import_vm_tool("run")
    assert again.report is not earlier and again.report.build
    assert sys.modules["report"] is earlier
    assert "run" not in sys.modules and "harness" not in sys.modules

