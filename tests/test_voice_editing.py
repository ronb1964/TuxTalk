"""Voice editing: select text, hold Shift + the dictation key, say how to
change it ("make this more formal"), and the selection is rewritten by the
local AI model.
"""
import pytest

from tuxtalk import clipboard as cb


# --- 1. grabbing the selection ------------------------------------------------------

class FakeBoard:
    """The Wayland clipboard as wl-paste / wl-copy see it, plus an app that
    copies *selected* to it when it gets Ctrl+C (None = nothing selected)."""

    def __init__(self, data=b"what I copied", selected="the selected text"):
        self.data, self.mime = data, "text/plain;charset=utf-8"
        self.selected = selected
        self.sensitive_writes = []

    def install(self, monkeypatch):
        monkeypatch.setattr(cb, "_wl_paste_types",
                            lambda: [] if self.data is None else [self.mime])
        monkeypatch.setattr(cb, "_wl_paste", lambda mime: self.data)
        monkeypatch.setattr(cb, "_wl_copy_bytes", self.write)
        monkeypatch.setattr(cb, "_wl_clear", lambda: self.write(None, None))
        monkeypatch.setattr(cb, "_wl_copy_sensitive", self.write_sensitive)

    def write(self, data, mime, sensitive=False):
        self.data, self.mime = data, mime or self.mime
        self.written_sensitive = sensitive

    def write_sensitive(self, text):
        self.sensitive_writes.append(text)
        self.data = text.encode()

    def ctrl_c(self):
        if self.selected is not None:
            self.data = self.selected.encode()
        return True


def test_returns_the_selection_and_puts_the_clipboard_back(monkeypatch):
    board = FakeBoard()
    board.install(monkeypatch)
    text, put_back = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    assert text == "the selected text"
    put_back()
    assert board.data == b"what I copied"


def test_nothing_selected_is_none_and_the_clipboard_is_back(monkeypatch):
    board = FakeBoard(selected=None)
    board.install(monkeypatch)
    text, put_back = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    assert text is None
    assert board.data == b"what I copied"          # already restored


def test_the_probe_marker_is_kept_out_of_clipboard_history(monkeypatch):
    board = FakeBoard()
    board.install(monkeypatch)
    cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    assert board.sensitive_writes and board.sensitive_writes[0].startswith("tuxtalk-")


def test_an_empty_clipboard_is_left_empty(monkeypatch):
    board = FakeBoard(data=None)
    board.install(monkeypatch)
    text, put_back = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    put_back()
    assert text == "the selected text" and board.data is None


def test_a_slow_app_is_waited_for(monkeypatch):
    board = FakeBoard()
    board.install(monkeypatch)
    waits = []

    def slow_ctrl_c():
        return True                                # copies only after a wait

    def sleep(s):
        waits.append(s)
        if len(waits) == 3:
            board.data = b"late selection"
    text, _ = cb.copy_selection(slow_ctrl_c, sleep=sleep)
    assert text == "late selection"


def test_a_failed_keystroke_means_no_selection(monkeypatch):
    board = FakeBoard()
    board.install(monkeypatch)
    text, _ = cb.copy_selection(lambda: False, sleep=lambda s: None)
    assert text is None and board.data == b"what I copied"


# --- 2. the AI engine edits -----------------------------------------------------------

import io
import json

from tuxtalk import ai_cleanup


class Reply:
    def __init__(self, content):
        self.body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    def __enter__(self):
        return io.BytesIO(self.body)

    def __exit__(self, *a):
        return False


@pytest.fixture
def engine(monkeypatch):
    sent = []
    e = ai_cleanup.CorrectionEngine()
    e._port, e._key = 1234, "k"
    e._ready.set()

    def urlopen(request, timeout):
        sent.append((json.loads(request.data), timeout))
        return Reply("  We completed the electrical work today.  ")
    monkeypatch.setattr(ai_cleanup.urllib.request, "urlopen", urlopen)
    e.sent = sent
    return e


def test_edit_sends_the_text_and_instruction_with_examples(engine):
    out = engine.edit("We finished the electrical today.", "make it sound more professional")
    assert out == "We completed the electrical work today."
    body, _ = engine.sent[0]
    msgs = body["messages"]
    assert msgs[0]["role"] == "system" and "never answer it" in msgs[0]["content"]
    assert len(msgs) == 1 + 2 * len(ai_cleanup.EDIT_EXAMPLES) + 1
    assert msgs[-1]["content"] == ("TEXT:\nWe finished the electrical today.\n\n"
                                   "INSTRUCTION: make it sound more professional")
    assert body["temperature"] == 0


def test_edit_allows_room_for_a_longer_answer(engine):
    engine.edit("x" * 2000, "turn this into a bullet list")
    body, timeout = engine.sent[0]
    assert body["max_tokens"] >= 1000 and timeout >= 20


def test_edit_waits_for_the_engine_to_be_ready(engine):
    engine._ready.clear()
    assert engine.edit("text", "shorten this") is None
    assert engine.sent == []


def test_the_engine_has_room_for_a_selection(monkeypatch):
    """2048 tokens held the self-correction prompt; an edit carries up to
    MAX_EDIT_CHARS of text plus the answer."""
    import types
    started = []
    engine = ai_cleanup.CorrectionEngine()
    monkeypatch.setattr(ai_cleanup, "is_installed", lambda: True)
    monkeypatch.setattr(ai_cleanup, "kill_orphaned_engines", lambda: 0)
    monkeypatch.setattr(ai_cleanup, "_server_binary", lambda: "/x/llama-server")
    monkeypatch.setattr(ai_cleanup, "_model_path", lambda: "/x/model.gguf")
    monkeypatch.setattr(ai_cleanup.subprocess, "Popen",
                        lambda argv, **k: started.append(argv) or types.SimpleNamespace(pid=42))
    monkeypatch.setattr(engine, "_wait_until_ready", lambda: None)
    engine.start()
    argv = started[0]
    assert int(argv[argv.index("-c") + 1]) >= 4096


