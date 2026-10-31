"""Whisper on AMD / Intel graphics through whisper.cpp's Vulkan engine.

The parts that need a real graphics chip (the engine itself) were checked by
hand on an RTX 4070 Super and a Ryzen 7800X3D's built-in Radeon; these cover
everything around them.
"""
import io
import os
import pathlib
import time
import types
import wave

import numpy as np
import pytest

from tuxtalk import whisper_vulkan as wv
from tuxtalk import engine

# Exactly what the engine printed on the development machine (NVIDIA card plus
# the Ryzen's built-in AMD graphics).
REAL_DEVICE_LIST = """\
ggml_vulkan: Found 2 Vulkan devices:
ggml_vulkan: 0 = NVIDIA GeForce RTX 4070 SUPER (NVIDIA) | uma: 0 | fp16: 1 | bf16: 1 | fp4: 0 | warp size: 32 | shared memory: 49152 | int dot: 1 | matrix cores: NV_coopmat2
ggml_vulkan: 1 = AMD Ryzen 7 7800X3D 8-Core Processor (RADV RAPHAEL_MENDOCINO) (radv) | uma: 1 | fp16: dot2 | bf16: 0 | fp4: 0 | warp size: 32 | shared memory: 65536 | int dot: 1 | matrix cores: none
load_backend: loaded Vulkan backend from /x/libggml-vulkan.so
"""


def test_parses_the_device_list_the_engine_prints():
    assert wv.parse_vulkan_devices(REAL_DEVICE_LIST) == [
        (0, "NVIDIA GeForce RTX 4070 SUPER (NVIDIA)", True, False),
        (1, "AMD Ryzen 7 7800X3D 8-Core Processor (RADV RAPHAEL_MENDOCINO) (radv)", False, True),
    ]


def test_picks_the_graphics_card_over_built_in_graphics_any_brand():
    """On the development machine: the RTX 4070 Super, not the Ryzen's tiny
    built-in Radeon (16x faster than the CPU vs no faster at all)."""
    assert wv.choose_device(wv.parse_vulkan_devices(REAL_DEVICE_LIST)) == 0


def test_prefers_a_graphics_card_over_built_in_graphics():
    devices = [(0, "AMD Radeon 780M (radv)", False, True),
               (1, "Intel Arc A770 (Intel open-source Mesa driver)", False, False)]
    assert wv.choose_device(devices) == 1


def test_an_nvidia_card_alone_is_used_and_no_chip_means_none():
    assert wv.choose_device([(0, "NVIDIA GeForce RTX 3060", True, False)]) == 0
    assert wv.choose_device([(0, "AMD Radeon 780M (radv)", False, True)]) == 0
    assert wv.choose_device([]) is None


def test_finds_amd_and_intel_chips_from_the_kernel(tmp_path):
    for card, vendor in (("card0", "0x10de"), ("card1", "0x1002"), ("card1-DP-1", None)):
        (tmp_path / card / "device").mkdir(parents=True)
        if vendor:
            (tmp_path / card / "device" / "vendor").write_text(vendor + "\n")
    assert wv.graphics_vendors(str(tmp_path)) == {"0x10de", "0x1002"}


def test_offered_with_any_graphics_chip_and_vulkan(monkeypatch):
    monkeypatch.setattr(wv, "vulkan_available", lambda: True)
    for vendors in ({wv.NVIDIA_VENDOR}, {wv.AMD_VENDOR}, {wv.INTEL_VENDOR}):
        monkeypatch.setattr(wv, "graphics_vendors", lambda v=vendors: v)
        assert wv.is_offered() is True
    monkeypatch.setattr(wv, "graphics_vendors", lambda: set())       # e.g. a VM
    assert wv.is_offered() is False
    monkeypatch.setattr(wv, "graphics_vendors", lambda: {wv.NVIDIA_VENDOR})
    monkeypatch.setattr(wv, "vulkan_available", lambda: False)
    assert wv.is_offered() is False


def test_every_offered_model_has_a_file_in_whisper_cpp_format():
    """Parakeet included: whisper.cpp 1.9.4 runs it too (libparakeet)."""
    from tuxtalk.model_helper import OFFERED_MODELS
    for model in OFFERED_MODELS:
        assert wv.supports_model(model), model


