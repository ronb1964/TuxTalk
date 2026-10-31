"""SetModel over D-Bus must actually change the model.

The D-Bus interface declares SetModel and the GNOME extension's interface XML
lists it, so both sides must really implement it:

  * DBusService._dispatch only forwards when hasattr(app, name) is true, so
    TrayAppInstance — the object the tray registers — needs set_model, or the
    call is silently dropped with no error anywhere.
  * The service's implementation must save through config.save_config(), not
    to a settings.json this app never reads.
"""

from types import SimpleNamespace

from tuxtalk.tray import DictationTray


def test_set_model_writes_the_model_and_restarts(monkeypatch):
    saved = {}
    restarted = []
    emitted = []

    cfg = SimpleNamespace(model="small", device="cpu")
    monkeypatch.setattr("tuxtalk.config.load_config", lambda: cfg)
    monkeypatch.setattr("tuxtalk.config.save_config",
                        lambda c: saved.update(model=c.model))

    tray = SimpleNamespace(
        update_menu_display=lambda: None,
        _emit_model_changed=lambda m: emitted.append(m),
        restart_service=lambda _: restarted.append(True),
    )

    DictationTray.set_model(tray, "large-v3")

    assert saved.get("model") == "large-v3", "the model was never persisted"
    assert emitted == ["large-v3"], "the GNOME extension was not told"
    assert restarted, "the service was not restarted to pick up the new model"


def test_set_model_rejects_an_unknown_model(monkeypatch):
    """A typo over D-Bus must not write a model the service cannot load."""
    saved = {}
    cfg = SimpleNamespace(model="small", device="cpu")
    monkeypatch.setattr("tuxtalk.config.load_config", lambda: cfg)
    monkeypatch.setattr("tuxtalk.config.save_config",
                        lambda c: saved.update(model=c.model))

    tray = SimpleNamespace(
        update_menu_display=lambda: None,
        _emit_model_changed=lambda m: None,
        restart_service=lambda _: None,
    )

    DictationTray.set_model(tray, "not-a-real-model")

    assert saved == {}, "wrote an unknown model to the config"


# --- the dictation service's own implementation -----------------------------

def test_the_service_does_not_write_a_phantom_settings_json(service_main, monkeypatch, tmp_path):
    """SetModel with no tray running reaches the service's own stand-in, which
    must save through config.save_config, not to a settings.json this app never
    reads: anything else is gone after the restart it tells the user is required."""
    from tuxtalk import config
    saved = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(config, "load_config", config.Settings)
    monkeypatch.setattr(config, "save_config", lambda cfg: saved.append(cfg.model))
    service = service_main().app_instance
    service.set_model("medium")
    service.set_model("not-a-real-model")
    assert saved == ["medium"]
    assert service.config.model == "medium"
    assert not list(tmp_path.rglob("settings.json"))
