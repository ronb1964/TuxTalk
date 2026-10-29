"""KDE's app menu learns about a written or removed launcher.

kbuildsycoca6 that fails must not stop kbuildsycoca5 from being tried, and a
quick rebuild that fails is tried once more in full, as before the tray split.
"""
import types

from tuxtalk import menu_cache


def _tools(monkeypatch, codes):
    """subprocess.run standing in for the tools: *codes* maps a command
    line to its exit code; one not there isn't installed. A home with no
    mime cache, unless the test makes one."""
    monkeypatch.setenv("HOME", "/nonexistent")
    ran = []

    def run(argv, **_kwargs):
        ran.append(" ".join(argv))
        if ran[-1] not in codes and not argv[0].startswith("update-desktop"):
            raise FileNotFoundError(argv[0])
        return types.SimpleNamespace(returncode=codes.get(ran[-1], 0))
    monkeypatch.setattr(menu_cache.subprocess, "run", run)
    return ran


def test_plasma_5_is_tried_when_plasma_6s_tool_fails(monkeypatch):
    ran = _tools(monkeypatch, {"kbuildsycoca6": 1, "kbuildsycoca5": 0})
    menu_cache.refresh_menu_cache()
    assert ran == ["kbuildsycoca6", "kbuildsycoca5"]


def test_a_failed_quick_rebuild_is_done_again_in_full(monkeypatch):
    ran = _tools(monkeypatch, {"kbuildsycoca6": 1, "kbuildsycoca6 --noincremental": 0})
    menu_cache.refresh_menu_cache()
    assert ran == ["kbuildsycoca6", "kbuildsycoca5", "kbuildsycoca6 --noincremental"]


def test_one_that_works_ends_it(monkeypatch):
    ran = _tools(monkeypatch, {"kbuildsycoca6": 0})
    menu_cache.refresh_menu_cache()
    assert ran == ["kbuildsycoca6"]


def test_no_mime_cache_is_started_where_there_was_none(monkeypatch, tmp_path):
    """KDE has none: one written there would go stale."""
    ran = _tools(monkeypatch, {"kbuildsycoca6": 0})
    monkeypatch.setenv("HOME", str(tmp_path))
    menu_cache.refresh_menu_cache()
    assert not any(cmd.startswith("update-desktop-database") for cmd in ran)
    apps = tmp_path / ".local/share/applications"
    apps.mkdir(parents=True)
    (apps / "mimeinfo.cache").write_text("")
    menu_cache.refresh_menu_cache()
    assert any(cmd.startswith("update-desktop-database") for cmd in ran)
