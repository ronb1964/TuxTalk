"""Starting the service when its speech model is not downloaded yet.

Cancel on the startup "Download model?" question is honoured on NVIDIA setups
too, not taken for a CUDA failure that the CUDA fallback answers by
downloading without asking. And a startup download's dialogs run before
Gtk.main() is live on another thread, or they crash the service.
"""
from types import SimpleNamespace

import pytest

from tuxtalk import model_helper
from tuxtalk import engine
from tuxtalk import gtk_loop


def _settings(model="small", device="cuda"):
    return SimpleNamespace(model=model, device=device)


@pytest.fixture(autouse=True)
def cuda_installed(monkeypatch):
    """These tests are about downloads and load failures with CUDA present."""
    from tuxtalk import cuda_helper
    monkeypatch.setattr(cuda_helper, "has_cuda_libraries", lambda: True)


def test_missing_cuda_files_use_the_processor_instead_of_crashing(monkeypatch):
    """Device CUDA with the CUDA files gone: the model still loads "on cuda",
    then the first dictation crashes the service inside the speech library
    (libcudnn_ops.so.9 not found), past every Python fallback. So use the
    processor for this run and say why; keep the setting."""
    from tuxtalk import cuda_helper
    monkeypatch.setattr(cuda_helper, "has_cuda_libraries", lambda: False)
    loaded, notes = [], []
    monkeypatch.setattr(model_helper, "download_model_with_progress",
                        lambda *a, **k: loaded.append(k.get("device")) or object())
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a: notes.append(a))
    settings = _settings(device="cuda")
    engine.build(settings)
    assert loaded == ["cpu"]
    assert settings.device == "cuda"          # the user's choice is kept
    assert notes and "CUDA" in notes[0][1]
    assert "General, Device" in notes[0][1]   # where the CUDA download is offered


def test_cancel_is_respected_on_an_nvidia_setup(monkeypatch):
    calls = []
    monkeypatch.setattr(model_helper, "download_model_with_progress",
                        lambda *a, **k: calls.append(k) or None)
    with pytest.raises(engine.ModelUnavailable):
        engine.build(_settings(device="cuda"))
    assert len(calls) == 1   # asked once; no silent CPU retry


def test_cancel_stops_the_service_when_no_other_model_is_downloaded(monkeypatch):
    monkeypatch.setattr(model_helper, "download_model_with_progress", lambda *a, **k: None)
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda m: False)
    with pytest.raises(SystemExit):
        engine.build_or_exit(_settings())


def test_parakeet_never_takes_the_cuda_fallback(monkeypatch):
    calls = []

    def fail(*a, **k):
        calls.append(k)
        raise RuntimeError("load failed")

    monkeypatch.setattr(model_helper, "download_model_with_progress", fail)
    with pytest.raises(RuntimeError):
        engine.build(_settings(model="parakeet-v3", device="cuda"))
    assert len(calls) == 1


def test_a_real_cuda_failure_still_falls_back_to_cpu(monkeypatch):
    calls = []

    def load(*a, **k):
        calls.append(k.get("device"))
        if k.get("device") == "cuda":
            raise RuntimeError("CUDA driver missing")
        return object()

    monkeypatch.setattr(model_helper, "download_model_with_progress", load)
    monkeypatch.setattr("tuxtalk.config.save_config", lambda cfg: None)
    assert engine.build(_settings(device="cuda")) is not None
    assert calls == ["cuda", "cpu"]


def test_a_needed_download_runs_on_gtk_s_thread(service_main):
    """Only one thread may drive GTK: the download's windows run on the
    thread Gtk.main() runs on, not the service's main thread."""
    started = service_main(cached=False)
    assert started.index("starts the GTK loop") < started.index("loads the speech model")
    assert engine.build_or_exit in started.on_gtk


def test_a_downloaded_model_loads_while_gtk_answers_d_bus(service_main):
    started = service_main(cached=True)
    assert started.index("starts the GTK loop") < started.index("loads the speech model")
    assert engine.build_or_exit not in started.on_gtk


def test_the_indicator_and_the_d_bus_stand_in_are_made_on_gtk_s_thread(service_main):
    """Made on the main thread, they were run by Gtk.main() on another."""
    started = service_main()
    assert started.index("starts the GTK loop") < started.index("offers its D-Bus service")
    assert [fn.__name__ for fn in started.on_gtk] == ["_make_gtk_parts"]


@pytest.mark.parametrize("wanted,downloaded,cuda,expected", [
    ("parakeet-v3", ["small", "large-v3"], True, "large-v3"),   # nearest in quality
    ("parakeet-v3", ["small", "large-v3"], False, "small"),     # large-v3 needs CUDA
    ("parakeet-v3", ["medium", "large-v3"], True, "medium"),    # tie -> the faster one
    ("base", ["tiny", "small"], False, "tiny"),                 # tie -> the faster one
    ("small", ["tiny", "medium"], False, "medium"),             # medium is one step away, tiny two
    ("medium", [], False, None),                                # nothing to fall back to
    ("tiny", ["tiny"], False, None),                            # never "falls back" to itself
])
def test_fallback_picks_the_nearest_downloaded_model(wanted, downloaded, cuda, expected):
    assert engine._pick_fallback_model(wanted, downloaded, cuda) == expected