# --- 3. the setting and the engine --------------------------------------------------

from tuxtalk import app, config


class FakeEngine:
    started = 0

    def __init__(self):
        self.stopped = False

    def start(self):
        FakeEngine.started += 1

    def stop(self):
        self.stopped = True


@pytest.fixture
def no_engine(monkeypatch):
    monkeypatch.setattr(ai_cleanup, "CorrectionEngine", FakeEngine)
    monkeypatch.setattr(ai_cleanup, "is_installed", lambda: True)
    monkeypatch.setattr(app, "_ai_engine", None)
    monkeypatch.setattr(app, "_ai_corrections", False)
    monkeypatch.setattr(app, "_voice_editing", False)
    FakeEngine.started = 0


def test_off_by_default_and_applied_without_a_restart():
    assert config.Settings().voice_editing is False
    assert "voice_editing" in config.LIVE_APPLIED_KEYS


def test_voice_editing_alone_starts_the_engine_but_not_self_corrections(no_engine):
    app._apply_cleanup_settings(config.Settings(voice_editing=True))
    assert FakeEngine.started == 1
    assert app._voice_editing is True and app._ai_corrections is False


def test_self_corrections_are_skipped_while_only_voice_editing_is_on(no_engine, monkeypatch):
    app._apply_cleanup_settings(config.Settings(voice_editing=True))
    called = []
    monkeypatch.setattr(ai_cleanup, "fix_self_corrections", lambda t, e: called.append(t) or t)
    monkeypatch.setattr(app, "_custom_commands", {})
    app._prepare_text("order two boxes, no wait, three boxes", True, True, False)
    assert called == []


def test_the_engine_stops_when_both_are_off(no_engine):
    app._apply_cleanup_settings(config.Settings(voice_editing=True, ai_corrections=True))
    engine = app._ai_engine
    app._apply_cleanup_settings(config.Settings(voice_editing=False, ai_corrections=False))
    assert engine.stopped and app._ai_engine is None


def test_turning_one_off_keeps_the_engine_for_the_other(no_engine):
    app._apply_cleanup_settings(config.Settings(voice_editing=True, ai_corrections=True))
    engine = app._ai_engine
    app._apply_cleanup_settings(config.Settings(voice_editing=True, ai_corrections=False))
    assert app._ai_engine is engine and not engine.stopped


# --- 4. Shift + the dictation key, and the edit itself --------------------------------

from evdev import ecodes
import numpy as np

from tuxtalk import history

F8, F9 = ecodes.KEY_F8, ecodes.KEY_F9
SHIFT = ecodes.KEY_LEFTSHIFT


class Ev:
    def __init__(self, code, value):
        self.type, self.code, self.value = ecodes.EV_KEY, code, value


class Cfg:
    beeps = False
    notify = False
    smart_quotes = True
    language = ""
    auto_space = True
    auto_period = True
    injection_mode = "type"


@pytest.fixture
def keyloop(monkeypatch):
    """_handle_key_event with recording and grabs recorded, not performed."""
    seen = {"starts": [], "stops": 0, "grab_codes": []}
    monkeypatch.setattr(app, "_voice_editing", True)
    monkeypatch.setattr(app.state, "is_recording", False)
    monkeypatch.setattr(app.state, "edit_mode", False, raising=False)
    monkeypatch.setattr(app, "_grab_keyboards_hiding", lambda devs, codes: seen["grab_codes"].append(set(codes)))
    monkeypatch.setattr(app, "_release_all_grabs", lambda: None)

    def start(beeps, notify, idx, from_toggle=False, edit_mode=False):
        seen["starts"].append({"from_toggle": from_toggle, "edit_mode": edit_mode})
        app.state.is_recording = True
        app.state.edit_mode = edit_mode
        return True

    def stop(*a, **k):
        seen["stops"] += 1
        app.state.is_recording = False
    monkeypatch.setattr(app, "start_recording", start)
    monkeypatch.setattr(app, "stop_recording", stop)
    monkeypatch.setattr(app, "_toggle_pressed_while_recording", lambda beeps: False)
    return seen


def key(code, value, held):
    app._handle_key_event(Ev(code, value), "hold", F8, F9, None, held, [], Cfg(), 0)


def test_shift_f8_starts_an_edit_and_hides_shift_from_the_desktop(keyloop):
    key(F8, 1, {SHIFT})
    assert keyloop["starts"] == [{"from_toggle": False, "edit_mode": True}]
    assert keyloop["grab_codes"][0] == {F8, SHIFT}


def test_plain_f8_still_dictates(keyloop):
    key(F8, 1, set())
    assert keyloop["starts"][0]["edit_mode"] is False


def test_with_voice_editing_off_shift_f8_is_left_to_the_app(keyloop, monkeypatch):
    """The desktop doesn't hold Shift+F8 back then, so the app gets it: a
    dictation as well would answer one key twice."""
    monkeypatch.setattr(app, "_voice_editing", False)
    key(F8, 1, {SHIFT})
    key(F9, 1, {SHIFT})
    assert keyloop["starts"] == []


@pytest.mark.parametrize("voice_editing", [False, True])
@pytest.mark.parametrize("mods", [(), ("Shift",), ("Alt",), ("Ctrl",), ("Ctrl", "Shift"), ("Meta",)])
def test_a_dictation_key_starts_only_as_the_desktop_holds_it(keyloop, monkeypatch,
                                                              voice_editing, mods):
    """What KDE, GNOME, COSMIC and the X server keep from the app
    (kwin_hotkeys.wanted_shortcuts) is exactly what starts a dictation.
    Alt+F8, which resizes the window on KDE and GNOME, did both."""
    import types
    from tuxtalk import kwin_hotkeys
    codes = {"Shift": SHIFT, "Alt": ecodes.KEY_LEFTALT, "Ctrl": ecodes.KEY_LEFTCTRL,
             "Meta": ecodes.KEY_LEFTMETA}
    monkeypatch.setattr(app, "_voice_editing", voice_editing)
    cfg = types.SimpleNamespace(hotkey="F8", toggle_hotkey="F9", voice_commands_hotkey="",
                                paste_last_hotkey="", voice_editing=voice_editing)
    held = {qt for _name, _text, qt in kwin_hotkeys.wanted_shortcuts(cfg)}
    for code, name in ((F8, "F8"), (F9, "F9")):
        keyloop["starts"].clear()
        app.state.is_recording = False
        key(code, 1, {codes[m] for m in mods})
        assert bool(keyloop["starts"]) == ("+".join(mods + (name,)) in held), name