def test_parakeet_comes_from_its_own_repo_and_whisper_from_whisper_cpps():
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    assert wv.model_repo(PARAKEET_MODEL) == "ggml-org/parakeet-GGUF"
    assert wv.MODEL_FILES[PARAKEET_MODEL][0] == "ggml-parakeet-tdt-0.6b-v3-q8_0.bin"
    assert wv.model_repo("small") == "ggerganov/whisper.cpp"


def _engine(tmp_path, monkeypatch, *names):
    """An engine folder holding executables *names*."""
    monkeypatch.setattr(wv, "_engine_dir", lambda: str(tmp_path))
    for name in names:
        f = tmp_path / name
        f.write_text("")
        f.chmod(0o755)


def test_parakeet_needs_an_engine_built_with_parakeet(tmp_path, monkeypatch):
    """Engine build 1 (TalkType 0.12.0 to 0.13.1) has no Parakeet in it. It
    still counts as installed for Whisper, so nobody re-downloads it."""
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    _engine(tmp_path, monkeypatch, "whisper-server")
    assert wv.is_engine_installed() and wv.is_engine_installed("small")
    assert not wv.is_engine_installed(PARAKEET_MODEL)
    _engine(tmp_path, monkeypatch, "whisper-server", "parakeet-cli", "libparakeet.so")
    assert wv.is_engine_installed(PARAKEET_MODEL)


def test_engine_download_fetches_again_when_parakeet_is_missing(tmp_path, monkeypatch):
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    _engine(tmp_path, monkeypatch, "whisper-server")
    fetched = []
    monkeypatch.setattr("tuxtalk.download_utils.download_file",
                        lambda url, *a, **k: fetched.append(url) or False)
    assert wv.make_engine_download_func("small")(lambda *a: None, None) is True
    assert fetched == []
    wv.make_engine_download_func(PARAKEET_MODEL)(lambda *a: None, None)
    assert fetched == [wv.ENGINE_URL]


def test_a_failed_engine_download_says_why(tmp_path, monkeypatch):
    """Its window said only "Download failed"."""
    from tuxtalk import download_utils
    monkeypatch.setattr(wv, "_engine_dir", lambda: str(tmp_path / "1.9.5" / wv.ENGINE_NAME))

    def offline(url, dest, **kwargs):
        raise download_utils.DownloadError("Name or service not known")
    monkeypatch.setattr(download_utils, "fetch_file", offline)
    reports = []
    assert wv.make_engine_download_func("small")(lambda *r: reports.append(r), None) is False
    assert download_utils.failure_reason(*reports[-1]) == (
        "Download failed: Name or service not known")


def test_a_download_that_fails_to_unpack_leaves_nothing_behind(tmp_path, monkeypatch):
    """Not the 22 MB archive, nor a half-unpacked folder."""
    engine = tmp_path / "1.9.5" / wv.ENGINE_NAME
    monkeypatch.setattr(wv, "_engine_dir", lambda: str(engine))

    def download(url, dest, **kwargs):
        pathlib.Path(dest).write_bytes(b"not a tar file")
        return True
    monkeypatch.setattr("tuxtalk.download_utils.download_file", download)
    assert wv.make_engine_download_func("small")(lambda *a: None, None) is False
    assert list((tmp_path / "1.9.5").iterdir()) == []


