"""CUDA is offered only where the speech library can use it.

A distro-packaged TuxTalk (install type "system", such as an AUR package) may
run a CTranslate2 compiled without CUDA. There, even with an NVIDIA card and
CUDA's libraries installed, Preferences must not offer CUDA, a saved "cuda"
shows as the processor, a backup with CUDA restores onto another device, and
System check doesn't send the user to pick CUDA. A source install's venv,
made with --system-site-packages, may use the distro's CTranslate2 the same
way. Every other install runs PyPI's CTranslate2, which has CUDA, and is never
checked.
"""
import importlib.util
import subprocess
import sys
from types import SimpleNamespace

try:
    import tomllib
except ImportError:
    import toml as tomllib

import pytest

from tuxtalk import config, cuda_helper, engine, installation, prefs_speech, settings_backup
from tuxtalk import prefs as P
from tuxtalk.prefs import PreferencesWindow
from tuxtalk.prefs_speech import SpeechSection
from tuxtalk.prefs_system_check import SystemCheck


@pytest.fixture(autouse=True)
def fresh_answers(monkeypatch):
    """Each test asks afresh: both answers are cached for the whole run."""
    monkeypatch.setattr(cuda_helper, "_cuda_build", None)
    installation.system_package.cache_clear()
    yield
    installation.system_package.cache_clear()


def _answer(text):
    return lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout=text, stderr="")


# --- the check itself ----------------------------------------------------------

def test_a_ctranslate2_built_without_cuda_is_seen():
    assert cuda_helper.probe_ctranslate2_cuda(run=_answer("no-cuda\n")) is False


def test_a_cuda_build_counts_even_with_no_card_now():
    assert cuda_helper.probe_ctranslate2_cuda(run=_answer("cuda\n")) is True


def test_a_check_that_hangs_gives_no_answer():
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    assert cuda_helper.probe_ctranslate2_cuda(run=hang) is None


def test_a_check_that_fails_gives_no_answer():
    def crashed(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="",
                                           stderr="ModuleNotFoundError: ctranslate2")
    assert cuda_helper.probe_ctranslate2_cuda(run=crashed) is None


def test_a_check_without_an_answer_offers_no_cuda_now_and_asks_again_next_time(monkeypatch):
    """A check that timed out at a busy login doesn't hide a working CUDA
    for as long as TuxTalk runs; a clean answer is kept."""
    answers = [None, True]
    monkeypatch.setattr(installation, "get_install_type", lambda: "system")
    monkeypatch.setattr(cuda_helper, "probe_ctranslate2_cuda", lambda **kw: answers.pop(0))
    assert cuda_helper.ctranslate2_has_cuda() is False
    assert cuda_helper.ctranslate2_has_cuda() is True
    assert cuda_helper.ctranslate2_has_cuda() is True and answers == []


def test_the_check_never_wakes_the_graphics_card():
    seen = {}

    def run(argv, **kw):
        seen.update(kw["env"])
        return _answer("cuda\n")(argv)
    cuda_helper.probe_ctranslate2_cuda(run=run)
    assert seen["CUDA_VISIBLE_DEVICES"] == ""


def test_the_check_asks_the_python_tuxtalk_runs_on():
    argvs = []
    cuda_helper.probe_ctranslate2_cuda(run=lambda argv, **kw: argvs.append(argv)
                                       or _answer("cuda\n")(argv), python="/usr/bin/python3")
    assert argvs[0][:2] == ["/usr/bin/python3", "-c"]


def test_the_check_finds_a_ctranslate2_from_pip_user_but_none_in_the_current_folder():
    """Not isolated, so it sees the user's site and PYTHONPATH as TuxTalk
    does; run in /, so a ctranslate2.py where TuxTalk was started can't
    answer for the real one."""
    calls = []
    cuda_helper.probe_ctranslate2_cuda(run=lambda argv, **kw: calls.append((argv, kw))
                                       or _answer("cuda\n")(argv))
    argv, kw = calls[0]
    assert "-I" not in argv and "-s" not in argv
    assert kw["cwd"] == "/"


def _no_probe(**kw):
    raise AssertionError("only a system install is checked")


@pytest.mark.parametrize("kind", ["appimage", "aur", "deb", "rpm", "package", "flatpak",
                                  "dev"])
