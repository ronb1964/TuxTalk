"""Download sizes come from one place: the AI's from ai_cleanup (its model
and the engine that runs it: "1.1 GB" alone left the 30 MB engine out), and
CUDA's from cuda_helper (typed out by hand in Preferences, Help and System
check, so one change of size meant finding them all)."""
from tuxtalk import ai_cleanup, cuda_helper, help_text, prefs_speech


AI_SIZE, CUDA_SIZE = "7.7 GB", "9.9 GB"      # sizes no text would have typed out


def test_help_gives_the_ai_and_cuda_sizes_from_their_modules(monkeypatch):
    """Changed in one place, every line of Help follows; a size typed out
    by hand would still read the old one."""
    real_cuda = cuda_helper.DOWNLOAD_SIZE_TEXT
    monkeypatch.setattr(help_text, "AI_SIZE_TEXT", AI_SIZE)
    monkeypatch.setattr(help_text, "CUDA_SIZE_TEXT", CUDA_SIZE)
    text = "".join(markup for _title, markup in help_text.tabs())
    assert f"one-time {AI_SIZE} download" in text
    assert f"~{CUDA_SIZE} download" in text
    assert real_cuda not in text and real_cuda.replace(" ", "") not in text


def test_voice_commands_give_the_ai_size_and_keep_the_braces(monkeypatch):
    from tuxtalk.voice_commands_dialog import _build_markup
    monkeypatch.setattr(ai_cleanup, "DOWNLOAD_SIZE_TEXT", AI_SIZE)
    markup = _build_markup()
    assert f"about {AI_SIZE})" in markup
    # The f-string that puts the size in must still show each brace once.
    assert "  <b>open brace</b> → {         <b>close brace</b> → }" in markup.splitlines()


def test_the_device_list_gives_cudas_size_before_its_download(monkeypatch):
    monkeypatch.setattr(prefs_speech, "CUDA_SIZE_TEXT", CUDA_SIZE)
    label = prefs_speech.device_label("cuda", "small", cuda_ready=False)
    assert f"{CUDA_SIZE} download" in label


def test_the_large_v3_question_gives_cudas_size(monkeypatch):
    """Preferences' question before CUDA and large-v3 download together."""
    from types import SimpleNamespace
    asked = []
    monkeypatch.setattr(prefs_speech, "CUDA_SIZE_TEXT", CUDA_SIZE)
    monkeypatch.setattr(prefs_speech, "show_message",
                        lambda *args, **kwargs: asked.append(args[3]) or None)
    section = SimpleNamespace(window=None, win=SimpleNamespace(vulkan_offered=lambda: False))
    prefs_speech.SpeechSection._large_v3_on_nvidia(section)
    assert f"~{CUDA_SIZE}" in asked[0]
    assert cuda_helper.DOWNLOAD_SIZE_TEXT.replace(" ", "") not in asked[0]


def test_reading_a_size_doesnt_load_gtk():
    """recommend.py reads CUDA's size, and the dictation service loads
    recommend: GTK came along with cuda_helper for one string."""
    import subprocess
    import sys
    code = ("import sys, tuxtalk.recommend, tuxtalk.cuda_helper; "
            "sys.exit('gi.repository.Gtk' in sys.modules)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_a_cuda_wheel_file_named_outside_its_folder_is_skipped(tmp_path):
    """A wheel's file names are data: "nvidia//home/u/x.so" or "../x.so"
    would have been written wherever they pointed."""
    assert cuda_helper._inside(str(tmp_path), "cublas/lib/libcublas.so.12") \
        == str(tmp_path / "cublas/lib/libcublas.so.12")
    assert cuda_helper._inside(str(tmp_path), "/home/u/.config/autostart/x.so.desktop") is None
    assert cuda_helper._inside(str(tmp_path), "../../x.so") is None