def test_shift_f9_edits_without_arming_hands_free(keyloop):
    key(F9, 1, {SHIFT})
    assert keyloop["starts"] == [{"from_toggle": False, "edit_mode": True}]


@pytest.fixture
def edit_env(monkeypatch):
    """_transcribe_and_edit with every outside part faked."""
    seen = {"notices": [], "typed": [], "history": [], "put_back": 0, "beeps": []}

    class Engine:
        ready = True
        answer = "We completed the electrical work today."
        asked = []

        def is_ready(self):
            return self.ready

        def edit(self, text, instruction):
            self.asked.append((text, instruction))
            return self.answer

    engine = Engine()
    monkeypatch.setattr(app, "_ai_engine", engine)
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "make it more professional")
    monkeypatch.setattr("tuxtalk.silence.is_speechless_tap", lambda a, sr: False)
    selection = {"text": "We finished the electrical today."}

    def copy_selection(send_copy):
        def put_back(also=()):
            seen["put_back"] += 1
        return selection["text"], put_back
    monkeypatch.setattr(cb, "copy_selection", copy_selection)
    monkeypatch.setattr(app, "_inject_text", lambda text, mode, t0, one_block=False: seen["typed"].append(text) or "ok")
    monkeypatch.setattr(app, "_notify", lambda title, msg: seen["notices"].append(msg))
    monkeypatch.setattr(app, "_beep", lambda on, *a: seen["beeps"].append(a))
    monkeypatch.setattr(history, "add_entry", lambda text: seen["history"].append(text))
    monkeypatch.setattr(app, "EDIT_RESTORE_DELAY_S", 0)
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "org.kde.kwrite")
    monkeypatch.setattr(app, "_is_wayland_session", lambda: True)
    seen["engine"], seen["selection"] = engine, selection
    return seen


def run_edit():
    frames = [np.full(16000, 1000, dtype=np.int16).tobytes()]
    app._transcribe_and_edit(frames, 16000, False, "", "type")


def test_an_edit_replaces_the_selection(edit_env):
    run_edit()
    assert edit_env["engine"].asked == [("We finished the electrical today.", "make it more professional")]
    assert edit_env["typed"] == ["We completed the electrical work today."]
    assert edit_env["history"] == ["We completed the electrical work today."]
    assert edit_env["put_back"] == 1 and edit_env["notices"] == []


def test_a_multi_line_rewrite_goes_in_as_one_block(edit_env, monkeypatch):
    """Line by line with Enter in between, KWrite continues each "- " list
    line and the dashes pile up ("- - 2", "- - - 3")."""
    calls = []
    monkeypatch.setattr(app, "_inject_text", lambda text, mode, t0, one_block=False: calls.append((text, one_block)) or "ok")
    edit_env["engine"].answer = "- Buy plywood\n- Call George"
    run_edit()
    assert calls == [("- Buy plywood\n- Call George", True)]


@pytest.mark.parametrize("setup, words", [
    (lambda env: env["selection"].update(text=None), "Select the text"),
    (lambda env: env["selection"].update(text="x" * 3001), "too much text"),
    (lambda env: setattr(env["engine"], "ready", False), "still starting"),
    (lambda env: setattr(env["engine"], "answer", None), "may already read that way"),
    (lambda env: setattr(env["engine"], "answer", "We finished the electrical today."), "may already read that way"),
])
def test_each_problem_says_what_to_do(edit_env, setup, words):
    setup(edit_env)
    run_edit()
    assert edit_env["typed"] == []
    assert len(edit_env["notices"]) == 1 and words in edit_env["notices"][0]
    assert edit_env["beeps"] == [app.CANCEL_BEEP]


def test_without_the_ai_downloaded_it_says_where_to_turn_it_on(edit_env, monkeypatch):
    monkeypatch.setattr(app, "_ai_engine", None)
    run_edit()
    assert "Preferences" in edit_env["notices"][0] and edit_env["typed"] == []


def test_in_a_password_field_the_selection_isnt_even_read(edit_env, monkeypatch):
    from tuxtalk import atspi_helper
    monkeypatch.setattr(atspi_helper, "focused_is_password", lambda: True)
    monkeypatch.setattr(cb, "copy_selection", lambda send_copy: pytest.fail("read the selection"))
    run_edit()
    assert edit_env["typed"] == [] and edit_env["history"] == []
    assert edit_env["engine"].asked == [] and edit_env["beeps"] == [app.CANCEL_BEEP]


def test_a_rewrite_is_not_typed_or_kept_if_the_focus_moved_to_a_password_field(edit_env,
                                                                                monkeypatch):
    from tuxtalk import atspi_helper
    answers = [False]       # a text box at the start, a password field by the end
    monkeypatch.setattr(atspi_helper, "focused_is_password",
                        lambda: answers.pop(0) if answers else True)
    run_edit()
    assert edit_env["typed"] == [] and edit_env["history"] == []
    assert edit_env["beeps"] == [app.CANCEL_BEEP] and edit_env["put_back"] == 1


def test_the_clipboard_comes_back_even_when_typing_fails(edit_env, monkeypatch):
    monkeypatch.setattr(app, "_inject_text", lambda text, mode, t0, one_block=False: (_ for _ in ()).throw(RuntimeError("boom")))
    run_edit()
    assert edit_env["put_back"] == 1


# --- 5. KDE and GNOME hold Shift + the keys back -----------------------------------------

