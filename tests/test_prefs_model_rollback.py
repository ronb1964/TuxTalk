"""A model whose download did not finish must not stay in config.toml.

Apply/OK save the config *before* downloading, because the downloader reads the
model name out of the merged config. That ordering is deliberate, but it leaves
a gap: cancel or fail the download and config.toml still names the new model,
while the running service is loaded with the old one. Model changes are
deliberately excluded from the live-settings reload, so nothing else corrects it.

save_config() also resets _config_at_open to what it just wrote, so without the
rollback a retry would see no change at all: Preferences would report "your
changes are already in effect" and skip the restart, leaving the service on
the old model indefinitely.

These tests drive on_apply/on_ok with a stub `self`, and the real rollback of
the speech settings, without constructing a GTK window. The failure path
returns before any dialog is built, so no display is needed.
"""

from types import SimpleNamespace


class StubPrefs:
    """Only what the Apply/OK ordering touches.

    save_config() mirrors the real one's two side effects: the config is
    written, and the baseline is reset to what was written.
    """

    def __init__(self, on_disk_model="small", chosen_model="large-v3",
                 on_disk_device="cpu", chosen_device="cpu"):
        self._config_at_open = {"model": on_disk_model, "device": on_disk_device, "beeps": True}
        self.config = {"model": chosen_model, "device": chosen_device, "beeps": True}
        self.saved_models = []
        self.download_result = False  # did the download succeed
        self.restart_calls = 0
        self.words_tab = SimpleNamespace(save=lambda: True)     # snippets saved
        self.on_disk = {}           # what the tray saved meanwhile
        self._restart_owed = set()
        self.speech = _speech(self, chosen_model, chosen_device)
        self.general_tab = SimpleNamespace(speech=self.speech, show_saved=lambda: None,
                                           after_hotkey_change=lambda changed, since: False)

    # --- real collaborators, stubbed ---

    def save_config(self):
        self.saved_models.append(self.config["model"])
        self._saved_before = {**self._config_at_open, **self.on_disk}
        self._config_at_open = dict(self.config)
        return True

    def _changed_since_open(self):
        # The real implementation: the rollback depends on this baseline
        # behaviour, so it must not be stubbed out with something simpler.
        from tuxtalk.prefs import PreferencesWindow

        return PreferencesWindow._changed_since_open(self)

    def _save_and_download(self):
        from tuxtalk.prefs import PreferencesWindow

        return PreferencesWindow._save_and_download(self)

    def _apply_or_restart(self, changed):
        self.restart_calls += 1
        return True

    def put_in_force(self, changed):
        from tuxtalk.prefs import PreferencesWindow

        return PreferencesWindow.put_in_force(self, changed)

    # --- helpers the tests read ---

    @property
    def model_on_disk(self):
        """The last model save_config() actually persisted."""
        return self.saved_models[-1] if self.saved_models else None


def _speech(win, model, device):
    """The real speech settings (rollback_download and set_choice are the
    code under test), with stand-in dropdowns and no download."""
    from tuxtalk.prefs_speech import SpeechSection

    speech = SpeechSection.__new__(SpeechSection)
    speech.win = win
    speech._updating_model = False
    speech.download_selected_model = lambda: win.download_result

    def no_dialogs(combo):
        raise AssertionError("the device dialogs ran during a rollback")
    speech._on_device_changed = no_dialogs
    speech.model_combo = FakeCombo(speech, model)
    speech.device_combo = FakeDeviceCombo(speech, device)
    return speech


def _changed_since_open(stub):
    from tuxtalk.config import changed_keys

    return changed_keys(stub._config_at_open, stub.config)


class TestApply:
    def test_a_cancelled_download_leaves_the_previous_model_on_disk(self, prefs_cls):
        """Cancel the 3 GB download and config must still name the live model."""
        stub = StubPrefs(on_disk_model="small", chosen_model="large-v3")
        stub.download_result = False

        prefs_cls.on_apply(stub, None)

        assert stub.model_on_disk == "small"

    def test_a_cancelled_download_does_not_restart_the_service(self, prefs_cls):
        stub = StubPrefs()
        stub.download_result = False

        prefs_cls.on_apply(stub, None)

        assert stub.restart_calls == 0

    def test_after_a_cancelled_download_a_retry_still_sees_a_change(self, prefs_cls):
        """Once rolled back, re-picking the model must register as a change so the
        next Apply actually restarts the service onto it.
        """
        stub = StubPrefs(on_disk_model="small", chosen_model="large-v3")
        stub.download_result = False

        prefs_cls.on_apply(stub, None)

        # The user picks it again and this time the download works.
        stub.config["model"] = "large-v3"
        assert "model" in _changed_since_open(stub)


class TestOk:
    def test_a_cancelled_download_leaves_the_previous_model_on_disk(self, prefs_cls):
        stub = StubPrefs(on_disk_model="small", chosen_model="large-v3")
        stub.download_result = False

        prefs_cls.on_ok(stub, None)

        assert stub.model_on_disk == "small"


class TestRollbackIsNarrow:
    def test_an_unrelated_setting_survives_the_rollback(self, prefs_cls):
        """Only the model is undone — the user's other edits were saved fine."""
        stub = StubPrefs(on_disk_model="small", chosen_model="large-v3")
        stub.config["beeps"] = False
        stub.download_result = False

        prefs_cls.on_apply(stub, None)

        assert stub.config["beeps"] is False

    def test_no_second_write_when_the_model_did_not_change(self, prefs_cls):
        """A failed download of the already-selected model has nothing to undo."""
        stub = StubPrefs(on_disk_model="small", chosen_model="small")
        stub.download_result = False

        prefs_cls.on_apply(stub, None)

        assert stub.saved_models == ["small"]