def test_two_downloads_of_the_engine_at_once_both_end_installed(tmp_path, monkeypatch):
    """The tray and Preferences finishing the same download together: one
    removed the engine folder while the other moved its copy in, and that
    window said "Could not unpack the download"."""
    import io
    import tarfile
    import threading
    from tuxtalk import download_utils
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    engine = tmp_path / "1.9.5" / wv.ENGINE_NAME
    monkeypatch.setattr(wv, "_engine_dir", lambda: str(engine))
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as tar:
        info = tarfile.TarInfo(f"{wv.ENGINE_NAME}/whisper-server")
        info.size, info.mode = 2, 0o755
        tar.addfile(info, io.BytesIO(b"#!"))
    both_downloaded = threading.Barrier(2)

    def download(url, dest, **kwargs):
        pathlib.Path(dest).write_bytes(packed.getvalue())
        both_downloaded.wait(5)
        return True
    monkeypatch.setattr("tuxtalk.download_utils.download_file", download)
    # The bad timing, made likely: both inside the swap at once, unless the
    # second waits its turn and skips it.
    both_swapping = threading.Barrier(2)
    replace_folder = download_utils.replace_folder

    def swap(staging, target, old):
        try:
            both_swapping.wait(0.5)
        except threading.BrokenBarrierError:
            pass
        replace_folder(staging, target, old)
    monkeypatch.setattr(download_utils, "replace_folder", swap)
    results = []
    runs = [threading.Thread(target=lambda: results.append(
        wv.make_engine_download_func("small")(lambda *a: None, None))) for _ in range(2)]
    for run in runs:
        run.start()
    for run in runs:
        run.join(10)
    assert results == [True, True] and wv.is_engine_installed("small")


def test_an_engine_update_that_cant_move_in_keeps_the_old_engine(tmp_path, monkeypatch):
    """The old engine was deleted before the new one moved in: a move that
    failed (or a kill between the two) left no engine at all."""
    import io
    import tarfile
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    engine = tmp_path / "1.9.5" / wv.ENGINE_NAME
    engine.mkdir(parents=True)
    (engine / "whisper-server").write_text("old")
    (engine / "whisper-server").chmod(0o755)
    monkeypatch.setattr(wv, "_engine_dir", lambda: str(engine))
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as tar:
        for name in ("whisper-server", "parakeet-cli", "libparakeet.so"):
            info = tarfile.TarInfo(f"{wv.ENGINE_NAME}/{name}")
            info.size, info.mode = 3, 0o755
            tar.addfile(info, io.BytesIO(b"new"))
    monkeypatch.setattr("tuxtalk.download_utils.download_file",
                        lambda url, dest, **k: pathlib.Path(dest).write_bytes(packed.getvalue()))
    real_rename, real_replace = os.rename, os.replace

    def fails_from_the_download(move):
        def moved(src, dst):
            if ".d" + os.sep in str(src):
                raise OSError(5, "Input/output error")
            move(src, dst)
        return moved
    monkeypatch.setattr(os, "rename", fails_from_the_download(real_rename))
    monkeypatch.setattr(os, "replace", fails_from_the_download(real_replace))
    assert wv.make_engine_download_func(PARAKEET_MODEL)(lambda *a: None, None) is False
    assert (engine / "whisper-server").read_text() == "old"


def test_an_engine_update_cut_off_part_way_is_put_back_though_the_download_fails(
        tmp_path, monkeypatch):
    """Killed between its renames, an update left the old engine only as
    .<name>.old; the next download swept it away before failing offline,
    and there was no engine left at all."""
    from tuxtalk.download_utils import STALE_PARTIAL_S
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    engine = tmp_path / "1.9.5" / wv.ENGINE_NAME
    kept = tmp_path / "1.9.5" / f".{wv.ENGINE_NAME}.old"
    kept.mkdir(parents=True)
    (kept / "whisper-server").write_text("old")
    (kept / "whisper-server").chmod(0o755)
    aged = time.time() - STALE_PARTIAL_S - 60
    os.utime(kept, (aged, aged))
    monkeypatch.setattr(wv, "_engine_dir", lambda: str(engine))
    monkeypatch.setattr("tuxtalk.download_utils.download_file", lambda *a, **k: False)
    assert wv.make_engine_download_func(PARAKEET_MODEL)(lambda *a: None, None) is False
    assert (engine / "whisper-server").read_text() == "old"
    assert wv.is_engine_installed("small")


def test_only_the_engines_own_kept_copy_is_put_back(tmp_path, monkeypatch):
    """Every hidden .old in the folder was renamed, a user's .notes.old to notes."""
    engine = tmp_path / "1.9.5" / wv.ENGINE_NAME
    engine.parent.mkdir()
    (engine.parent / ".notes.old").mkdir()
    monkeypatch.setattr(wv, "_engine_dir", lambda: str(engine))
    monkeypatch.setattr("tuxtalk.download_utils.download_file", lambda *a, **k: False)
    wv.make_engine_download_func("small")(lambda *a: None, None)
    assert [p.name for p in engine.parent.iterdir()] == [".notes.old"]