from tuxtalk import kwin_hotkeys, gnome_hotkeys


def _keys(cfg):
    return [k for _n, _w, k in kwin_hotkeys.wanted_shortcuts(cfg)]


def test_shift_keys_are_claimed_only_with_voice_editing_on():
    on = config.Settings(hotkey="F8", toggle_hotkey="F9", voice_editing=True)
    off = config.Settings(hotkey="F8", toggle_hotkey="F9", voice_editing=False)
    assert {"Shift+F8", "Shift+F9"} <= set(_keys(on))
    assert "Shift+F8" not in _keys(off) and "Shift+F9" not in _keys(off)


def test_gnome_gets_them_as_accelerators():
    on = config.Settings(hotkey="F8", toggle_hotkey="F9", voice_editing=True)
    assert "<Shift>F8" in gnome_hotkeys.accelerators(on)


# --- 6. the Preferences switch -------------------------------------------------------------

def _prefs_stub(saved):
    import types
    from tuxtalk.prefs import PreferencesWindow
    stub = types.SimpleNamespace(update_config=lambda k, v: saved.append((k, v)), window=None)
    stub._untick_ai_feature = lambda check, key, handler: PreferencesWindow._untick_ai_feature(stub, check, key, handler)
    return stub, PreferencesWindow


class Box:
    def __init__(self, active):
        self.active = active

    def get_active(self):
        return self.active

    def set_active(self, v):
        self.active = v

    def handler_block_by_func(self, f):
        pass

    def handler_unblock_by_func(self, f):
        pass


def test_switching_on_with_the_ai_present_just_saves_it(monkeypatch):
    monkeypatch.setattr(ai_cleanup, "is_installed", lambda: True)
    saved = []
    stub, P = _prefs_stub(saved)
    P._toggle_ai_feature(stub, Box(True), "voice_editing", None, "?")
    assert saved == [("voice_editing", True)]


def test_switching_off_saves_off(monkeypatch):
    saved = []
    stub, P = _prefs_stub(saved)
    P._toggle_ai_feature(stub, Box(False), "voice_editing", None, "?")
    assert saved == [("voice_editing", False)]


def test_declining_the_download_unticks_only_this_box(monkeypatch):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    monkeypatch.setattr(ai_cleanup, "is_installed", lambda: False)
    monkeypatch.setattr(Gtk.Dialog, "run", lambda self: Gtk.ResponseType.CANCEL)
    saved = []
    stub, P = _prefs_stub(saved)
    box = Box(True)
    P._toggle_ai_feature(stub, box, "voice_editing", None, "Download the AI for voice editing?")
    assert box.active is False and saved == [("voice_editing", False)]


def _labs(window):
    """What Preferences shows under its TuxTalk Labs heading."""
    from gi.repository import Gtk
    from gtk_text import below, find
    return below(find(window.window, Gtk.Label, "🧪 TuxTalk Labs"))


def test_the_advanced_tab_has_the_switch(prefs_window, monkeypatch):
    from gi.repository import Gtk
    monkeypatch.setattr(ai_cleanup, "is_installed", lambda: True)
    window = prefs_window()
    switch = next(w for w in _labs(window) if isinstance(w, Gtk.CheckButton)
                  and w.get_label().startswith("Voice editing"))
    switch.set_active(True)
    assert window.config["voice_editing"] is True


# --- Shift release, the clipboard, terminals and window changes --------------------------

class FakeKeyboard:
    """A keyboard whose held keys change over successive active_keys() calls."""
    def __init__(self, held_sequence):
        self.seq = list(held_sequence)

    def active_keys(self):
        return self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]


def test_stopping_with_shift_waits_for_it_without_injecting_keys(keyloop, monkeypatch):
    """Injected key events replayed after a slow edit would start a new
    recording, so TuxTalk just waits for the real Shift release."""
    monkeypatch.setattr(app, "_is_keyboard_device", lambda d: True)
    kb = FakeKeyboard([[SHIFT], [SHIFT], []])
    key(F9, 1, {SHIFT})
    keyloop["grab_codes"].clear()
    app._handle_key_event(Ev(F9, 1), "hold", F8, F9, None, {SHIFT}, [kb], Cfg(), 0)
    assert keyloop["stops"] == 1 and keyloop["grab_codes"] == []


def test_shift_held_too_long_cancels_the_edit(keyloop, monkeypatch):
    monkeypatch.setattr(app, "_is_keyboard_device", lambda d: True)
    monkeypatch.setattr(app, "EDIT_SHIFT_WAIT_S", 0.05)
    cancelled = []
    monkeypatch.setattr(app, "cancel_recording", lambda b, n, reason="": cancelled.append(reason))
    key(F9, 1, {SHIFT})
    app._handle_key_event(Ev(F9, 1), "hold", F8, F9, None, {SHIFT}, [FakeKeyboard([[SHIFT]])], Cfg(), 0)
    assert keyloop["stops"] == 0 and cancelled and "Shift" in cancelled[0]


def test_an_empty_copy_still_puts_the_clipboard_back(monkeypatch):
    board = FakeBoard(selected="")
    board.install(monkeypatch)
    text, _ = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    assert text is None and board.data == b"what I copied"


def test_a_password_is_restored_marked_sensitive(monkeypatch):
    board = FakeBoard(data=b"hunter2")
    board.install(monkeypatch)
    monkeypatch.setattr(cb, "_wl_paste_types",
                        lambda: [board.mime, "x-kde-passwordManagerHint"] if board.data == b"hunter2" else [board.mime])
    _, put_back = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    put_back()
    assert board.data == b"hunter2" and board.written_sensitive is True


def test_copied_files_come_back_as_files(monkeypatch):
    board = FakeBoard(data=b"file:///home/ron/a.txt")
    board.install(monkeypatch)
    types = ["text/plain;charset=utf-8", "text/uri-list", "application/x-kde4-urilist"]
    monkeypatch.setattr(cb, "_wl_paste_types", lambda: types if board.data.startswith(b"file:") else [board.mime])
    written = []
    monkeypatch.setattr(cb, "_wl_copy_bytes",
                        lambda data, mime, sensitive=False: written.append(mime) or board.write(data, mime))
    _, put_back = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    put_back()
    assert written[-1] == "text/uri-list"


