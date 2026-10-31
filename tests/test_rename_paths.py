"""Folder names, log name and app ID after the rename (TuxTalk, 0.15.0)."""
from tuxtalk import config, desktop_identity, logger


def test_new_folder_names():
    assert config.CONFIG_DIR in ("tuxtalk", "tuxtalk-dev")
    assert config.DATA_DIR in ("TuxTalk", "TuxTalk-dev")


def test_one_app_id_everywhere():
    assert config.APP_ID == "io.github.ronb1964.TuxTalk"
    assert desktop_identity.APP_ID == config.APP_ID


def test_log_lives_in_the_new_folder(monkeypatch):
    monkeypatch.delenv("TUXTALK_LOG_DIR", raising=False)
    monkeypatch.delenv("DEV_MODE", raising=False)
    assert str(logger._log_dir()).endswith("/.config/tuxtalk")
    monkeypatch.setenv("DEV_MODE", "1")
    assert str(logger._log_dir()).endswith("/.config/tuxtalk-dev")
    assert logger.LOG_FILE_NAME == "tuxtalk.log"


def test_clearing_the_log_empties_it_and_drops_the_backup(tmp_path):
    (tmp_path / "tuxtalk.log").write_text("lots")
    (tmp_path / "tuxtalk.log.old").write_text("older")
    logger.clear_log(tmp_path)
    assert (tmp_path / "tuxtalk.log").read_text() == ""
    assert not (tmp_path / "tuxtalk.log.old").exists()


def test_help_points_at_the_new_log_and_settings_folder(monkeypatch):
    from tuxtalk import help_text
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    facts = help_text.install_facts()
    assert facts["cfg_dir"] == "~/.config/tuxtalk/"
    assert facts["log_path"] == "~/.config/tuxtalk/tuxtalk.log"


def test_tray_logs_what_the_migration_did(tray_main, monkeypatch):
    # The migration runs at package import, before any logger exists; the
    # tray writes its lines once its logger is up, so a support log shows it.
    import types
    import tuxtalk
    from tuxtalk import tray
    logged = []
    monkeypatch.setattr(tuxtalk, "MIGRATION_LOG", [".config/talktype -> .config/tuxtalk"])
    monkeypatch.setattr(tray, "logger", types.SimpleNamespace(
        info=logged.append, warning=logged.append, debug=logged.append, error=logged.append))
    tray_main()
    assert "Rename to TuxTalk: .config/talktype -> .config/tuxtalk" in logged


def test_tray_logs_a_failed_migration_as_a_warning(tray_main, monkeypatch):
    import types
    import tuxtalk
    from tuxtalk import tray
    warned = []
    monkeypatch.setattr(tuxtalk, "MIGRATION_LOG", ["could not move .config/talktype: [Errno 13] no"])
    monkeypatch.setattr(tray, "logger", types.SimpleNamespace(
        info=lambda m: None, warning=warned.append, debug=lambda m: None, error=lambda m: None))
    tray_main()
    assert "Rename to TuxTalk: could not move .config/talktype: [Errno 13] no" in warned


def test_clearing_the_log_also_removes_the_pre_rename_logs(tmp_path):
    """The rename moved the config folder with talktype.log inside, and those
    can hold dictations logged before the rename."""
    (tmp_path / "talktype.log").write_text("old dictation")
    (tmp_path / "talktype.log.old").write_text("older dictation")
    logger.clear_log(tmp_path)
    assert not (tmp_path / "talktype.log").exists()
    assert not (tmp_path / "talktype.log.old").exists()