def test_each_engine_build_has_its_own_asset():
    """Released versions check their build's SHA256 (0.12.0-0.13.1: 1.9.4 b1,
    0.14.x-0.15.x: 1.9.4 b2), so a new build never reuses a published name.
    1.9.5 b1 adds Your Words for Parakeet (vulkan-engine-patches/)."""
    assert wv.ENGINE_ASSET == "talktype-whisper-vulkan-1.9.5-b1-x64.tar.gz"
    assert wv.ENGINE_TAG == "whisper-vulkan-1.9.5-b1"
    assert wv.ENGINE_NAME == "talktype-whisper-vulkan-1.9.5"


def test_wav_is_16_bit_mono_and_keeps_the_samples():
    audio = np.array([0.0, 0.5, -0.5, 1.5], dtype=np.float32)   # 1.5 must clip
    with wave.open(io.BytesIO(wv.wav_bytes(audio))) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        samples = np.frombuffer(w.readframes(4), dtype="<i2")
    assert list(samples) == [0, 16383, -16383, 32767]


def test_multipart_carries_fields_and_the_file():
    body, content_type = wv.multipart({"language": "en"}, b"RIFFdata")
    boundary = content_type.split("boundary=")[1]
    assert body.startswith(f"--{boundary}\r\n".encode())
    assert b'name="language"\r\n\r\nen\r\n' in body
    assert b'filename="audio.wav"' in body and b"RIFFdata" in body
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_reads_text_and_no_speech_from_the_reply():
    reply = {"text": " Hello there. Bye.", "segments": [
        {"text": " Hello there.", "no_speech_prob": 0.02},
        {"text": " Bye.", "no_speech_prob": 0.4}]}
    assert wv.parse_result(reply) == ("Hello there. Bye.", 0.4)
    assert wv.parse_result({"text": " Just text "}) == ("Just text", 0.0)


@pytest.mark.parametrize("gpu, cpu, worth_it", [
    (0.38, 6.2, True),      # RTX 4070 Super vs Ryzen 7800X3D, large model
    (6.27, 6.18, False),    # the Ryzen's tiny built-in graphics: a tie at best
    (0.9, 1.0, False),      # 10% isn't worth an extra engine
    (0.8, 1.0, True),
])
def test_graphics_is_used_only_when_clearly_faster(gpu, cpu, worth_it):
    assert wv.graphics_is_worth_it(gpu, cpu) is worth_it


def test_speed_check_reads_whisper_cli_timings(monkeypatch):
    runs = []

    def fake_run(cmd, cancel=None):
        runs.append(cmd)
        seconds = 6.2 if "-ng" in cmd else 0.4
        return 0, f"whisper_print_timings:    total time =  {seconds * 1000:.2f} ms\n"

    monkeypatch.setattr(wv, "_run_timed", fake_run)
    monkeypatch.setattr(wv, "model_path", lambda m: "/models/x.bin")
    assert wv.speed_check("large-v3", 1, threads=8) == (0.4, 6.2)
    assert [("-dev" in r, "-ng" in r) for r in runs] == [(True, False), (True, False), (False, True)]


def test_parakeet_speed_check_uses_parakeet_cli_without_its_load_time(monkeypatch):
    """Loading onto a graphics card takes longer than onto the processor, and
    Parakeet transcribes the sample so fast that counting the load would hide
    most of the difference."""
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    runs = []

    def timings(load, total):
        return 0, (f"parakeet_print_timings:     load time =   {load * 1000:.2f} ms\n"
                   f"parakeet_print_timings:    total time =   {total * 1000:.2f} ms\n")

    def fake_run(cmd, cancel=None):
        runs.append(cmd)
        return timings(0.08, 0.84) if "-ng" in cmd else timings(0.20, 0.26)

    monkeypatch.setattr(wv, "_run_timed", fake_run)
    monkeypatch.setattr(wv, "model_path", lambda m: "/models/p.bin")
    gpu, cpu = wv.speed_check(PARAKEET_MODEL, 0, threads=4)
    assert (round(gpu, 2), round(cpu, 2)) == (0.06, 0.76)
    assert all(r[0].endswith("parakeet-cli") for r in runs)