def test_a_copied_whole_line_without_a_selection_is_not_a_selection(monkeypatch):
    """VS Code and JetBrains copy the current line on Ctrl+C with nothing selected."""
    board = FakeBoard(selected="the whole line\n")
    board.install(monkeypatch)
    monkeypatch.setattr(cb, "_read_primary", lambda: "an older selection")
    text, _ = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    assert text is None
    assert board.data == b"what I copied"          # not the editor's line


def test_a_real_selection_ending_in_a_line_break_is_kept(monkeypatch):
    board = FakeBoard(selected="a selected line\n")
    board.install(monkeypatch)
    monkeypatch.setattr(cb, "_read_primary", lambda: "a selected line\n")
    text, _ = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    assert text == "a selected line\n"


def test_put_back_leaves_a_newer_clipboard_alone(monkeypatch):
    board = FakeBoard()
    board.install(monkeypatch)
    _, put_back = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    board.data = b"something the user copied since"
    put_back()
    assert board.data == b"something the user copied since"


def test_put_back_after_a_pasted_rewrite_restores(monkeypatch):
    board = FakeBoard()
    board.install(monkeypatch)
    _, put_back = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    board.data = b"the rewrite"                       # paste mode left it there
    put_back(also=("the rewrite",))
    assert board.data == b"what I copied"


def test_a_cut_off_answer_is_not_used(engine, monkeypatch):
    def urlopen(request, timeout):
        body = json.dumps({"choices": [{"message": {"content": "half a rewr"},
                                        "finish_reason": "length"}]}).encode()
        return type("R", (), {"__enter__": lambda s: io.BytesIO(body), "__exit__": lambda s, *a: False})()
    monkeypatch.setattr(ai_cleanup.urllib.request, "urlopen", urlopen)
    assert engine.edit("some long text", "translate this to Japanese") is None


def test_no_edit_in_a_terminal(edit_env, monkeypatch):
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "org.kde.konsole")
    run_edit()
    assert edit_env["typed"] == [] and "terminal" in edit_env["notices"][0]


def test_yakuake_counts_as_a_terminal():
    assert app.is_terminal_class("org.kde.yakuake") and app.is_terminal_class("yakuake")


def test_a_window_change_while_the_ai_works_keeps_the_rewrite_safe(edit_env, monkeypatch):
    classes = iter(["org.kde.kwrite", "org.mozilla.firefox"])
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: next(classes))
    run_edit()
    assert edit_env["typed"] == []
    assert edit_env["history"] == ["We completed the electrical work today."]
    assert "Recent Dictations" in edit_env["notices"][0]


# --- primary selection, links, pasted rewrites and the toggle key ---------------------------

def test_an_app_without_primary_selection_keeps_a_line_selection(monkeypatch):
    board = FakeBoard(selected="a triple-clicked line\n")
    board.install(monkeypatch)
    monkeypatch.setattr(cb, "_read_primary", lambda: None)
    text, _ = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    assert text == "a triple-clicked line\n"


def test_a_copied_link_stays_text(monkeypatch):
    """A browser offers a link as text/uri-list and text; only a file manager's
    copy is files."""
    assert cb._best_type(["text/uri-list", "text/plain;charset=utf-8"]) == "text/plain;charset=utf-8"
    assert cb._best_type(["text/uri-list", "x-special/gnome-copied-files", "text/plain"]) == "text/uri-list"


def test_a_line_by_line_pasted_rewrite_still_restores(edit_env, monkeypatch):
    """Paste mode pastes each line on its own, leaving only the last one."""
    alsos = []
    monkeypatch.setattr(cb, "copy_selection", lambda send: ("old text", lambda also=(): alsos.append(set(also))))
    edit_env["engine"].answer = "- Buy plywood\n- Call George"
    run_edit()
    assert "- Call George" in alsos[0]


def test_tapping_the_toggle_key_during_a_hold_edit_lets_go_of_the_keyboard(keyloop, monkeypatch):
    released = []
    monkeypatch.setattr(app, "_release_all_grabs", lambda: released.append(True))
    key(F8, 1, {SHIFT})                      # hold edit: keyboards grabbed
    key(F9, 1, set())                        # brush the toggle key
    assert keyloop["stops"] == 1 and released


# --- pasting a rewrite, and nothing said ----------------------------------------------------

@pytest.fixture
def injection(monkeypatch):
    """_inject_text with the desktop faked: what was pasted, typed, keyed."""
    seen = {"pasted": [], "typed": [], "shift_enter": 0}
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "org.kde.kwrite")
    monkeypatch.setattr(app, "_paste_text", lambda t: seen["pasted"].append(t) or True)
    monkeypatch.setattr(app, "_send_shift_enter", lambda: seen.__setitem__("shift_enter", seen["shift_enter"] + 1) or True)

    from tuxtalk.output_backends import OutputBackend

    class Backend(OutputBackend):
        def type_text(self, t):
            seen["typed"].append(t)
            return True
    monkeypatch.setattr(app, "_output_backend", lambda: Backend())
    return seen


def test_one_block_is_pasted_once_with_its_line_breaks(injection, monkeypatch):
    monkeypatch.setattr(app, "_determine_injection_method", lambda mode: ("paste", "test"))
    app._inject_text("- 1\n- 2\n- 3", "auto", 0, one_block=True)
    assert injection["pasted"] == ["- 1\n- 2\n- 3"] and injection["shift_enter"] == 0


def test_dictation_still_pastes_line_by_line(injection, monkeypatch):
    """In a chat app; KDE's editors get one paste (test_terminal_line_breaks)."""
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "discord")
    monkeypatch.setattr(app, "_determine_injection_method", lambda mode: ("paste", "test"))
    app._inject_text("one§SHIFT_ENTER§two", "auto", 0)
    assert injection["pasted"] == ["one", "two"] and injection["shift_enter"] == 1