def test_only_a_system_install_is_checked(monkeypatch, kind):
    monkeypatch.setattr(installation, "get_install_type", lambda: kind)
    monkeypatch.setattr(cuda_helper, "probe_ctranslate2_cuda", _no_probe)
    assert cuda_helper.ctranslate2_has_cuda() is True


def _source_venv_finding_ctranslate2_at(monkeypatch, origin):
    """A source install running from /home/ann/venv whose Python finds
    CTranslate2 at *origin*."""
    monkeypatch.setattr(installation, "get_install_type", lambda: "source")
    monkeypatch.setattr(sys, "prefix", "/home/ann/venv")
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: SimpleNamespace(origin=origin)
                        if name == "ctranslate2" else real_find_spec(name, *a))


def test_a_source_install_with_ctranslate2_in_its_venv_is_never_checked(monkeypatch):
    _source_venv_finding_ctranslate2_at(
        monkeypatch, "/home/ann/venv/lib/python3.14/site-packages/ctranslate2/__init__.py")
    monkeypatch.setattr(cuda_helper, "probe_ctranslate2_cuda", _no_probe)
    assert cuda_helper.ctranslate2_has_cuda() is True


def test_a_source_venv_using_the_distros_ctranslate2_is_checked(monkeypatch):
    """pip keeps the system's CTranslate2 when its version meets the pin, and
    the distro may have built it without CUDA."""
    _source_venv_finding_ctranslate2_at(
        monkeypatch, "/usr/lib64/python3.14/site-packages/ctranslate2/__init__.py")
    monkeypatch.setattr(cuda_helper, "probe_ctranslate2_cuda", lambda **kw: False)
    assert cuda_helper.ctranslate2_has_cuda() is False


def test_the_real_check_finds_cuda_in_pypis_ctranslate2():
    """The probe script itself, run for real: a typo in it would read as "no
    CUDA" and hide CUDA from every install that is checked."""
    pytest.importorskip("ctranslate2")
    assert cuda_helper.probe_ctranslate2_cuda() is True


def test_a_system_install_is_checked_once(monkeypatch):
    probes = []
    monkeypatch.setattr(installation, "get_install_type", lambda: "system")
    monkeypatch.setattr(cuda_helper, "probe_ctranslate2_cuda",
                        lambda **kw: probes.append(1) or False)
    assert cuda_helper.ctranslate2_has_cuda() is False
    assert cuda_helper.ctranslate2_has_cuda() is False
    assert probes == [1]


# --- Preferences ---------------------------------------------------------------

class FakeCombo:
    def __init__(self):
        self.ids, self.active = [], None

    def remove_all(self):
        self.ids = []

    def append(self, id_, label):
        self.ids.append(id_)

    def set_active_id(self, id_):
        self.active = id_

    def set_tooltip_text(self, text):
        pass


@pytest.fixture
def without_cuda_build(monkeypatch):
    """A system install whose CTranslate2 was built without CUDA."""
    monkeypatch.setattr(installation, "get_install_type", lambda: "system")
    monkeypatch.setattr(cuda_helper, "probe_ctranslate2_cuda", lambda **kw: False)