def test_stop_ends_a_speed_check_run_at_once(monkeypatch):
    """Stop on a hung graphics driver: the run is killed, not left to keep
    the chip and a core busy for minutes in the background."""
    import threading
    monkeypatch.setattr(wv, "_engine_env", lambda: None)
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="stopped"):
        wv._run_timed(["/bin/sleep", "30"], cancel)
    assert time.monotonic() - started < 5


def test_a_speed_check_run_goes_through_the_engine_launcher(monkeypatch):
    """So a service killed mid-check takes the run with it."""
    started = []
    real_popen = wv.subprocess.Popen

    def recording_popen(command, **k):
        started.append(command)
        return real_popen(command, **k)
    monkeypatch.setattr(wv, "_engine_env", lambda: None)
    monkeypatch.setattr(wv.subprocess, "Popen", recording_popen)
    wv._run_timed(["/bin/true"])
    assert started[0][1] == wv.engine_process.engine_launcher.__file__


def test_no_run_starts_after_stop(monkeypatch):
    import threading
    started = []
    monkeypatch.setattr(wv.subprocess, "Popen", lambda *a, **k: started.append(a))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match="stopped"):
        wv._run_timed(["whisper-cli"], cancel)
    assert started == []


def test_a_finished_run_gives_its_exit_code_and_stderr(monkeypatch):
    monkeypatch.setattr(wv, "_engine_env", lambda: None)
    assert wv._run_timed(["/bin/sh", "-c", "echo timings >&2; exit 3"]) == (3, "timings\n")


@pytest.mark.parametrize("text, cleaned", [
    (" [BLANK_AUDIO]", ""),                                   # silence, measured
    (" [MUSIC] Okay let's go [NOISE]", "Okay let's go"),
    (" I wrote [sic] in the note", "I wrote [sic] in the note"),   # real brackets stay
])
def test_non_speech_markers_are_never_typed(text, cleaned):
    reply = {"text": text, "segments": [{"text": text, "no_speech_prob": 0.0}]}
    assert wv.parse_result(reply)[0] == cleaned


# --- the dictation service ----------------------------------------------------

class FakeEngine(wv.VulkanWhisperModel):
    def __init__(self, model_name, device_index):
        self.model_name, self.device_index = model_name, device_index

    def transcribe(self, audio, language=None, prompt=None, limit_to=()):
        return "Thank you.", 0.95          # a high, untrustworthy no-speech score


class SignOffEngine(wv.VulkanWhisperModel):
    """whisper.cpp's best-known invention from silence, with its 0.00 score."""
    def __init__(self):
        pass

    def transcribe(self, audio, language=None, prompt=None, limit_to=()):
        return "Thanks for watching!", 0.0


def _audio(voiced):
    rng = np.random.default_rng(1)
    t = np.arange(3 * 16000) / 16000
    audio = rng.normal(0, 60, t.size)
    if voiced:   # a second of speech in the middle
        audio += np.sin(2 * np.pi * 220 * t) * 3000 * ((t > 1) & (t < 2))
    return (audio / 32768.0).astype(np.float32)


def test_vulkan_drops_a_sign_off_invented_from_silence(monkeypatch):
    """whisper.cpp's no-speech score is 0.00 on silence, so without a stand-in
    a held key with nothing said would type "Thanks for watching!"."""
    monkeypatch.setattr(engine, "model", SignOffEngine(), raising=False)
    assert engine.transcribe(_audio(voiced=False), "en") is None


def test_vulkan_keeps_a_sign_off_said_aloud(monkeypatch):
    monkeypatch.setattr(engine, "model", SignOffEngine(), raising=False)
    assert engine.transcribe(_audio(voiced=True), "en") == "Thanks for watching!"


def _settings(**kw):
    from tuxtalk.config import Settings
    return Settings(**{"model": "small", "device": "vulkan", **kw})