def test_a_typed_block_uses_shift_enter_not_enter(injection, monkeypatch):
    """Typing a plain Enter would send the message in a chat box."""
    monkeypatch.setattr(app, "_determine_injection_method", lambda mode: ("type", "test"))
    app._inject_text("- 1\n- 2", "type", 0, one_block=True)
    assert injection["typed"] == ["- 1§SHIFT_ENTER§- 2"]


def test_nothing_selected_says_so_even_with_nothing_said(edit_env, monkeypatch):
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: None)
    edit_env["selection"]["text"] = None
    run_edit()
    assert len(edit_env["notices"]) == 1 and "Select the text" in edit_env["notices"][0]


def test_a_selection_with_nothing_said_says_so(edit_env, monkeypatch):
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: None)
    run_edit()
    assert edit_env["typed"] == []
    assert len(edit_env["notices"]) == 1 and "didn't catch" in edit_env["notices"][0]
    assert edit_env["put_back"] == 1


# --- TuxTalk Labs: experimental features, with Help and feedback ---------------------------

def test_help_explains_labs_and_voice_editing_on_the_writing_tab():
    """Help is sorted by situation since the 0.16 simplicity review: voice
    editing is explained where people write, marked as Labs."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from gtk_text import find, shown_text
    from tuxtalk import help_dialog
    dialog = help_dialog.show_help_dialog(tab="writing")
    try:
        notebook = find(dialog, Gtk.Notebook)
        page = notebook.get_nth_page(notebook.get_current_page())
        assert "Writing" in notebook.get_tab_label_text(page)
        text = " ".join(shown_text(page))
        for must in ("experimental", "Shift", "Ctrl+Z", "make this more formal", "terminal"):
            assert must in text, must
    finally:
        dialog.destroy()


def test_preferences_has_a_labs_section_with_both_links(prefs_window, monkeypatch):
    from gi.repository import Gtk
    from gtk_text import shown_text
    from tuxtalk import help_dialog
    opened = []
    monkeypatch.setattr(help_dialog, "show_help_dialog", lambda tab=None: opened.append(tab))
    labs = _labs(prefs_window())
    assert any("Experimental" in text for w in labs for text in shown_text(w))
    assert any(isinstance(w, Gtk.CheckButton) and w.get_label().startswith("Voice editing")
               for w in labs)
    next(w for w in labs if isinstance(w, Gtk.Button) and w.get_label() == "How these work").clicked()
    assert opened == ["writing"]
    feedback = next(w for w in labs if isinstance(w, Gtk.LinkButton))
    assert feedback.get_label() == "Send feedback" and feedback.get_uri() == help_dialog.FEEDBACK_URL


def test_the_flatpak_has_no_labs(prefs_window, monkeypatch):
    """Its portal input can't see Shift or send Ctrl+C, so none of them work there yet."""
    from gtk_text import tree_text
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    shown = tree_text(prefs_window().window)
    assert "🧪 TuxTalk Labs" not in shown
    assert not [text for text in shown if text.startswith("Voice editing")]


def test_voice_editing_is_in_labs_not_with_the_cleanup_options(prefs_window):
    window = prefs_window()
    assert window.voice_editing_check in _labs(window)


# --- lines the AI must not lose ------------------------------------------------------------

def scripted_engine(engine, monkeypatch, answers):
    """The engine's AI answers, in turn, with *answers*."""
    replies = iter(answers)

    def urlopen(request, timeout):
        engine.sent.append((json.loads(request.data), timeout))
        return Reply(next(replies))
    monkeypatch.setattr(ai_cleanup.urllib.request, "urlopen", urlopen)


def test_merged_lines_are_edited_again_line_by_line(engine, monkeypatch):
    """Two selected lines can come back as one ("Testing one, two, three,
    four! Testing one, two, three!"), and for some edits the second line is
    lost."""
    scripted_engine(engine, monkeypatch, ["Testing one, two, three, four!",
                                          "Testing one, two, three, four!", "Testing one, two, three!"])
    out = engine.edit("Testing one, two, three, four.\n\n  Testing one, two, three.", "add an exclamation point")
    assert out == "Testing one, two, three, four!\n\n  Testing one, two, three!"
    asked = [body["messages"][-1]["content"].split("\n")[1] for body, _ in engine.sent]
    assert asked == ["Testing one, two, three, four.", "Testing one, two, three, four.",
                     "Testing one, two, three."]


def test_a_list_or_a_shorter_text_may_change_the_lines(engine, monkeypatch):
    scripted_engine(engine, monkeypatch, ["Buy plywood and call George."])
    assert engine.edit("Buy plywood.\nCall George.", "join these into one sentence") == "Buy plywood and call George."
    assert len(engine.sent) == 1


def test_a_failed_line_fails_the_whole_edit(engine, monkeypatch):
    replies = iter(["merged", "first!"])

    def urlopen(request, timeout):
        try:
            return Reply(next(replies))
        except StopIteration:
            raise OSError("server gone")
    monkeypatch.setattr(ai_cleanup.urllib.request, "urlopen", urlopen)
    assert engine.edit("first\nsecond", "add an exclamation point") is None


# --- done without the AI, and formatting --------------------------------------------------

def test_exact_jobs_are_done_in_code_even_before_the_ai_is_ready(edit_env, monkeypatch):
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Make this all caps.")
    edit_env["selection"]["text"] = "Testing one.\nTesting two."
    edit_env["engine"].ready = False
    run_edit()
    assert edit_env["engine"].asked == []
    assert edit_env["typed"] == ["TESTING ONE.\nTESTING TWO."]


@pytest.fixture
def keys_sent(edit_env, monkeypatch):
    sent = []
    monkeypatch.setattr(app, "_send_keys", lambda keys, what="", **kw: sent.append(list(keys)) or True)
    monkeypatch.setattr("tuxtalk.keyboard_layout.active_layout", lambda: ("us", ""))
    return sent


