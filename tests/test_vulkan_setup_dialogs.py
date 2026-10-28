"""The Vulkan setup questions name the model the way the menus do."""
from tuxtalk import vulkan_setup_dialogs as vsd


def test_the_light_or_full_question_names_large_v3_as_the_menus_do(monkeypatch):
    """str.title() wrote "Large-V3"; the menus say "Whisper Large-v3"."""
    said = []

    class Dialog:
        def __init__(self, text, **kwargs):
            said.append(text)

        def format_secondary_text(self, text):
            said.append(text)

        def add_button(self, *a):
            pass

        def set_keep_above(self, *a):
            pass

        def run(self):
            return None

        def destroy(self):
            pass
    monkeypatch.setattr(vsd, "message_dialog", lambda **kw: Dialog(**kw))
    assert vsd.choose_light_or_full(None, "large-v3") is None
    assert said[0] == "Whisper Large-v3 needs your graphics card"
    assert "Large-V3" not in said[1] and "Whisper Large-v3 in its" in said[1]
