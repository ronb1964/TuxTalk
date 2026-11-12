"""Parakeet on the graphics chip: the helper process and its protocol.

The real helper (parakeet_gpu_worker.py) needs a graphics chip and the engine,
and was checked by hand on an RTX 4070 Super. These tests run a stand-in
helper that speaks the same protocol, so the start-up, transcription, crash
and fallback paths are all exercised through a real process and real pipes.
"""
import io
import sys
import textwrap
import threading

import numpy as np
import pytest

from tuxtalk import parakeet_gpu
from tuxtalk import parakeet_gpu_worker as worker

# A stand-in helper. Behaviour is picked by its first argument:
#   ok      answers "<n> samples" for each request
#   refuse  reports an error instead of becoming ready
#   crash   exits with an engine error before becoming ready
#   die     becomes ready, then exits on the first request
#   chatty  becomes ready, writes to stderr, then exits on the first request
#   once    answers the first request, then exits
#   old     answers Your Words as a helper too old to take them would
#   silent  never answers at all
#   half    becomes ready, then freezes halfway through its first reply
#   deaf    becomes ready, then stops reading its requests
FAKE_WORKER = textwrap.dedent("""
    import json, struct, sys
    mode = sys.argv[1]
    out, inp = sys.stdout.buffer, sys.stdin.buffer
    def reply(obj):
        out.write((json.dumps(obj) + "\\n").encode()); out.flush()
    if mode == "silent":
        import time; time.sleep(60)
    if mode == "refuse":
        reply({"error": "no graphics chip"}); sys.exit(1)
    if mode == "crash":
        sys.exit("ggml_vulkan: no device found")
    reply({"ready": True})
    if mode == "chatty":
        print("the dictated words", file=sys.stderr); sys.stderr.flush()
    if mode == "deaf":
        import time; time.sleep(60)
    answered = 0
    while True:
        head = inp.read(4)
        if len(head) < 4:
            break
        n = struct.unpack("<I", head)[0]
        if n == 0xFFFFFFFF:                       # Your Words (WORDS_REQUEST)
            size = struct.unpack("<I", inp.read(4))[0]
            words = json.loads(inp.read(size))
            reply({"words": None if mode == "old" else len(words)})
            continue
        inp.read(n * 4)
        if mode == "half":
            out.write(b'{"text": "par'); out.flush()
            import time; time.sleep(60)
        if mode in ("die", "chatty") or (mode == "once" and answered):
            sys.exit(3)
        reply({"text": f" {n} samples "})
        answered += 1
""")


@pytest.fixture
def fake(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER)
    return lambda mode: [sys.executable, str(script), mode]


def _audio(seconds=1.0):
    return np.zeros(int(16000 * seconds), dtype=np.float32)


def test_transcribes_through_the_helper(fake):
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake("ok"))
    try:
        assert model.recognize(_audio(2)) == "32000 samples"
        assert model.recognize(_audio(3)) == "48000 samples"
    finally:
        model.close()


def test_very_short_audio_is_padded_to_a_second(fake):
    """A tap of the key can record almost nothing; the engine gets at least
    a second, padded with silence, rather than a near-empty buffer."""
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake("ok"))
    try:
        assert model.recognize(_audio(0.1)) == "16000 samples"
    finally:
        model.close()


def test_a_helper_that_cannot_start_says_why(fake):
    with pytest.raises(RuntimeError, match="no graphics chip"):
        parakeet_gpu.ParakeetGpuModel(0, command=fake("refuse"))


def test_a_helper_that_dies_while_starting_says_what_it_printed(fake):
    with pytest.raises(RuntimeError, match="ggml_vulkan: no device found"):
        parakeet_gpu.ParakeetGpuModel(0, command=fake("crash"))


def test_what_a_ready_helper_prints_never_reaches_the_log(fake, monkeypatch, caplog):
    """It dies during a dictation after printing: by then its messages could
    hold the words, so the log says only that it stopped."""
    import logging

    class Cpu:
        def recognize(self, audio):
            return "from the processor"

    monkeypatch.setattr(parakeet_gpu, "load_parakeet_model", lambda: Cpu())
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake("chatty"))
    try:
        with caplog.at_level(logging.DEBUG):
            assert model.recognize(_audio()) == "from the processor"
        assert "helper stopped" in caplog.text
        assert "dictated words" not in caplog.text
    finally:
        model.close()


def test_a_crashed_helper_is_started_again(fake):
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake("once"))
    try:
        assert model.recognize(_audio()) == "16000 samples"
        # The helper exits on its second request; a fresh one answers it.
        assert model.recognize(_audio(2)) == "32000 samples"
    finally:
        model.close()