def test_bold_in_a_word_processor_is_its_shortcut(edit_env, keys_sent, monkeypatch):
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Make this bold and italic.")
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "libreoffice-writer")
    run_edit()
    assert keys_sent == [[(29, 1), (48, 1), (48, 0), (29, 0)], [(29, 1), (23, 1), (23, 0), (29, 0)]]
    assert edit_env["typed"] == [] and edit_env["notices"] == [] and edit_env["engine"].asked == []
    assert edit_env["beeps"] == [app.READY_BEEP] and edit_env["put_back"] == 1


def test_bold_in_a_plain_text_app_says_where_it_works(edit_env, keys_sent, monkeypatch):
    """KWrite has no bold; its Ctrl+B sets a bookmark."""
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Make this bold.")
    run_edit()
    assert keys_sent == [] and edit_env["typed"] == [] and edit_env["engine"].asked == []
    assert "LibreOffice" in edit_env["notices"][0]


def test_font_size_says_it_cant(edit_env, keys_sent, monkeypatch):
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Make this text larger.")
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "libreoffice-writer")
    run_edit()
    assert keys_sent == [] and edit_env["typed"] == []
    assert "font, size or color" in edit_env["notices"][0]


@pytest.mark.parametrize("answer", ["None.", "none", "  ", "N/A", "null"])
def test_a_none_answer_is_no_answer(engine, monkeypatch, answer):
    """"Delete this" can get "None." from the AI, once per line."""
    scripted_engine(engine, monkeypatch, [answer, "x", "x"])
    assert engine.edit("Testing one.\nTesting two.", "make it nicer") is None
    assert len(engine.sent) == 1


def test_delete_this_presses_delete(edit_env, keys_sent, monkeypatch):
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Delete this.")
    run_edit()
    assert keys_sent == [[(111, 1), (111, 0)]]
    assert edit_env["typed"] == [] and edit_env["engine"].asked == [] and edit_env["notices"] == []


def test_an_ai_answer_that_only_changes_spacing_is_no_change(edit_env, monkeypatch):
    """An answer that only trims the line ends (as the AI does for "Select
    all") changes nothing."""
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Select all.")
    edit_env["selection"]["text"] = "Testing one. \nTesting two. "
    edit_env["engine"].answer = "Testing one.\nTesting two."
    run_edit()
    assert edit_env["typed"] == [] and "may already read that way" in edit_env["notices"][0]


def test_indenting_still_counts_as_a_change(edit_env, monkeypatch):
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Indent this.")
    edit_env["selection"]["text"] = "One\nTwo"
    run_edit()
    assert edit_env["typed"] == ["    One\n    Two"]


# --- X11 and a clipboard that can't be used ----------------------------------------------

def test_on_x11_it_says_voice_editing_needs_wayland(edit_env, monkeypatch):
    """The selection is read with wl-copy / wl-paste, so on X11 every edit
    would say "select some text first" with text selected."""
    monkeypatch.setattr(app, "_is_wayland_session", lambda: False)
    copied = []
    monkeypatch.setattr(cb, "copy_selection", lambda send: copied.append(1))
    run_edit()
    assert copied == [] and edit_env["typed"] == []
    assert len(edit_env["notices"]) == 1 and "Wayland" in edit_env["notices"][0]


def test_a_clipboard_that_cannot_be_used_is_not_called_no_selection(monkeypatch):
    board = FakeBoard()
    board.install(monkeypatch)

    def broken(text):
        raise FileNotFoundError("wl-copy")
    monkeypatch.setattr(cb, "_wl_copy_sensitive", broken)
    with pytest.raises(cb.ClipboardUnavailable):
        cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    assert board.data == b"what I copied"


def test_the_edit_says_the_clipboard_could_not_be_used(edit_env, monkeypatch):
    def unavailable(send):
        raise cb.ClipboardUnavailable("wl-copy failed")
    monkeypatch.setattr(cb, "copy_selection", unavailable)
    run_edit()
    assert len(edit_env["notices"]) == 1 and "clipboard" in edit_env["notices"][0]
    assert "Select" not in edit_env["notices"][0]


# --- the window must still be the same one before any key is pressed ----------------------

@pytest.mark.parametrize("said, focused", [
    ("Delete this.", "org.kde.kwrite"),
    ("Make this bold.", "libreoffice-writer"),
])
def test_no_key_goes_to_a_window_switched_to_while_transcribing(edit_env, keys_sent, monkeypatch,
                                                                said, focused):
    """Delete and Ctrl+B are sent after transcription, so without a check they
    could land in whatever window the user had clicked into."""
    classes = iter([focused, "org.kde.konsole"])
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: next(classes))
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: said)
    run_edit()
    assert keys_sent == [] and edit_env["typed"] == []
    assert len(edit_env["notices"]) == 1 and "switched windows" in edit_env["notices"][0]
    assert "Recent Dictations" not in edit_env["notices"][0]


@pytest.mark.parametrize("said", ["Delete this.", "Make this bold."])
def test_no_key_is_pressed_when_the_window_is_unknown(edit_env, keys_sent, monkeypatch, said):
    """Unknown both times (None == None) is not "the same window", or a
    switch during transcription goes unnoticed."""
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: None)
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: said)
    run_edit()
    assert keys_sent == [] and edit_env["typed"] == []
    assert len(edit_env["notices"]) == 1


@pytest.mark.parametrize("said", ["Delete this.", "Make this bold.", "make it more professional"])
def test_a_window_that_cant_be_found_after_transcribing_counts_as_moved(edit_env, keys_sent,
                                                                       monkeypatch, said):
    classes = iter(["libreoffice-writer", None])
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: next(classes))
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: said)
    run_edit()
    assert keys_sent == [] and edit_env["typed"] == []
    assert len(edit_env["notices"]) == 1 and "switched windows" in edit_env["notices"][0]


