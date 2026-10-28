"""The single-instance locks must be sandbox-private inside a Flatpak.

Flatpak shares XDG_RUNTIME_DIR (/run/user/N) with the host, so a host or
AppImage TuxTalk's lock files are visible inside the sandbox, and the
Flatpak's tray and service would exit as "already running" and never start.
Flatpak gives each app a private $XDG_RUNTIME_DIR/app/$FLATPAK_ID; the locks
live there in a Flatpak. On the host the location is unchanged.
"""
from tuxtalk import single_instance


def test_runtime_dir_is_app_private_inside_flatpak(monkeypatch, tmp_path):
    run = tmp_path / "run"
    app_dir = run / "app" / "io.github.ronb1964.TalkType"
    app_dir.mkdir(parents=True)
    for folder in (run, app_dir):
        folder.chmod(0o700)         # as the desktop and Flatpak make them
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    assert single_instance.runtime_dir() == str(app_dir)


def test_runtime_dir_falls_back_when_app_subdir_absent(monkeypatch, tmp_path):
    """A per-app folder that isn't there: the base folder, not a path that
    doesn't exist."""
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    assert single_instance.runtime_dir() == str(run)


def test_runtime_dir_unchanged_on_host(monkeypatch, tmp_path):
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert single_instance.runtime_dir() == str(run)
