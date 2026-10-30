"""The Vulkan setup questions name the model the way the menus do."""
from tuxtalk import vulkan_setup_dialogs as vsd


def test_the_light_or_full_question_names_large_v3_as_the_menus_do(message_boxes):
    """str.title() wrote "Large-V3"; the menus say "Whisper Large-v3"."""
    assert vsd.choose_light_or_full(None, "large-v3") is None
    [box] = message_boxes.shown
    assert box.title == "Whisper Large-v3 needs your graphics card"
    assert "Large-V3" not in box.text and "Whisper Large-v3 in its format" in box.text
