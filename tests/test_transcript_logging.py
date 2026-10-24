"""Privacy: transcribed text is kept out of the on-disk log by default.

TuxTalk logs every raw/normalized transcription to ~/.config/tuxtalk/tuxtalk.log
for troubleshooting. That is a standing plaintext record of everything the user
dictates. It must be redacted by default and only revealed when the user opts in
via the `log_transcripts` setting (which pops a warning before it turns on).
"""
import gi
import pytest

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from gtk_text import find, shown_text  # noqa: E402
from tuxtalk import config  # noqa: E402
from tuxtalk import logger as tlog  # noqa: E402


# --- config flag -----------------------------------------------------------

def test_log_transcripts_defaults_off():
    assert config.Settings().log_transcripts is False


def test_log_transcripts_applies_live_no_restart():
    """Toggling it in Preferences should take effect without a service restart."""
    assert "log_transcripts" in config.LIVE_APPLIED_KEYS
    assert "log_transcripts" not in config.RESTART_REQUIRED_KEYS


# --- redaction helper ------------------------------------------------------

def test_redact_hides_content_but_keeps_length_by_default():
    assert tlog.redact_text("hello world") == "[hidden, 11 chars]"


def test_redact_reveals_full_repr_when_opted_in():
    # The repr form matches `{text!r}` logging exactly, so an opted-in log
    # reads the same as plain repr logging.
    assert tlog.redact_text("hello world", reveal=True) == "'hello world'"


def test_redact_is_robust_to_non_strings():
    assert tlog.redact_text(None) == "[hidden]"


# --- app wiring: the service's live flag gates redaction -------------------

def test_app_defaults_to_not_logging_transcripts():
    from tuxtalk import app
    assert app._log_transcripts is False


def test_app_loggable_respects_the_live_flag(monkeypatch):
    from tuxtalk import app
    monkeypatch.setattr(app, "_log_transcripts", False)
    assert app._loggable("secret words") == "[hidden, 12 chars]"
    monkeypatch.setattr(app, "_log_transcripts", True)
    assert app._loggable("secret words") == "'secret words'"


# --- end-to-end: a real logging site keeps the words out of the log --------

def test_custom_command_log_line_redacts_the_text(monkeypatch, caplog):
    """Drives a real logging site: with the flag off, the emitted log record
    must not contain the dictated words, only the redacted breadcrumb. Guards
    against a future edit reverting `{_loggable(x)}` back to `{x!r}`."""
    import logging
    from tuxtalk import app
    monkeypatch.setattr(app, "_log_transcripts", False)
    monkeypatch.setattr(app, "_custom_commands", {"banana": "apple"})
    secret = "my banana passphrase"
    with caplog.at_level(logging.INFO, logger="tuxtalk.app"):
        app._apply_custom_commands(secret)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Custom commands applied" in logged   # the line did fire
    assert "banana" not in logged                 # ...but the words are gone
    assert "[hidden," in logged


# --- prefs UI: opt-in checkbox with a warning gate -------------------------

@pytest.fixture
def dialogs(monkeypatch):
    """Answers every dialog Preferences runs with dialogs.answer, and keeps
    each one's message type."""
    class Dialogs(list):
        answer = Gtk.ResponseType.OK
    seen = Dialogs()

    def run(dialog):
        seen.append(dialog.props.message_type)
        return seen.answer
    monkeypatch.setattr(Gtk.Dialog, "run", run)
    return seen


def _log_check(window):
    return find(window.window, Gtk.CheckButton, "Log transcribed text (for troubleshooting)")


def test_prefs_has_log_transcripts_checkbox(prefs_window, dialogs):
    window = prefs_window()
    check = _log_check(window)
    assert not check.get_active()
    check.set_active(True)
    assert window.config["log_transcripts"] is True
    check.set_active(False)
    assert window.config["log_transcripts"] is False