def test_a_rewrite_is_still_typed_where_the_window_is_never_known(edit_env, monkeypatch):
    """Refusing would turn voice editing off on desktops that can't report
    the window; the rewrite is typed like ordinary dictation."""
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: None)
    run_edit()
    assert edit_env["typed"] == ["We completed the electrical work today."]
    assert edit_env["notices"] == []


@pytest.mark.parametrize("said, selected, typed", [
    ("Indent this.", "One", "    One"),
    ("Unindent this.", "    One", "One"),
])
def test_indenting_one_line_counts_as_a_change(edit_env, monkeypatch, said, selected, typed):
    """The only change is spacing, which a strip() comparison would remove."""
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: said)
    edit_env["selection"]["text"] = selected
    run_edit()
    assert edit_env["typed"] == [typed] and edit_env["notices"] == []


# --- deleting and formatting need no AI ------------------------------------------------------

def test_delete_and_exact_jobs_work_without_the_ai_downloaded(edit_env, keys_sent, monkeypatch):
    """Edits done with a key or in code must not say "needs its AI" when the
    AI download is missing."""
    monkeypatch.setattr(app, "_ai_engine", None)
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Delete this.")
    run_edit()
    assert keys_sent == [[(111, 1), (111, 0)]] and edit_env["notices"] == []
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Make this all caps.")
    run_edit()
    assert edit_env["typed"] == ["WE FINISHED THE ELECTRICAL TODAY."] and edit_env["notices"] == []


def test_a_long_selection_can_still_be_deleted_or_made_bold(edit_env, keys_sent, monkeypatch):
    edit_env["selection"]["text"] = "x " * 2000
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Delete this.")
    run_edit()
    monkeypatch.setattr(app, "_query_focused_window_class", lambda: "libreoffice-writer")
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "Make this bold.")
    run_edit()
    assert keys_sent == [[(111, 1), (111, 0)], [(29, 1), (48, 1), (48, 0), (29, 0)]]
    assert edit_env["notices"] == []


# --- put_back leaves only what the user had ------------------------------------------------

def test_the_probe_marker_is_not_left_when_the_clipboard_could_not_be_kept(monkeypatch):
    """When the old clipboard can't be read (too big, too slow) there is
    nothing to put back, and the next Ctrl+V must not paste
    "tuxtalk-selection-probe-…"."""
    board = FakeBoard(selected=None)
    board.install(monkeypatch)
    monkeypatch.setattr(cb, "_read_clipboard", lambda: cb._NOTHING)
    text, _ = cb.copy_selection(board.ctrl_c, sleep=lambda s: None)
    assert text is None and board.data is None


def test_an_image_copied_during_the_probe_is_left_alone(monkeypatch):
    board = FakeBoard()
    board.install(monkeypatch)

    def copy_an_image():
        board.data, board.mime = b"\x89PNG", "image/png"
        return True
    monkeypatch.setattr(cb, "_wl_paste", lambda mime: board.data if mime == board.mime else b"")
    monkeypatch.setattr(cb, "_wl_paste_types", lambda: [board.mime] if board.data is not None else [])
    text, _ = cb.copy_selection(copy_an_image, sleep=lambda s: None)
    assert text is None and board.data == b"\x89PNG"


def test_a_rewrite_uses_ss_with_swiss_spelling_on(edit_env, monkeypatch):
    """The AI writes ß like the speech engines do (issue #10)."""
    monkeypatch.setattr(app, "_swiss_spelling", True)
    edit_env["engine"].answer = "Die Straße ist gross."
    run_edit()
    assert edit_env["typed"] == ["Die Strasse ist gross."]


def test_two_changes_are_done_one_after_the_other(edit_env, monkeypatch):
    """"make it formal and remove the commas": the AI rewords, then the
    commas go in code, exactly."""
    monkeypatch.setattr(app, "_transcribe_audio",
                        lambda audio, lang: "make it formal and remove the commas")
    edit_env["engine"].answer = "We completed the electrical work, as planned, today."
    run_edit()
    assert [i for _t, i in edit_env["engine"].asked] == ["make it formal"]
    assert edit_env["typed"] == ["We completed the electrical work as planned today."]


def test_a_step_with_no_change_does_not_stop_the_others(edit_env, monkeypatch):
    monkeypatch.setattr(app, "_transcribe_audio",
                        lambda audio, lang: "make it formal and make it all caps")
    edit_env["engine"].answer = None              # the AI has nothing for step one
    run_edit()
    assert edit_env["typed"] == ["WE FINISHED THE ELECTRICAL TODAY."]


def test_a_rewording_step_needs_the_ai(edit_env, monkeypatch):
    monkeypatch.setattr(app, "_ai_engine", None)
    monkeypatch.setattr(app, "_transcribe_audio",
                        lambda audio, lang: "remove the commas and make it formal")
    run_edit()
    assert edit_env["typed"] == []
    assert edit_env["notices"]


def test_the_edit_key_understands_select_commands_with_nothing_selected(edit_env, monkeypatch):
    """Shift+F8 "select the last sentence", then Shift+F8 "make it formal"."""
    pressed = []
    monkeypatch.setattr(app, "_voice_navigation", True)
    monkeypatch.setattr(app, "_output_backend",
                        lambda: type("B", (), {"press_keys": lambda self, c: pressed.append(c) or True})())
    monkeypatch.setattr(app.state, "last_inserted_text", "One. Two.")
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "select the last sentence")
    edit_env["selection"]["text"] = ""
    run_edit()
    assert len(pressed) == 1 and edit_env["notices"] == [] and edit_env["typed"] == []


def test_with_the_labs_switch_off_nothing_selected_still_says_select_first(edit_env, monkeypatch):
    monkeypatch.setattr(app, "_voice_navigation", False)
    monkeypatch.setattr(app, "_transcribe_audio", lambda audio, lang: "select the last sentence")
    edit_env["selection"]["text"] = ""
    run_edit()
    assert edit_env["notices"] and "Select the text" in edit_env["notices"][0]