def test_a_helper_started_again_runs_the_engine_on_disk_now(fake, monkeypatch):
    """An engine update during the session (whisper_vulkan.update_in_background)
    removes the folder of the build the helper started from: a crashed
    helper must start from the new one, or it fails at every dictation."""
    from tuxtalk import whisper_vulkan as wv
    started = []

    def launch(argv, env=None):
        started.append(argv[argv.index("tuxtalk.parakeet_gpu_worker") + 1])
        return fake("once"), env
    monkeypatch.setattr(parakeet_gpu.engine_process, "parent_death_command", launch)
    monkeypatch.setattr(wv, "model_path", lambda name: "/models/parakeet.bin")
    monkeypatch.setattr(wv, "engine_dir", lambda: "/engines/build-1")
    model = parakeet_gpu.ParakeetGpuModel(0)
    try:
        assert model.recognize(_audio()) == "16000 samples"
        monkeypatch.setattr(wv, "engine_dir", lambda: "/engines/build-2")
        assert model.recognize(_audio(2)) == "32000 samples"    # through a new helper
        assert started == ["/engines/build-1", "/engines/build-2"]
    finally:
        model.close()


def test_falls_back_to_the_processor_when_the_helper_keeps_failing(fake, monkeypatch):
    """Once, saying so (the service then tells the menus and live text)."""
    class Cpu:
        def recognize(self, audio):
            return "from the processor"

    monkeypatch.setattr(parakeet_gpu, "load_parakeet_model", lambda: Cpu())
    moved = []
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake("die"),
                                          on_fallback=lambda: moved.append(True))
    try:
        assert model.recognize(_audio()) == "from the processor"
        assert model.recognize(_audio()) == "from the processor"   # stays there
    finally:
        model.close()
    assert moved == [True]


def test_the_fallback_is_told_once_the_dictations_text_is_in_hand(fake, monkeypatch):
    """Telling the menus is a D-Bus call to the tray: made first, inside the
    lock, it held up the dictation that found the fallback, and the next."""
    events = []

    class Cpu:
        def recognize(self, audio):
            events.append("recognized")
            return "from the processor"

    monkeypatch.setattr(parakeet_gpu, "load_parakeet_model", lambda: Cpu())
    model = parakeet_gpu.ParakeetGpuModel(
        0, command=fake("die"),
        on_fallback=lambda: events.append(("told", model._lock.locked())))
    try:
        assert model.recognize(_audio()) == "from the processor"
    finally:
        model.close()
    assert events == ["recognized", ("told", False)]


def test_the_fallback_is_told_even_when_the_processors_first_read_fails(fake, monkeypatch):
    """Every later dictation goes to the processor without a word to the
    menus, so if this one isn't told, they say "graphics chip" all session."""
    told = []

    class Cpu:
        def recognize(self, audio):
            raise RuntimeError("the processor's read failed")

    monkeypatch.setattr(parakeet_gpu, "load_parakeet_model", lambda: Cpu())
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake("die"),
                                          on_fallback=lambda: told.append(True))
    try:
        with pytest.raises(RuntimeError, match="processor's read failed"):
            model.recognize(_audio())
    finally:
        model.close()
    assert told == [True]


def test_the_menus_and_live_text_follow_parakeet_to_the_processor(monkeypatch):
    """What runs changes at once; the tray is told without the dictation
    waiting for it."""
    from tuxtalk import engine
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    monkeypatch.setattr(engine, "_running_engine", (PARAKEET_MODEL, "vulkan"))
    monkeypatch.setattr(engine, "model", object())
    tray_answers, told = threading.Event(), []

    def slow_tray():
        tray_answers.wait(5)
        told.append(engine.running())
    monkeypatch.setattr(engine, "notify_tray", slow_tray)
    engine.processor_took_over()
    assert not engine.on_graphics_card() and told == []      # returned before the tray answered
    tray_answers.set()
    for thread in threading.enumerate():
        if thread.name == "tell-tray-processor":
            thread.join(5)
    assert told == [(PARAKEET_MODEL, "cpu")]


def test_no_processor_model_to_fall_back_to_is_an_error(fake, monkeypatch):
    def missing():
        raise FileNotFoundError("Parakeet model has not been downloaded")

    monkeypatch.setattr(parakeet_gpu, "load_parakeet_model", missing)
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake("die"))
    try:
        with pytest.raises(RuntimeError, match="graphics"):
            model.recognize(_audio())
    finally:
        model.close()


# --- the helper's side of the protocol -------------------------------------------

def test_worker_reads_one_request_at_a_time_and_stops_at_the_end():
    samples = np.arange(5, dtype=np.float32)
    stream = io.BytesIO(worker.encode_request(samples) + worker.encode_request(samples[:2]))
    assert np.frombuffer(worker.read_request(stream), np.float32).tolist() == [0, 1, 2, 3, 4]
    assert np.frombuffer(worker.read_request(stream), np.float32).tolist() == [0, 1]
    assert worker.read_request(stream) is None


def test_worker_replies_are_single_json_lines():
    out = io.BytesIO()
    worker.write_reply(out, {"text": "two\nlines"})
    assert out.getvalue().count(b"\n") == 1