def test_service_runs_whisper_on_the_amd_or_intel_chip(monkeypatch):
    monkeypatch.setattr(wv, "is_installed", lambda m: True)
    monkeypatch.setattr(wv, "find_device", lambda: 1)
    monkeypatch.setattr(wv, "VulkanWhisperModel", FakeEngine)
    model = engine.build(_settings())
    assert isinstance(model, FakeEngine) and (model.model_name, model.device_index) == ("small", 1)


def test_service_falls_back_to_the_processor_when_the_engine_is_missing(monkeypatch):
    loaded, told = [], []
    monkeypatch.setattr(wv, "is_installed", lambda m: False)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a: told.append(a))
    monkeypatch.setattr("tuxtalk.model_helper.download_model_with_progress",
                        lambda model, device, compute_type, **k: loaded.append(device) or "cpu-model")
    settings = _settings()
    assert engine.build(settings) == "cpu-model"
    assert loaded == ["cpu"] and told
    assert settings.device == "vulkan"        # the user's choice is not overwritten


def test_parakeet_never_uses_the_whisper_engine(monkeypatch):
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    monkeypatch.setattr(wv, "VulkanWhisperModel", lambda *a: pytest.fail("used the Whisper engine"))
    monkeypatch.setattr(wv, "is_installed", lambda m: False)
    monkeypatch.setattr("tuxtalk.model_helper.download_model_with_progress",
                        lambda model, device, compute_type, **k: "parakeet-model")
    assert engine.build(_settings(model=PARAKEET_MODEL)) == "parakeet-model"


class FakeParakeetGpu:
    def __init__(self, device_index):
        self.device_index = device_index

    def recognize(self, audio):
        return "Hello there."


def test_service_runs_parakeet_on_the_graphics_chip(monkeypatch):
    from tuxtalk import parakeet_gpu
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    monkeypatch.setattr(wv, "is_installed", lambda m: True)
    monkeypatch.setattr(wv, "find_device", lambda: 0)
    monkeypatch.setattr(parakeet_gpu, "ParakeetGpuModel", FakeParakeetGpu)
    model = engine.build(_settings(model=PARAKEET_MODEL))
    assert isinstance(model, FakeParakeetGpu) and model.device_index == 0


def test_parakeet_on_vulkan_without_its_files_quietly_uses_the_processor(monkeypatch):
    """Someone on 0.13.1 with Vulkan as the device and Parakeet as the model
    has never downloaded Parakeet's graphics files. They keep the processor,
    as before, without a warning at every start."""
    from tuxtalk import parakeet_gpu
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    told = []
    monkeypatch.setattr(wv, "is_installed", lambda m: False)
    monkeypatch.setattr(parakeet_gpu, "ParakeetGpuModel", lambda *a: pytest.fail("started the helper"))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a: told.append(a))
    monkeypatch.setattr("tuxtalk.model_helper.download_model_with_progress",
                        lambda model, device, compute_type, **k: f"parakeet-on-{device}")
    assert engine.build(_settings(model=PARAKEET_MODEL)) == "parakeet-on-cpu"
    assert told == []


def test_parakeet_falls_back_to_the_processor_when_the_helper_fails(monkeypatch):
    from tuxtalk import parakeet_gpu
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    told = []

    def broken(device_index):
        raise RuntimeError("driver said no")

    monkeypatch.setattr(wv, "is_installed", lambda m: True)
    monkeypatch.setattr(wv, "find_device", lambda: 0)
    monkeypatch.setattr(parakeet_gpu, "ParakeetGpuModel", broken)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a: told.append(a))
    monkeypatch.setattr("tuxtalk.model_helper.download_model_with_progress",
                        lambda model, device, compute_type, **k: f"parakeet-on-{device}")
    settings = _settings(model=PARAKEET_MODEL)
    assert engine.build(settings) == "parakeet-on-cpu"
    assert told and settings.device == "vulkan"