def test_enabling_logging_is_gated_by_a_warning_dialog(prefs_window, dialogs):
    """Turning ON transcript logging must confirm via a WARNING dialog before it
    writes the config, so the user is fully aware their dictation will be saved."""
    window = prefs_window()
    dialogs.answer = Gtk.ResponseType.CANCEL
    check = _log_check(window)
    check.set_active(True)
    assert dialogs == [Gtk.MessageType.WARNING]
    assert not check.get_active() and window.config["log_transcripts"] is False


def test_injection_tooltip_tells_the_truth_about_password_fields(prefs_window):
    """0.18 recognises password fields; the tooltip had kept saying nothing
    can detect them."""
    window = prefs_window()
    window.select_tab("advanced")
    find(window.window, Gtk.Button, "Show fine-tuning").clicked()
    shown = "\n".join(shown_text(window.window))
    assert "it cannot detect password fields" not in shown
    assert "doesn't type into password fields" in shown


# --- the enable gate is enforced (drives the real handler) -----------------

def _fake_check(active):
    import types
    reverted = []
    check = types.SimpleNamespace(
        get_active=lambda: active,
        set_active=lambda v: reverted.append(v),
        handler_block_by_func=lambda f: None,
        handler_unblock_by_func=lambda f: None,
    )
    return check, reverted


def _install_fake_dialog(monkeypatch, response):
    from tuxtalk import prefs
    seen = {}

    class _FakeStyle:
        def add_class(self, *a):
            pass

    class _FakeButton:
        def get_style_context(self):
            return _FakeStyle()

    class _FakeDialog:
        def __init__(self, **kw):
            seen["message_type"] = kw.get("message_type")
            seen["transient_for"] = kw.get("transient_for")

        def format_secondary_text(self, t):
            seen["secondary"] = t

        def add_button(self, label, resp):
            seen.setdefault("buttons", []).append(label)
            return _FakeButton()

        def set_default_response(self, r):
            seen["default"] = r

        def run(self):
            return response

        def destroy(self):
            pass

    monkeypatch.setattr(prefs, "message_dialog", _FakeDialog)
    return seen


def test_checking_the_box_without_confirming_does_not_enable_logging(monkeypatch):
    import types
    from tuxtalk import prefs
    seen = _install_fake_dialog(monkeypatch, prefs.Gtk.ResponseType.CANCEL)
    saved = []
    window = object()  # stands in for the real Gtk.Window at self.window
    self = types.SimpleNamespace(update_config=lambda k, v: saved.append((k, v)),
                                 window=window,
                                 _on_log_transcripts_toggled=lambda *a: None)
    check, reverted = _fake_check(active=True)

    prefs.PreferencesWindow._on_log_transcripts_toggled(self, check)

    assert seen["message_type"] == prefs.Gtk.MessageType.WARNING  # a warning was shown
    # The dialog must be parented to the real window, not the wrapper — passing
    # the wrapper makes GTK reject it and no modal ever appears.
    assert seen["transient_for"] is window
    assert ("log_transcripts", True) not in saved                 # cancel → not enabled
    assert reverted == [False]                                    # box snapped back off


def test_confirming_the_warning_enables_logging(monkeypatch):
    import types
    from tuxtalk import prefs
    _install_fake_dialog(monkeypatch, prefs.Gtk.ResponseType.OK)
    saved = []
    self = types.SimpleNamespace(update_config=lambda k, v: saved.append((k, v)),
                                 window=object(),
                                 _on_log_transcripts_toggled=lambda *a: None)
    check, _ = _fake_check(active=True)

    prefs.PreferencesWindow._on_log_transcripts_toggled(self, check)

    assert ("log_transcripts", True) in saved                     # confirmed → enabled


def test_unchecking_needs_no_dialog_and_disables(monkeypatch):
    import types
    from tuxtalk import prefs
    # If a dialog were constructed while unchecking, this would blow up.
    monkeypatch.setattr(prefs, "message_dialog",
                        lambda **k: (_ for _ in ()).throw(AssertionError("no dialog on uncheck")))
    saved = []
    self = types.SimpleNamespace(update_config=lambda k, v: saved.append((k, v)),
                                 _on_log_transcripts_toggled=lambda *a: None)
    check, _ = _fake_check(active=False)

    prefs.PreferencesWindow._on_log_transcripts_toggled(self, check)

    assert ("log_transcripts", False) in saved


