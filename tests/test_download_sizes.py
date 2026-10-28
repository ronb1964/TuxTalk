"""Download sizes come from one place: the AI's from ai_cleanup (its model
and the engine that runs it: "1.1 GB" alone left the 30 MB engine out), and
CUDA's from cuda_helper (typed out by hand in Preferences, Help and System
check, so one change of size meant finding them all)."""
from tuxtalk import ai_cleanup, cuda_helper, help_text, prefs_speech


def test_help_and_voice_commands_give_the_ai_and_cuda_sizes():
    from tuxtalk.voice_commands_dialog import _build_markup
    text = "".join(markup for _title, markup in help_text.tabs())
    assert f"one-time {ai_cleanup.DOWNLOAD_SIZE_TEXT} download" in text
    assert f"~{cuda_helper.DOWNLOAD_SIZE_TEXT} download" in text
    assert f"about {ai_cleanup.DOWNLOAD_SIZE_TEXT})" in _build_markup()
    assert "open brace</b> → {" in _build_markup()      # still shown as one brace


def test_the_device_list_gives_cudas_size_before_its_download():
    label = prefs_speech.device_label("cuda", "small", cuda_ready=False)
    assert f"{cuda_helper.DOWNLOAD_SIZE_TEXT} download" in label