def test_cancel_falls_back_to_a_downloaded_model_and_saves_it(monkeypatch):
    import tuxtalk.config as config
    from tuxtalk import cuda_helper

    def download(name, **k):
        return None if name == "parakeet-v3" else f"model:{name}"

    saved, notes = [], []
    monkeypatch.setattr(model_helper, "download_model_with_progress", download)
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda m: m in ("small", "large-v3"))
    monkeypatch.setattr(cuda_helper, "has_cuda_libraries", lambda: False)
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(model="parakeet-v3"))
    monkeypatch.setattr(config, "save_config", lambda cfg: saved.append(cfg.model))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: notes.append(f"{title}\n{body}"))

    settings = _settings(model="parakeet-v3", device="cpu")
    assert engine.build_or_exit(settings) == "model:small"
    assert settings.model == "small" and saved == ["small"]
    assert "Parakeet wasn't downloaded" in notes[0] and "Small" in notes[0]


def test_a_cancelled_cpu_retry_is_a_missing_model_not_a_crash(monkeypatch):
    """CUDA failed, then the processor copy was cancelled. That must reach
    _build_model_or_exit as ModelUnavailable, which it handles by using a
    downloaded model, not as a bare Exception that crashes the service."""
    def load(*a, **k):
        if k.get("device") == "cuda":
            raise RuntimeError("CUDA driver missing")
        return None

    monkeypatch.setattr(model_helper, "download_model_with_progress", load)
    with pytest.raises(engine.ModelUnavailable):
        engine.build(_settings(device="cuda"))


def test_a_model_load_error_is_not_reported_as_a_gtk_failure(service_main, monkeypatch, caplog):
    """The early build must not sit inside the GTK setup's try, or a load
    error is logged as "GTK initialization failed", the GTK loop never starts,
    and the model is then built a second time."""
    builds = []

    def broken(cfg):
        builds.append(cfg.model)
        raise RuntimeError("bad model.bin")
    monkeypatch.setattr(engine, "build_or_exit", broken)
    with pytest.raises(RuntimeError, match="bad model.bin"):
        service_main(cached=False)
    assert builds == ["parakeet-v3"]
    assert "GTK initialization failed" not in caplog.text


def test_broken_files_raise_instead_of_opening_dialogs_off_the_gtk_thread(monkeypatch):
    """With Gtk.main() on another thread, the service's main thread must not
    run the download dialogs for a model whose files won't load."""
    def broken(*a, **k):
        raise RuntimeError("bad model.bin")

    def no_dialogs(*a, **k):
        raise AssertionError("a GTK dialog was opened")

    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda m: True)
    monkeypatch.setattr(model_helper, "load_model", broken)
    monkeypatch.setattr(model_helper, "_loads_on_cpu", lambda m: False)
    monkeypatch.setattr(model_helper, "message_dialog", no_dialogs)
    monkeypatch.setattr(model_helper.Gtk, "Dialog", no_dialogs)
    with pytest.raises(model_helper.BrokenModelFiles):
        model_helper.download_model_with_progress("small", dialogs=False)


def test_power_saving_never_opens_a_download_off_the_gtk_thread(monkeypatch):
    """Power Saving loads the model again on a dictation's thread. If its
    files went missing meanwhile, the download dialogs would open there, with
    Gtk.main() running on another thread (review of 0.17). It is treated as
    unavailable and the user is told instead."""
    def no_dialogs(*a, **k):
        raise AssertionError("a GTK dialog was opened")

    shown = []
    monkeypatch.setattr(engine, "model", None)
    monkeypatch.setattr(gtk_loop, "_thread", object())       # GTK runs on another thread
    monkeypatch.setattr(engine, "load_config", lambda: _settings(device="cpu"))
    checked = []
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda m: checked.append(m) or False)
    monkeypatch.setattr(model_helper, "message_dialog", no_dialogs)
    monkeypatch.setattr(model_helper.Gtk, "Dialog", no_dialogs)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: shown.append(body))
    assert engine.wake() is None
    assert "small" in checked          # the settings above, not the user's own
    assert shown and "Restart Service" in shown[0]


def test_the_service_treats_broken_files_as_an_unavailable_model(monkeypatch):
    asked = []

    def broken(*a, **k):
        asked.append(k.get("dialogs"))
        raise model_helper.BrokenModelFiles("small won't load")

    monkeypatch.setattr(gtk_loop, "_thread", object())       # GTK runs on another thread
    monkeypatch.setattr(model_helper, "download_model_with_progress", broken)
    with pytest.raises(engine.ModelUnavailable):
        engine.build(_settings(device="cpu"))
    assert asked == [False]


def test_a_model_that_wont_load_falls_back_without_replacing_the_choice(monkeypatch):
    """Downloaded files that won't load are not "wasn't downloaded", and the
    fallback is not saved over the choice: replaced at every start, the model
    could not be fixed by choosing it again in Preferences, which downloads
    nothing."""
    import tuxtalk.config as config
    from tuxtalk import cuda_helper

    def load(name, **k):
        if name == "medium":
            raise model_helper.BrokenModelFiles("medium won't load")
        return f"model:{name}"

    saved, notes = [], []
    monkeypatch.setattr(model_helper, "download_model_with_progress", load)
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda m: m in ("small", "medium"))
    monkeypatch.setattr(cuda_helper, "has_cuda_libraries", lambda: False)
    monkeypatch.setattr(config, "save_config", lambda cfg: saved.append(cfg.model))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: notes.append(f"{title}\n{body}"))

    settings = _settings(model="medium", device="cpu")
    assert engine.build_or_exit(settings) == "model:small"
    assert settings.model == "small" and saved == []
    assert "wouldn't load" in notes[0] and "wasn't downloaded" not in notes[0]