# --- clearing the log ------------------------------------------------------

def test_the_log_folder_follows_dev_mode(monkeypatch):
    # The suite redirects logs via TUXTALK_LOG_DIR (tests/conftest.py); this
    # checks the normal rule, so switch that off. Only paths are computed.
    monkeypatch.delenv("TUXTALK_LOG_DIR", raising=False)
    monkeypatch.setenv("DEV_MODE", "1")
    assert tlog._log_dir().name == "tuxtalk-dev"
    monkeypatch.delenv("DEV_MODE", raising=False)
    assert tlog._log_dir().name == "tuxtalk"


def test_log_dir_override_sets_the_location(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_MODE", "1")
    monkeypatch.setenv("TUXTALK_LOG_DIR", str(tmp_path))
    assert tlog._log_dir() == tmp_path


def test_clear_log_empties_main_and_removes_backup(tmp_path):
    main = tmp_path / "tuxtalk.log"
    main.write_text("secret dictation history\n")
    old = tmp_path / "tuxtalk.log.old"
    old.write_text("older secrets\n")

    tlog.clear_log(tmp_path)

    # Truncated (not deleted) so a service holding it open keeps appending.
    assert main.exists() and main.read_text() == ""
    assert not old.exists()


def test_clear_log_says_when_it_could_not_erase(tmp_path):
    """Preferences said "Log cleared." even when the log stayed on disk."""
    (tmp_path / "tuxtalk.log").mkdir()          # can't be truncated
    assert tlog.clear_log(tmp_path) is False


def test_a_quoted_false_turns_a_setting_off_not_on():
    """bool("false") is True: a hand-edited log_transcripts = "false" wrote
    every dictation to the log."""
    s = config.Settings()
    assert config._apply_toml(s, {"log_transcripts": "false", "beeps": "TRUE"}) == []
    assert s.log_transcripts is False and s.beeps is True
    assert config._apply_toml(s, {"beeps": "loud"}) == ["beeps"]


def test_prefs_has_clear_log_button(prefs_window, dialogs, monkeypatch):
    cleared = []
    monkeypatch.setattr(tlog, "clear_log", lambda: cleared.append(True) or True)
    window = prefs_window()
    dialogs.answer = Gtk.ResponseType.CANCEL
    find(window.window, Gtk.Button, "Clear log now").clicked()
    assert cleared == []
    dialogs.answer = Gtk.ResponseType.OK
    find(window.window, Gtk.Button, "Clear log now").clicked()
    assert cleared == [True]
    assert dialogs[0] == Gtk.MessageType.WARNING


# --- whole dictations: no path writes the words ----------------------------

SECRET = ("zanzibar", "quokka", "hunter")


def _leaks(caplog, capsys):
    """The secret words found in the log records or on stdout and stderr."""
    out = capsys.readouterr()
    everything = "\n".join([caplog.text, out.out, out.err]).lower()
    return [word for word in SECRET if word in everything]


def _hear_every_level(caplog):
    """Capture DEBUG lines too, from every TuxTalk module. setup_logger()
    pins each module's logger at INFO, so lowering only the package logger
    left their DEBUG lines unwritten, and unchecked."""
    import logging
    for name in list(logging.root.manager.loggerDict):
        if name == "tuxtalk" or name.startswith("tuxtalk."):
            caplog.set_level(logging.DEBUG, logger=name)


def test_a_dictation_never_writes_the_words(monkeypatch, caplog, capsys, keyboard):
    """Every log line and print on the way from the recording to the typed
    text, with transcript logging off (the default)."""
    import numpy as np
    from tuxtalk import app
    monkeypatch.setattr(app, "_log_transcripts", False)
    monkeypatch.setattr(app, "_transcribe_audio",
                        lambda audio, language: "zanzibar quokka password hunter two")
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "org.kde.kwrite")
    frames = [(np.ones(app.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    _hear_every_level(caplog)
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, beeps_on=False,
                               smart_quotes=False, notify_on=False)
    assert keyboard.typed_keys(), "the dictation was not typed"
    assert "[hidden" in caplog.text                     # it did log, redacted
    assert _leaks(caplog, capsys) == []


def test_a_voice_edit_never_writes_the_words(monkeypatch, caplog, capsys):
    """The selection, the spoken instruction and the AI's rewrite."""
    import numpy as np
    from tuxtalk import app
    from tuxtalk import clipboard as cb
    typed = []
    engine = type("Engine", (), {"is_ready": lambda self: True,
                                 "edit": lambda self, text, instruction: "Quokka hunter, formally."})()
    monkeypatch.setattr(app, "_log_transcripts", False)
    monkeypatch.setattr(app, "_ai_engine", engine)
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "make the zanzibar formal")
    monkeypatch.setattr("tuxtalk.silence.is_speechless_tap", lambda a, sr: False)
    monkeypatch.setattr(cb, "copy_selection",
                        lambda send_copy: ("zanzibar quokka hunter", lambda also=(): None))
    monkeypatch.setattr(app, "_inject_text", lambda text, mode, one_block=False:
                        typed.append(text) or "ok")
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "EDIT_RESTORE_DELAY_S", 0)
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "org.kde.kwrite")
    monkeypatch.setattr(app, "_is_wayland_session", lambda: True)
    frames = [np.full(16000, 1000, dtype=np.int16).tobytes()]
    _hear_every_level(caplog)
    app._transcribe_and_edit(frames, 16000, False, "", "type")
    assert typed == ["Quokka hunter, formally."]
    assert _leaks(caplog, capsys) == []