class FakeCombo:
    """Records the dropdown's choice, and whether the change handler was muted."""

    def __init__(self, owner, active):
        self.owner, self.active, self.muted = owner, active, None

    def set_active_id(self, model):
        self.active = model
        self.muted = getattr(self.owner, "_updating_model", False)


class TestDropdownFollowsTheRollback:
    def test_the_dropdown_goes_back_to_the_model_in_use(self, prefs_cls):
        """After a cancelled Medium download the dropdown must not stay on
        Medium while the service keeps running the old model."""
        stub = StubPrefs(on_disk_model="parakeet-v3", chosen_model="medium")
        stub.download_result = False

        prefs_cls.on_apply(stub, None)

        assert stub.speech.model_combo.active == "parakeet-v3"
        assert stub.speech.model_combo.muted is True  # no model-picked dialogs
        assert stub.speech._updating_model is False
        assert stub.speech._last_selected_model == "parakeet-v3"


class FakeDeviceCombo:
    """The Device dropdown: its change handler must stay blocked while it is
    put back, or switching to Vulkan would start its setup dialogs."""

    def __init__(self, owner, active):
        self.owner, self.active, self.blocked = owner, active, False

    def handler_block_by_func(self, func):
        self.blocked = True

    def handler_unblock_by_func(self, func):
        self.blocked = False

    def set_active_id(self, device):
        if not self.blocked:
            self.owner._on_device_changed(self)
        self.active = device


class TestADeviceChangeIsRolledBackToo:
    """Device from Vulkan to CPU, OK, cancel the download of the processor
    copy of Parakeet, OK again: the retry must see a change, or config.toml
    says "cpu" while dictation keeps running on the graphics card."""

    def _stub(self):
        stub = StubPrefs(on_disk_model="parakeet-v3", chosen_model="parakeet-v3",
                         on_disk_device="vulkan", chosen_device="cpu")
        stub.download_result = False
        return stub

    def test_a_cancelled_download_puts_the_device_back(self, prefs_cls):
        stub = self._stub()
        prefs_cls.on_ok(stub, None)
        assert stub.config["device"] == "vulkan"
        assert stub._config_at_open["device"] == "vulkan"   # what's on disk
        assert stub.speech.device_combo.active == "vulkan"
        assert stub.speech.device_combo.blocked is False

    def test_the_retry_still_restarts_onto_the_new_device(self, prefs_cls):
        stub = self._stub()
        prefs_cls.on_ok(stub, None)
        stub.config["device"] = "cpu"     # the user picks CPU again
        assert "device" in _changed_since_open(stub)


class TrayChangedTheModel(StubPrefs):
    """The tray switched to Parakeet while Preferences was open: the save
    merges like the real one, keeping the tray's model unless this window
    changed it too."""

    def save_config(self):
        from tuxtalk.config import merge_changed_keys
        self.on_disk = {"model": "parakeet-v3"}
        base = {**self._config_at_open, **self.on_disk}
        merged = merge_changed_keys(self._config_at_open, self.config, dict(base))
        self._saved_before, self._config_at_open = base, dict(merged)
        self.config = dict(merged)
        self.saved_models.append(self.config["model"])
        return True


class TestTheTraysChoiceSurvivesTheRollback:
    def test_a_failed_device_download_leaves_the_trays_model(self, prefs_cls):
        """Only the device was changed here, so only the device goes back."""
        stub = TrayChangedTheModel(on_disk_model="small", chosen_model="small",
                                   on_disk_device="cpu", chosen_device="vulkan")
        stub.download_result = False

        prefs_cls.on_apply(stub, None)

        assert stub.config == {"model": "parakeet-v3", "device": "cpu", "beeps": True}
        assert stub.model_on_disk == "parakeet-v3"

    def test_a_failed_model_download_goes_back_to_the_trays_model(self, prefs_cls):
        """The tray switched to Parakeet, then this window picked Medium: a
        failed download leaves the model the service runs, not the one at
        open."""
        stub = TrayChangedTheModel(on_disk_model="small", chosen_model="medium")
        stub.download_result = False

        prefs_cls.on_apply(stub, None)

        assert stub.model_on_disk == "parakeet-v3"
        assert stub.speech.model_combo.active == "parakeet-v3"


class TestAFailedRestartIsStillOwed:
    def test_the_next_apply_restarts_rather_than_say_its_in_effect(self, prefs_cls,
                                                                    monkeypatch):
        """The save moved the baseline on, so the second Apply found nothing
        changed and said "already in effect" though nothing had restarted."""
        from tuxtalk import prefs
        monkeypatch.setattr(prefs, "show_message", lambda *a, **k: None)
        stub = StubPrefs(on_disk_model="small", chosen_model="medium")
        stub.download_result = True
        restarted = iter([False, False, True])
        asked = []
        stub._apply_or_restart = lambda changed: asked.append(set(changed)) or next(restarted)
        stub._say_restart_failed = lambda: None
        stub.window = None

        for _apply in range(3):
            prefs_cls.on_apply(stub, None)

        assert asked == [{"model"}, {"model"}, {"model"}]
        assert prefs_cls._changed_since_open(stub) == set()     # done now
