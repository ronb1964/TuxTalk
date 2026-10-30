"""Whether first-run setup is finished (first_run_marker.py)."""
import subprocess
import sys

from tuxtalk import config, first_run_marker


def test_first_run_setup_shows_until_it_is_finished(tmp_path, monkeypatch):
    """The welcome screens come back at every start until setup is finished,
    then never again."""
    monkeypatch.setattr(config, "get_data_dir", lambda: str(tmp_path / "TuxTalk"))
    assert first_run_marker.is_first_run() is True
    first_run_marker.mark_first_run_complete()
    assert first_run_marker.is_first_run() is False


def test_the_dictation_service_reads_the_marker_without_loading_windows():
    """The service's hotkeys ask whether setup is finished before watching any
    key: that took first-run setup's welcome and hotkey-test windows, and GTK
    with them, into the service."""
    code = ("import sys, tuxtalk.hotkeys, tuxtalk.first_run_marker as m; m.is_first_run(); "
            "sys.exit('gi.repository.Gtk' in sys.modules "
            "or 'tuxtalk.first_run_setup' in sys.modules)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