def _engine(kind, monkeypatch):
    """A loaded speech model of each kind the service runs, hearing the
    secret: TuxTalk's own engine code is real, and only what it drives (the
    ONNX model, the graphics helper process, the whisper-server reply,
    faster-whisper) is faked."""
    import io
    import json
    import threading
    import types
    from tuxtalk import whisper_vulkan
    from tuxtalk.parakeet_engine import ParakeetModel
    from tuxtalk.parakeet_gpu import ParakeetGpuModel
    heard = "zanzibar quokka password hunter two"
    running = types.SimpleNamespace(poll=lambda: None, stdin=io.BytesIO())
    if kind == "parakeet":
        engine = ParakeetModel.__new__(ParakeetModel)
        engine._model = types.SimpleNamespace(recognize=lambda audio, sample_rate: heard)
    elif kind == "parakeet on the graphics chip":
        engine = ParakeetGpuModel.__new__(ParakeetGpuModel)
        engine._lock, engine._cpu, engine._proc = threading.Lock(), None, running
        engine._words = engine._sent_words = ()
        engine._read_reply = lambda timeout: {"text": heard}
    elif kind == "whisper on vulkan":
        engine = whisper_vulkan.VulkanWhisperModel.__new__(
            whisper_vulkan.VulkanWhisperModel)
        engine._lock, engine._proc, engine._port = threading.RLock(), running, 1
        reply = json.dumps({"segments": [{"text": heard, "no_speech_prob": 0.0}]}).encode()
        monkeypatch.setattr(whisper_vulkan.urllib.request, "urlopen",
                            lambda request, timeout: io.BytesIO(reply))
    else:
        segment = types.SimpleNamespace(text=heard, no_speech_prob=0.0)
        info = types.SimpleNamespace(language="en", all_language_probs=None)
        engine = types.SimpleNamespace(transcribe=lambda audio, **k: ([segment], info))
    return engine


@pytest.mark.parametrize("kind", ["parakeet", "parakeet on the graphics chip",
                                  "whisper on vulkan", "faster-whisper"])
def test_no_speech_engine_writes_the_words(monkeypatch, caplog, capsys, kind):
    import numpy as np
    from tuxtalk import app
    monkeypatch.setattr(app, "_log_transcripts", False)
    monkeypatch.setattr(app, "model", _engine(kind, monkeypatch))
    _hear_every_level(caplog)
    raw = app._transcribe_audio(np.full(16000, 0.2, dtype=np.float32), language=None)
    assert raw and "quokka" in raw
    assert _leaks(caplog, capsys) == []
