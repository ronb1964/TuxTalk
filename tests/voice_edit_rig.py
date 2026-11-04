"""The world around a voice edit, faked, for the voice editing tests
(test_voice_editing.py, test_spoken_edit.py)."""
from tuxtalk import clipboard as cb
from tuxtalk import delivery, history, keyboard_output, voice_edit
from tuxtalk import engine as speech_engine
from tuxtalk import live_settings as live


def fake_edit_world(monkeypatch):
    """voice_edit.transcribe_and_edit and edit_selection with every outside
    part faked: the AI (seen["engine"]), the selection (seen["selection"]),
    typing, notices, beeps and Recent Dictations. Returns what they saw."""
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
    monkeypatch.setattr(live, "ai_engine", engine)
    monkeypatch.setattr(speech_engine, "transcribe",
                        lambda audio, lang: "make it more professional")
    monkeypatch.setattr("tuxtalk.silence.is_speechless_tap", lambda a, sr: False)
    selection = {"text": "We finished the electrical today."}

    def copy_selection(send_copy):
        def put_back(also=()):
            seen["put_back"] += 1
        return selection["text"], put_back
    monkeypatch.setattr(cb, "copy_selection", copy_selection)
    monkeypatch.setattr(delivery, "inject_text",
                        lambda text, mode, one_block=False, **kw: seen["typed"].append(text) or "ok")
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, msg: seen["notices"].append(msg))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda on, *a: seen["beeps"].append(a))
    monkeypatch.setattr(history, "add_entry", lambda text: seen["history"].append(text))
    monkeypatch.setattr(voice_edit, "EDIT_RESTORE_DELAY_S", 0)
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.kwrite")
    monkeypatch.setattr(keyboard_output, "is_wayland_session", lambda: True)
    seen["engine"], seen["selection"] = engine, selection
    return seen