def test_parakeet_on_the_graphics_chip_transcribes(monkeypatch):
    from tuxtalk import parakeet_gpu
    gpu = parakeet_gpu.ParakeetGpuModel.__new__(parakeet_gpu.ParakeetGpuModel)
    monkeypatch.setattr(parakeet_gpu.ParakeetGpuModel, "recognize", lambda self, a: "Hello there.")
    monkeypatch.setattr(engine, "model", gpu, raising=False)
    assert engine.transcribe(np.zeros(16000, dtype=np.float32), None) == "Hello there."


def test_transcription_ignores_the_engines_no_speech_score(monkeypatch):
    """A real "Thank you." must survive even though the engine scored it 0.95.
    Real means the recording has speech in it; silence has its own test."""
    monkeypatch.setattr(engine, "model", FakeEngine("small", 1), raising=False)
    assert engine.transcribe(_audio(voiced=True), "en") == "Thank you."


# --- first-run setup: the light choice for NVIDIA ---------------------------------

def test_first_run_model_download_is_skipped_when_already_there(monkeypatch):
    from tuxtalk import first_run_setup as fr
    monkeypatch.setattr(wv, "is_engine_installed", lambda *a: True)
    monkeypatch.setattr(wv, "model_path", lambda m: "/models/x.bin")
    assert fr._download_vulkan_model_first_run("small") == (True, False)


def test_first_run_parakeet_on_vulkan_fetches_an_engine_that_can_run_it(monkeypatch):
    """Engine build 1 can't run Parakeet, so it's fetched again with the model."""
    from tuxtalk import first_run_setup as fr
    import tuxtalk.download_progress_dialog as dpd
    added = []

    class Dialog:
        def __init__(self, **kw): pass
        def add_task(self, task): added.append(task.name)
        def run(self): return {}

    monkeypatch.setattr(wv, "is_engine_installed", lambda *a: not a or a[0] != "parakeet-v3")
    monkeypatch.setattr(wv, "model_path", lambda m: None)
    monkeypatch.setattr(dpd, "UnifiedDownloadDialog", Dialog)
    assert fr._download_vulkan_model_first_run("parakeet-v3") == (False, False)  # not cancelled
    assert added == ["Graphics engine", "Speech model"]


def test_first_run_vulkan_download_reports_a_cancel(monkeypatch):
    from tuxtalk import first_run_setup as fr
    import tuxtalk.download_progress_dialog as dpd

    class Dialog:
        def __init__(self, **kw): pass
        def add_task(self, task): pass
        def run(self):
            return {"Speech model": {"success": False, "cancelled": True, "completed": False}}

    monkeypatch.setattr(wv, "is_engine_installed", lambda *a: True)
    monkeypatch.setattr(wv, "model_path", lambda m: None)
    monkeypatch.setattr(dpd, "UnifiedDownloadDialog", Dialog)
    assert fr._download_vulkan_model_first_run("small") == (False, True)


def test_first_run_vulkan_uses_the_preferences_speed_check(monkeypatch):
    """First run times Vulkan before switching to it, so weak built-in
    graphics can't end up slower than the processor."""
    from tuxtalk import first_run_setup as fr
    seen = []
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.run_speed_check",
                        lambda parent, model: seen.append(model) or False)
    assert fr._vulkan_speed_check_first_run("parakeet-v3") is False
    assert seen == ["parakeet-v3"]


def test_first_run_speed_check_error_keeps_the_processor(monkeypatch):
    from tuxtalk import first_run_setup as fr

    def boom(parent, model):
        raise RuntimeError("no Vulkan device")

    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.run_speed_check", boom)
    assert fr._vulkan_speed_check_first_run("small") is False


