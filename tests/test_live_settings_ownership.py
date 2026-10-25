"""One owner for the config-change signal, and no snapshots of live settings.

Both rules prevent the same failure: a setting Preferences reports as "applied
without restart" that the running service never actually picks up.

1. _config_file_changed() is edge-triggered — it compares mtime against a
   module global and returns True once per change. _service_tick() is the
   single detector (Backend B runs no evdev loop, so the tick must check) and
   hands the reloaded settings back to the evdev loop, which rebinds
   hold_key and toggle_key from them. A second check in the loop would always
   see False, and a changed hotkey would do nothing until a restart.

2. _reload_live_settings refreshes cfg in place, so _ServiceState must read the
   auto-timeout from cfg rather than a snapshot taken at construction, or
   switching the timeout off leaves the service still exiting on it.
"""

import types

import pytest

from tuxtalk import app, hotkey_keys
from tuxtalk import type_at_pause
from tuxtalk import engine
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs
from tuxtalk import recording


def _idle(monkeypatch):
    """A service that is running but not recording."""
    recording.start_requested.clear()
    recording.stop_requested.clear()
    monkeypatch.setattr(rs.state, "is_recording", False, raising=False)


def _cfg(**overrides):
    base = dict(auto_timeout_enabled=False, auto_timeout_minutes=5, notify=False)
    base.update(overrides)
    return types.SimpleNamespace(**base)


class TestChangeSignalHasOneOwner:
    def test_tick_hands_back_the_reloaded_settings(self, monkeypatch):
        """The evdev loop rebinds its hotkey locals from this return value.

        Without it the loop has to re-detect the change itself, and the
        detector is edge-triggered — the tick already consumed it.
        """
        _idle(monkeypatch)
        reloaded = app.LiveSettings(
            hold_key=60, toggle_key=None, vc_hotkey_str="",
            voice_cmds_combo=None, voice_cmds_main_key=None,
        )
        monkeypatch.setattr(live, "config_file_changed", lambda: True)
        monkeypatch.setattr(app, "_reload_live_settings", lambda cfg, ind: reloaded)

        cfg = _cfg()
        assert app._service_tick(cfg, app._ServiceState(cfg)) is reloaded

    def test_tick_hands_back_nothing_when_the_config_is_unchanged(self, monkeypatch):
        _idle(monkeypatch)
        monkeypatch.setattr(live, "config_file_changed", lambda: False)

        cfg = _cfg()
        assert app._service_tick(cfg, app._ServiceState(cfg)) is None

    def test_a_new_hotkey_survives_the_tick(self, monkeypatch):
        """End to end: the change is detected once and the new key comes back.

        If the tick discarded the result, the caller would have to detect the
        change a second time, which can never succeed.
        """
        _idle(monkeypatch)
        monkeypatch.setattr(live, "config_file_changed", lambda: True)
        monkeypatch.setattr(app, "load_config", lambda: _cfg(
            hotkey="F9", toggle_hotkey="", voice_commands_hotkey="", mode="hold",
            key_delay=12, log_transcripts=False,
        ))

        cfg = _cfg(hotkey="F8", toggle_hotkey="", voice_commands_hotkey="",
                   mode="hold", key_delay=12, log_transcripts=False)
        reloaded = app._service_tick(cfg, app._ServiceState(cfg))

        assert reloaded is not None, "the tick must return what it reloaded"
        assert reloaded.hold_key == hotkey_keys.keycode_from_name("F9")