@pytest.fixture
def prefs_on_nvidia_without_cuda_build(tmp_path, monkeypatch, without_cuda_build):
    """The Speech section of Preferences, saved on CUDA, on an NVIDIA computer
    with the system's CUDA libraries installed and Vulkan offered."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('device = "cuda"\nbeeps = true\n')
    monkeypatch.setattr(P, "CONFIG_PATH", str(cfg))
    opened = {"device": "cuda", "beeps": True, "model": "small", "language_mode": "auto"}
    win = SimpleNamespace(config=dict(opened), _config_at_open=dict(opened),
                          cuda_files_present=lambda: True, has_nvidia=lambda: True,
                          vulkan_offered=lambda: True)
    win.cuda_ready = lambda: PreferencesWindow.cuda_ready(win)
    win.cuda_offered = lambda: PreferencesWindow.cuda_offered(win)
    win.show_without_saving = lambda key, value: PreferencesWindow.show_without_saving(
        win, key, value)
    section = SimpleNamespace(win=win, config=win.config, window=None,
                              device_combo=FakeCombo())
    SpeechSection._fill_device_options(section)
    return section


def test_device_lists_no_cuda_where_the_speech_library_cant_use_it(
        prefs_on_nvidia_without_cuda_build):
    assert prefs_on_nvidia_without_cuda_build.device_combo.ids == ["cpu", "vulkan"]


def test_a_saved_cuda_shows_as_cpu_there_and_stays_saved(prefs_on_nvidia_without_cuda_build):
    section = prefs_on_nvidia_without_cuda_build
    assert section.device_combo.active == "cpu"

    section.config["beeps"] = False                 # the user's only change
    assert PreferencesWindow.save_config(section.win) is True
    with open(P.CONFIG_PATH) as f:
        assert tomllib.loads(f.read())["device"] == "cuda"


def test_large_v3_there_is_offered_through_vulkan(prefs_on_nvidia_without_cuda_build,
                                                  monkeypatch):
    section = prefs_on_nvidia_without_cuda_build
    asked, switched = [], []
    monkeypatch.setattr(prefs_speech, "show_message",
                        lambda parent, kind, title, text="", buttons=None, keep_above=False:
                        asked.append(text) or prefs_speech.Gtk.ResponseType.OK)
    from tuxtalk import vulkan_setup_dialogs
    monkeypatch.setattr(vulkan_setup_dialogs, "choose_light_or_full",
                        lambda parent, model: pytest.fail("CUDA offered as Full"))
    section._switch_to_vulkan_for_model = (
        lambda model, confirm=True: switched.append((model, confirm)))

    SpeechSection._set_up_large_v3(section)

    assert asked and asked[0].startswith("Large-v3 can run on your graphics card through Vulkan.")
    assert switched == [("large-v3", False)]


def test_large_v3_there_reads_as_running_through_vulkan(prefs_on_nvidia_without_cuda_build):
    assert (SpeechSection._large_v3_text(prefs_on_nvidia_without_cuda_build)
            == "large-v3 — Whisper's best, on your graphics card (Vulkan)")


def test_picking_large_v3_there_sets_it_up_rather_than_confirming_3_gb(
        prefs_on_nvidia_without_cuda_build):
    section = prefs_on_nvidia_without_cuda_build
    section.config["device"] = "cpu"
    done = []
    section._updating_model = False
    section._last_selected_model = "small"
    section.set_choice = lambda key, value: done.append(("back to", value))
    section._set_up_large_v3 = lambda: done.append("set up")
    section._confirm_large_v3_download = lambda: done.append("3 GB?") or True
    section._choose_model = lambda model: done.append(("chosen", model))
    combo = SimpleNamespace(popdown=lambda: None, get_active_id=lambda: "large-v3")

    SpeechSection._on_model_changed(section, combo)

    assert done == [("back to", "small"), "set up"]


def test_large_v3_there_without_vulkan_says_the_speech_library_cant_use_cuda(
        monkeypatch, without_cuda_build):
    win = SimpleNamespace(cuda_files_present=lambda: True, has_nvidia=lambda: True,
                          vulkan_offered=lambda: False)
    win.cuda_ready = lambda: PreferencesWindow.cuda_ready(win)
    win.cuda_offered = lambda: PreferencesWindow.cuda_offered(win)
    assert (SpeechSection._large_v3_text(SimpleNamespace(win=win))
            == "large-v3 — needs CUDA, which the speech library installed here can't use")


def test_a_cuda_build_still_lists_cuda_on_nvidia(monkeypatch):
    """Every other install type: as before, CUDA is listed on NVIDIA."""
    monkeypatch.setattr(installation, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(cuda_helper, "probe_ctranslate2_cuda", _no_probe)
    win = SimpleNamespace(config={"device": "cuda", "model": "small"},
                          cuda_files_present=lambda: True, has_nvidia=lambda: True,
                          vulkan_offered=lambda: True)
    win.cuda_ready = lambda: PreferencesWindow.cuda_ready(win)
    win.cuda_offered = lambda: PreferencesWindow.cuda_offered(win)
    section = SimpleNamespace(win=win, config=win.config, device_combo=FakeCombo())
    SpeechSection._fill_device_options(section)
    assert section.device_combo.ids == ["cpu", "cuda", "vulkan"]
    assert section.device_combo.active == "cuda"


# --- backups and System check --------------------------------------------------

def test_a_backup_with_cuda_restores_onto_another_device_there(monkeypatch, without_cuda_build):
    monkeypatch.setattr(cuda_helper, "detect_nvidia_gpu", lambda: "NVIDIA GeForce RTX 4070")
    assert settings_backup._device_works_here("cuda") is False


def test_the_restore_note_there_blames_the_speech_library_not_the_card(
        tmp_path, monkeypatch, without_cuda_build):
    monkeypatch.setattr(cuda_helper, "detect_nvidia_gpu", lambda: "NVIDIA GeForce RTX 4070")
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.toml"))
    notes = []
    text = settings_backup._fit_device('device = "cuda"\n', notes)
    assert text == 'device = "cpu"\n'
    assert notes == ["The backup uses CUDA, which the speech library installed here "
                     "can't use, so the Device stays on CPU."]


def test_a_saved_cuda_there_loads_on_the_processor_without_a_cuda_notice(
        monkeypatch, without_cuda_build):
    """Not loaded on CUDA first: that fails, then says "choose the device
    again to try the graphics card", where Preferences lists no CUDA."""
    from tuxtalk import model_helper
    monkeypatch.setattr(cuda_helper, "has_cuda_libraries", lambda: True)
    loaded, notes = [], []
    monkeypatch.setattr(model_helper, "download_model_with_progress",
                        lambda *a, **k: loaded.append(k.get("device")) or object())
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a: notes.append(a))
    engine.build(SimpleNamespace(model="small", device="cuda"))
    assert loaded == ["cpu"] and notes == []


