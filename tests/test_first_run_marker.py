"""Whether first-run setup is finished (first_run_marker.py)."""
import subprocess
import sys

import pytest

from tuxtalk import config, first_run_marker


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    folder = tmp_path / "TuxTalk"
    monkeypatch.setattr(config, "get_data_dir", lambda: str(folder))
    return folder


def test_first_run_setup_shows_until_it_is_finished(data_dir):
    """The welcome screens come back at every start until setup is finished,
    then never again."""
    assert first_run_marker.is_first_run() is True
    first_run_marker.mark_first_run_complete()
    assert first_run_marker.is_first_run() is False


def test_a_computer_set_up_by_an_earlier_version_isnt_set_up_again(data_dir):
    """Earlier versions left .first_run_done in the data folder: with another
    name, every update would start first-run setup all over again."""
    data_dir.mkdir()
    (data_dir / ".first_run_done").write_text("done")
    assert first_run_marker.is_first_run() is False


def test_finishing_setup_writes_the_marker_earlier_versions_read(data_dir):
    first_run_marker.mark_first_run_complete()
    assert [p.name for p in data_dir.iterdir()] == [".first_run_done"]


def test_the_dictation_service_reads_the_marker_without_loading_windows():
    """The service's hotkeys ask whether setup is finished before watching any
    key: that took first-run setup's welcome and hotkey-test windows, and GTK
    with them, into the service."""
    code = ("import sys, tuxtalk.hotkeys, tuxtalk.first_run_marker as m; m.is_first_run(); "
            "print([n for n in ('gi.repository.Gtk', 'tuxtalk.first_run_setup') "
            "if n in sys.modules])")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