class _Engine:
    """A loaded speech model whose helper process can be stopped."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class TestAutoTimeoutIsLive:
    def test_disabling_the_timeout_takes_effect_without_a_restart(self, monkeypatch):
        """Untick 'auto-timeout' and the service must stop exiting on it."""
        _idle(monkeypatch)
        cfg = _cfg(auto_timeout_enabled=True, auto_timeout_minutes=5)
        svc = app._ServiceState(cfg)

        # What _reload_live_settings does: update cfg in place.
        cfg.auto_timeout_enabled = False

        assert svc.timeout_enabled is False

    def test_enabling_the_timeout_takes_effect_without_a_restart(self, monkeypatch):
        _idle(monkeypatch)
        cfg = _cfg(auto_timeout_enabled=False, auto_timeout_minutes=1)
        svc = app._ServiceState(cfg)

        cfg.auto_timeout_enabled = True

        assert svc.timeout_enabled is True
        assert svc.timeout_seconds == 60

    def test_a_shortened_timeout_takes_effect_without_a_restart(self, monkeypatch):
        _idle(monkeypatch)
        cfg = _cfg(auto_timeout_enabled=True, auto_timeout_minutes=30)
        svc = app._ServiceState(cfg)

        cfg.auto_timeout_minutes = 2

        assert svc.timeout_minutes == 2
        assert svc.timeout_seconds == 120

    def test_a_disabled_timeout_does_not_shut_the_service_down(self, monkeypatch):
        """The user-visible failure: 'disabled' and it exits anyway."""
        _idle(monkeypatch)
        monkeypatch.setattr(live, "config_file_changed", lambda: False)
        cfg = _cfg(auto_timeout_enabled=True, auto_timeout_minutes=5)
        svc = app._ServiceState(cfg)
        svc.last_activity = 0.0  # idle since the epoch

        cfg.auto_timeout_enabled = False
        loaded = _Engine()
        monkeypatch.setattr(engine, "model", loaded)

        app._service_tick(cfg, svc)
        assert engine.model is loaded          # not rested

    def test_switching_the_timeout_on_does_not_stop_the_service_at_once(self, monkeypatch):
        """The idle clock must not count only D-Bus commands while the option is
        off, or after a long session of F8 dictation, ticking it on stops the
        service on the next pass, right after the user turned the option on."""
        _idle(monkeypatch)
        monkeypatch.setattr(live, "commands_file_changed", lambda: False)
        monkeypatch.setattr(live, "words_file_changed", lambda: False)
        cfg = _cfg(auto_timeout_enabled=False, auto_timeout_minutes=5)
        svc = app._ServiceState(cfg)
        svc.last_activity = 0.0       # no D-Bus command since the epoch
        svc.last_config_check = 0.0

        def reload(c, indicator):
            c.auto_timeout_enabled = True   # what Preferences just saved
            return None

        monkeypatch.setattr(live, "config_file_changed", lambda: True)
        monkeypatch.setattr(app, "_reload_live_settings", reload)
        monkeypatch.setattr(svc, "refresh_input_device", lambda: None)
        app._service_tick(cfg, svc)

        loaded = _Engine()
        monkeypatch.setattr(engine, "model", loaded)
        monkeypatch.setattr(live, "config_file_changed", lambda: False)
        app._service_tick(cfg, svc)
        assert engine.model is loaded    # not rested on the very next pass


def test_power_saving_rests_the_model_and_keeps_the_service(monkeypatch):
    """Mio, Lemmy 2026-10-17: dictation 'just stops after a while'. Power
    Saving used to end the service, and F8 did nothing until dictation was
    turned back on from the menu. Now it lets go of the model and stays."""
    _idle(monkeypatch)
    monkeypatch.setattr(live, "config_file_changed", lambda: False)
    shown = []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: shown.append((title, body)))
    loaded = _Engine()
    monkeypatch.setattr(engine, "model", loaded)
    cfg = _cfg(auto_timeout_enabled=True, auto_timeout_minutes=5)
    svc = app._ServiceState(cfg)
    svc.last_activity = 0.0
    app._service_tick(cfg, svc)           # no SystemExit: the hotkeys stay
    assert engine.model is None and loaded.closed
    assert shown == []                    # nothing to explain: F8 still works


def test_power_saving_waits_for_a_transcription_still_running(monkeypatch):
    """Codex review of 0.17: the idle clock starts when the key comes up, so a
    long transcription or Type at pause chunks still typing could outlast a
    1-minute timeout and lose the Vulkan engine mid-request."""
    _idle(monkeypatch)
    monkeypatch.setattr(live, "config_file_changed", lambda: False)
    loaded = _Engine()
    monkeypatch.setattr(engine, "model", loaded)
    cfg = _cfg(auto_timeout_enabled=True, auto_timeout_minutes=1)
    svc = app._ServiceState(cfg)
    svc.last_activity = 0.0
    with type_at_pause.inject_lock:                # a dictation is being transcribed
        app._service_tick(cfg, svc)
    assert engine.model is loaded and not loaded.closed
    app._service_tick(cfg, svc)           # done: now it rests
    assert engine.model is None


def test_the_next_dictation_loads_the_model_again(monkeypatch):
    built = []
    monkeypatch.setattr(engine, "model", None)
    monkeypatch.setattr(engine, "load_config", lambda: "settings")
    monkeypatch.setattr(engine, "build",
                        lambda settings: built.append(settings) or _Engine())
    monkeypatch.setattr(engine, "notify_tray", lambda: None)
    first = engine.wake()
    assert isinstance(first, _Engine) and built == ["settings"]
    assert engine.wake() is first and len(built) == 1     # loaded once


def test_a_model_that_wont_load_again_is_said_not_raised(monkeypatch):
    """Raised in a dictation's thread, it would end only the thread, leaving a
    service without a model. And no other model is saved over the user's
    choice: a graphics card failing once on waking shouldn't change it."""
    shown = []
    monkeypatch.setattr(engine, "model", None)
    monkeypatch.setattr(engine, "load_config", lambda: "settings")
    monkeypatch.setattr(engine, "build",
                        lambda s: (_ for _ in ()).throw(engine.ModelUnavailable("not downloaded")))
    monkeypatch.setattr(engine, "build_or_exit", lambda s: pytest.fail("fell back"))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: shown.append(body))
    assert engine.wake() is None
    assert shown and "Restart Service" in shown[0]