def test_large_v3_there_runs_as_medium_rather_than_on_the_processor(
        monkeypatch, without_cuda_build):
    monkeypatch.setattr(cuda_helper, "has_cuda_libraries", lambda: True)
    notes = []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: notes.append(body))
    cfg = SimpleNamespace(model="large-v3", device="cuda")
    engine.guard_large_v3_without_cuda(cfg)
    assert cfg.model == "medium"
    assert notes and "speech library installed here can't use" in notes[0]


class FakeLabel:
    def __init__(self):
        self.text = ""

    def set_markup(self, markup):
        self.text = markup

    def set_text(self, text):
        self.text = text


def _system_check(vulkan):
    """System check's graphics card lines on an NVIDIA computer, *vulkan*
    offered there or not."""
    check = SimpleNamespace(win=SimpleNamespace(nvidia_gpu=lambda: "NVIDIA GeForce RTX 4070",
                                                vulkan_offered=lambda: vulkan),
                            gpu_status_label=FakeLabel(), cuda_status_label=FakeLabel())
    SystemCheck.check_gpu_status(check)
    return check


def test_system_check_doesnt_send_the_user_to_pick_cuda_there(monkeypatch, without_cuda_build):
    monkeypatch.setattr(cuda_helper, "has_talktype_cuda_libraries", lambda: False)
    check = _system_check(vulkan=True)
    assert "NVIDIA GeForce RTX 4070" in check.gpu_status_label.text
    assert ("CUDA isn't available: the speech library installed here was built without it. "
            "Vulkan runs the models on your graphics card instead.") in check.cuda_status_label.text
    assert "choose CUDA" not in check.cuda_status_label.text


def test_system_check_there_promises_no_vulkan_where_it_isnt_offered(
        monkeypatch, without_cuda_build):
    monkeypatch.setattr(cuda_helper, "has_talktype_cuda_libraries", lambda: False)
    assert _system_check(vulkan=False).cuda_status_label.text == (
        "CUDA isn't available: the speech library installed here was built without it.")


# --- "What works with what?" ----------------------------------------------------

def test_what_works_with_what_doesnt_say_cuda_works_there(without_cuda_build):
    from tuxtalk import recommend
    line = recommend.this_computer(recommend.Hardware("NVIDIA GeForce RTX 4070", True))
    assert line == ("Your computer: NVIDIA GeForce RTX 4070, so the processor and Vulkan "
                    "columns work here. The speech library installed here can't use CUDA.")
    for slower in (False, True):
        assert "CUDA column" not in recommend.this_computer(recommend.Hardware(None, True),
                                                            slower)


def test_the_restore_note_on_a_computer_without_nvidia_blames_the_missing_card(
        tmp_path, monkeypatch, without_cuda_build):
    """Without a card, CUDA wouldn't work with any speech library; and the
    library isn't checked for it."""
    monkeypatch.setattr(cuda_helper, "detect_nvidia_gpu", lambda: False)
    monkeypatch.setattr(cuda_helper, "probe_ctranslate2_cuda", _no_probe)
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.toml"))
    notes = []
    assert settings_backup._fit_device('device = "cuda"\n', notes) == 'device = "cpu"\n'
    assert notes == ["The backup uses CUDA, which this computer doesn't have, so the Device "
                     "stays on CPU."]