@pytest.mark.parametrize("times,slower", [((1.0, 5.0), False), ((5.0, 1.0), True)])
def test_speed_check_remembers_which_won(monkeypatch, times, slower):
    """recommend.py must not keep proposing a slower chip."""
    from tuxtalk import vulkan_setup_dialogs as vsd
    saved = {}
    cfg = types.SimpleNamespace(vulkan_slower=not slower)
    monkeypatch.setattr(vsd.wv, "find_device", lambda: 0)
    monkeypatch.setattr(vsd.wv, "speed_check", lambda model, dev, cancel: times)
    monkeypatch.setattr(vsd, "message", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.config.load_config", lambda: cfg)
    monkeypatch.setattr("tuxtalk.config.save_config", lambda c: saved.update(slower=c.vulkan_slower))
    monkeypatch.setattr(vsd, "_wait_dialog", lambda parent, work: work())
    vsd.run_speed_check(None, "small")
    assert saved == {"slower": slower}


# --- the server process ------------------------------------------------------

# A stand-in whisper-server: /health answers once it is up, /inference
# answers with fixed text, or with an error when started with FAKE_FAIL_INFERENCE
# in its environment (so the warm-up fails); FAKE_FAIL_START makes it print
# that and end at once.
FAKE_SERVER = '''
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[sys.argv.index("--port") + 1])
fail = os.environ.get("FAKE_FAIL_INFERENCE") == "1"
if os.environ.get("FAKE_FAIL_START"):
    sys.exit(os.environ["FAKE_FAIL_START"])
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if fail:
            self.send_response(500); self.end_headers(); return
        body = json.dumps({"text": "hello", "segments": []}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


@pytest.fixture
def fake_server(tmp_path, monkeypatch):
    import sys
    script = tmp_path / "whisper-server"
    script.write_text(f"#!{sys.executable}\n" + FAKE_SERVER)
    script.chmod(0o755)
    monkeypatch.setattr(wv, "_binary", lambda name: str(script))
    monkeypatch.setattr(wv, "model_path", lambda name: "model.bin")
    monkeypatch.setattr(wv.engine_process, "kill_orphans", lambda *a, **k: 0)
    return monkeypatch


def test_a_restarted_server_outlives_the_short_lived_thread_that_restarted_it(fake_server):
    """The server dies with the thread that started it. A restart from a
    dictation thread that then ends must leave a server that keeps working."""
    import threading
    model = wv.VulkanWhisperModel("small", 0, threads=1)
    try:
        model._proc.kill()
        model._proc.wait()
        results = []
        t = threading.Thread(target=lambda: results.append(model.transcribe(np.zeros(16000, np.float32))))
        t.start()
        t.join()                                   # restarted the server, then ended
        assert results == [("hello", 0.0)]
        restarted = model._proc
        time.sleep(0.3)
        assert restarted.poll() is None            # still running
        assert model.transcribe(np.zeros(16000, np.float32)) == ("hello", 0.0)
        assert model._proc is restarted            # no second restart
    finally:
        model.close()


def test_the_graphics_engine_starts_through_the_engine_launcher(fake_server, monkeypatch):
    """So it dies with the service (test_engine_parent_death)."""
    started = []
    real_popen = wv.subprocess.Popen

    def recording_popen(command, **k):
        started.append((command, k))
        return real_popen(command, **k)
    monkeypatch.setattr(wv.subprocess, "Popen", recording_popen)
    model = wv.VulkanWhisperModel("small", 0, threads=1)
    try:
        command, kwargs = started[0]
        assert command[1] == wv.engine_process.engine_launcher.__file__
        assert "preexec_fn" not in kwargs
    finally:
        model.close()


def test_a_server_that_ends_at_start_says_what_it_printed(fake_server, monkeypatch):
    """Its messages went nowhere: the log said only that it stopped."""
    monkeypatch.setenv("FAKE_FAIL_START", "no Vulkan device found")
    with pytest.raises(RuntimeError, match="exit code 1: no Vulkan device found"):
        wv.VulkanWhisperModel("small", 0, threads=1)


def test_a_failed_warm_up_stops_the_server(fake_server, monkeypatch):
    """A failed warm-up must not leave whisper-server holding the model in
    graphics memory for the whole session."""
    monkeypatch.setenv("FAKE_FAIL_INFERENCE", "1")
    started = []
    real_popen = wv.subprocess.Popen

    def recording_popen(*a, **k):
        started.append(real_popen(*a, **k))
        return started[-1]
    monkeypatch.setattr(wv.subprocess, "Popen", recording_popen)
    with pytest.raises(OSError):           # the server's error reply (HTTPError)
        wv.VulkanWhisperModel("small", 0, threads=1)
    assert started and started[0].poll() is not None