def test_segments_are_joined_into_one_line_of_text():
    assert worker.join_segments([" And so,", " my fellow", "Americans. "]) == \
        "And so, my fellow Americans."
    assert worker.join_segments([]) == ""


def test_a_restart_on_a_short_lived_thread_outlives_that_thread(fake):
    """The helper dies with the thread that started it. A restart from a
    dictation thread that then ends must leave a helper that keeps working."""
    import threading
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake("once"))
    try:
        model.recognize(_audio())
        results = []
        t = threading.Thread(target=lambda: results.append(model.recognize(_audio(2))))
        t.start()
        t.join(30)                                # restarted the helper, then ended
        assert not t.is_alive()
        assert results == ["32000 samples"]
        assert model._proc.poll() is None         # still running
        assert model.recognize(_audio(3)) == "48000 samples"
    finally:
        model.close()


def test_a_helper_that_never_answers_is_stopped(fake, monkeypatch):
    """A helper that times out while starting must not keep running, holding
    graphics memory, for the rest of the session."""
    monkeypatch.setattr(parakeet_gpu, "START_TIMEOUT", 0.5)
    started = []
    real_popen = parakeet_gpu.subprocess.Popen

    def recording_popen(*a, **k):
        started.append(real_popen(*a, **k))
        return started[-1]
    monkeypatch.setattr(parakeet_gpu.subprocess, "Popen", recording_popen)
    with pytest.raises(RuntimeError):
        parakeet_gpu.ParakeetGpuModel(0, command=fake("silent"))
    assert started and started[0].poll() is not None


@pytest.mark.parametrize("mode", ["half", "deaf"])
def test_a_helper_frozen_mid_request_gives_way_to_the_processor(fake, monkeypatch, mode):
    """A helper that hangs halfway through a reply, or stops reading the
    recording, must not hang dictation until a restart: each try ends at
    its deadline and the recording goes to the processor."""
    class Cpu:
        def recognize(self, audio):
            return "from the processor"

    monkeypatch.setattr(parakeet_gpu, "REPLY_TIMEOUT", 0.5)
    monkeypatch.setattr(parakeet_gpu, "REPLY_PER_AUDIO_S", 0)
    monkeypatch.setattr(parakeet_gpu, "load_parakeet_model", lambda: Cpu())
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake(mode))
    try:
        # Three seconds of audio is more than a pipe holds, so a helper
        # that stops reading blocks the write, not only the reply.
        assert model.recognize(_audio(3)) == "from the processor"
    finally:
        model.close()


def test_your_words_reach_the_helper_once_per_change(fake, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="tuxtalk.parakeet_gpu")
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake("ok"))
    try:
        assert model.recognize(_audio()) == "16000 samples"      # no words: nothing sent
        model.set_words(["TuxTalk", "ClaudeClaw"])
        assert model.recognize(_audio()) == "16000 samples"
        model.set_words(["TuxTalk", "ClaudeClaw"])
        assert model.recognize(_audio()) == "16000 samples"
        sent = [r.message for r in caplog.records if "Your Words" in r.message]
        assert len(sent) == 1 and "2 spellings of 2 words" in sent[0]
    finally:
        model.close()


def test_an_engine_without_the_patch_still_transcribes(fake, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="tuxtalk.parakeet_gpu")
    model = parakeet_gpu.ParakeetGpuModel(0, command=fake("old"))
    try:
        model.set_words(["TuxTalk"])
        assert model.recognize(_audio()) == "16000 samples"
        assert any("can't steer Parakeet" in r.message for r in caplog.records)
    finally:
        model.close()


def test_worker_reads_a_word_list_between_recordings():
    stream = io.BytesIO(worker.encode_words(["TuxTalk", "Ärger"])
                        + worker.encode_request(np.zeros(3, dtype=np.float32)))
    assert worker.read_request(stream) == ["TuxTalk", "Ärger"]
    assert len(worker.read_request(stream)) == 12
    assert worker.read_request(stream) is None


def test_the_real_helper_starts_through_the_engine_launcher(monkeypatch):
    """So it dies with the service (test_engine_parent_death)."""
    started = []

    def popen(command, **kwargs):
        started.append((command, kwargs))
        raise OSError("not starting anything in a test")
    from tuxtalk import whisper_vulkan
    monkeypatch.setattr(whisper_vulkan, "model_path", lambda name: "/x/parakeet.bin")
    monkeypatch.setattr(whisper_vulkan, "_engine_dir", lambda: "/x")
    monkeypatch.setattr(parakeet_gpu.subprocess, "Popen", popen)
    with pytest.raises(OSError):
        parakeet_gpu.ParakeetGpuModel(0, threads=1)
    command, kwargs = started[0]
    assert command[1] == parakeet_gpu.engine_process.engine_launcher.__file__
    assert command[command.index("--") + 1:][1:3] == ["-m", "tuxtalk.parakeet_gpu_worker"]
    assert "preexec_fn" not in kwargs
