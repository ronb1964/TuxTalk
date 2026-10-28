"""recommend.py: the best setup for this computer, in one place.

First run, the tray presets, the GNOME menu and the update notice all ask
recommend(), so they can't disagree. Pure logic: hardware and language in,
setup out.
"""
import types

import pytest

from tuxtalk import recommend as r

NVIDIA = r.Hardware("NVIDIA GeForce RTX 4070 SUPER", has_nvidia=True)
AMD = r.Hardware("AMD graphics")
NONE = r.Hardware(None)


@pytest.mark.parametrize("lang,hw,model,device", [
    ("en", NVIDIA, "parakeet-v3", "vulkan"),
    ("uk", AMD, "parakeet-v3", "vulkan"),
    ("en", NONE, "parakeet-v3", "cpu"),
    ("ja", NVIDIA, "large-v3", "vulkan"),
    ("ja", NONE, "small", "cpu"),
])
def test_recommendation_per_computer(lang, hw, model, device):
    s = r.recommend(lang, hw)
    assert (s.model, s.device) == (model, device)


def test_titles_name_the_graphics_card_or_processor():
    assert r.recommend("en", NVIDIA).title == "Parakeet on your NVIDIA GeForce RTX 4070 SUPER"
    assert r.recommend("en", NONE).title == "Parakeet on this computer's processor"
    assert r.recommend("ja", NVIDIA).title == "Whisper Large-v3 on your NVIDIA GeForce RTX 4070 SUPER"


def test_a_language_parakeet_lacks_says_why():
    s = r.recommend("ja", NVIDIA)
    assert "Parakeet doesn't understand Japanese" in s.explanation


def test_overrides_are_honoured_or_explained():
    assert r.recommend("en", NVIDIA, model="small").model == "small"
    assert r.recommend("en", NVIDIA, use_gpu=False).device == "cpu"
    s = r.recommend("ja", NVIDIA, model="parakeet-v3")        # can't work
    assert s.model == "large-v3" and "doesn't understand" in s.explanation
    s = r.recommend("en", NONE, model="large-v3")              # needs a GPU
    assert s.model == "small" and "needs a graphics card" in s.explanation.lower()


def test_download_text_matches_the_setup():
    assert r.recommend("en", NVIDIA).download_text == "About 700 MB, downloaded once."
    assert r.recommend("en", NONE).download_text == "About 670 MB, downloaded once."
    assert r.recommend("ja", NVIDIA).download_text == "About 1.1 GB, downloaded once."


def test_option_states_grey_out_what_cant_work():
    states = {o.model: o for o in r.option_states("ja", NONE)}
    assert not states["parakeet-v3"].available
    assert states["parakeet-v3"].reason == "Doesn't understand Japanese."
    assert not states["large-v3"].available
    assert states["large-v3"].reason == "Needs a graphics card."
    assert states["small"].available
    assert all(o.available for o in r.option_states("en", NVIDIA))


def test_large_v3_is_greyed_out_while_the_graphics_card_is_unticked():
    """Offered with the box unticked, picking it was replaced at once by
    Whisper Small; it says what it needs instead."""
    states = {o.model: o for o in r.option_states("ja", NVIDIA, use_gpu=False)}
    assert not states["large-v3"].available
    assert states["large-v3"].reason == "Needs the graphics card ticked."
    assert {o.model: o for o in r.option_states("ja", NVIDIA)}["large-v3"].available


@pytest.mark.parametrize("env,code", [
    ({"LANG": "en_US.UTF-8"}, "en"),
    ({"LANG": "uk_UA.UTF-8"}, "uk"),
    ({"LC_ALL": "ja_JP.UTF-8", "LANG": "en_US.UTF-8"}, "ja"),
    ({"LANG": "C.UTF-8"}, "en"),
    ({"LANG": "sr_RS@latin"}, "sr"),
    ({}, "en"),
    # LANGUAGE (the display language) comes first, as in gettext...
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "de"}, "de"),
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "pt_BR:pt:en"}, "pt"),
    ({"LANG": "en_US.UTF-8", "LANGUAGE": ""}, "en"),
    # ...except when the locale is C, where gettext ignores it.
    ({"LC_ALL": "C", "LANGUAGE": "de"}, "en"),
])
def test_system_language(env, code):
    assert r.system_language(env) == code


def test_effective_language_order(monkeypatch):
    monkeypatch.setattr(r, "system_language", lambda env=None: "en")
    cfg = types.SimpleNamespace(dictation_language="", language_mode="manual", language="ja")
    assert r.effective_language(cfg) == "ja"           # an old manual choice counts
    cfg.dictation_language = "en"
    assert r.effective_language(cfg) == "ja"           # set in Preferences after first run
    cfg.language_mode = "auto"
    assert r.effective_language(cfg) == "en"           # otherwise the first-run choice
    cfg2 = types.SimpleNamespace(dictation_language="", language_mode="auto", language="")
    assert r.effective_language(cfg2) == "en"          # the system locale


def test_presets_have_stable_ids_and_never_coincide():
    for lang in ("en", "ja"):
        for hw in (NVIDIA, AMD, NONE):
            ps = r.presets(lang, hw)
            assert [p.id for p in ps] == ["recommended", "lightest", "battery"]
            pairs = [(p.model, p.device, p.extras) for p in ps]
            assert len(set(pairs)) == 3, (lang, hw, pairs)
    battery = r.presets("en", NONE)[2]
    assert (battery.model, battery.device) == ("tiny", "cpu")
    assert dict(battery.extras) == {"auto_timeout_enabled": True, "auto_timeout_minutes": 2}
    assert r.presets("en", NONE)[1].model == "base"


def _cfg(model, device, timeout_on=True, minutes=5):
    return types.SimpleNamespace(model=model, device=device, auto_timeout_enabled=timeout_on,
                                 auto_timeout_minutes=minutes, dictation_language="",
                                 language_mode="auto", language="")


def test_match_preset():
    ps = r.presets("en", NVIDIA)
    assert r.match_preset(_cfg("parakeet-v3", "vulkan"), ps) == "recommended"
    assert r.match_preset(_cfg("base", "cpu"), ps) == "lightest"
    assert r.match_preset(_cfg("tiny", "cpu", True, 2), ps) == "battery"
    assert r.match_preset(_cfg("tiny", "cpu", True, 5), ps) == "custom"
    assert r.match_preset(_cfg("parakeet-v3", "cpu"), ps) == "custom"   # GPU idle: not Recommended


def test_differs_from_recommendation(monkeypatch):
    monkeypatch.setattr(r, "system_language", lambda env=None: "en")
    assert r.differs_from_recommendation(_cfg("parakeet-v3", "vulkan"), NVIDIA) is None
    s = r.differs_from_recommendation(_cfg("small", "cpu"), NVIDIA)
    assert s is not None and s.model == "parakeet-v3"


def test_a_slower_graphics_chip_means_the_processor(monkeypatch):
    """Once the speed check says the processor wins, recommend it."""
    monkeypatch.setattr(r, "_vulkan_facts", lambda: (True, frozenset({"0x8086"})))
    monkeypatch.setattr(r, "_nvidia_name", lambda: None)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    fast = r.detect_hardware(types.SimpleNamespace(vulkan_slower=False))
    slow = r.detect_hardware(types.SimpleNamespace(vulkan_slower=True))
    assert fast.gpu and fast.gpu_name == "Intel graphics"
    assert not slow.gpu


def test_flatpak_has_no_usable_graphics(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    assert not r.detect_hardware().gpu


def test_detect_hardware_never_raises(monkeypatch):
    def boom():
        raise OSError("no sysfs")
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(r, "_vulkan_facts", boom)
    monkeypatch.setattr(r, "_nvidia_name", lambda: None)
    assert r.detect_hardware() == r.Hardware(None, False)


def test_hardware_facts_are_cached():
    """The tray asks every second; nvidia-smi must run once."""
    assert hasattr(r._nvidia_name, "cache_info") and hasattr(r._vulkan_facts, "cache_info")


def test_new_settings_exist_and_are_live():
    from tuxtalk.config import LIVE_APPLIED_KEYS, Settings
    s = Settings()
    assert (s.dictation_language, s.recommend_notice_shown, s.vulkan_slower) == ("", False, False)
    assert {"dictation_language", "recommend_notice_shown", "vulkan_slower"} <= LIVE_APPLIED_KEYS


@pytest.mark.parametrize("hw", [r.Hardware("NVIDIA GeForce RTX 4070 SUPER", True), r.Hardware(None)])
def test_explanation_never_repeats_itself(hw):
    """Japanese on a graphics card must not read "...uses Whisper, which knows
    99 languages. Whisper's most accurate, and it knows 99 languages." """
    for model in (None, "parakeet-v3", "small", "large-v3"):
        s = r.recommend("ja", hw, model=model)
        assert s.explanation.count("99 languages") <= 1, s.explanation


def test_the_help_notes_give_the_real_graphics_engine_size():
    """Help, Preferences and these notes said 24 MB for a 22 MB engine."""
    from tuxtalk import recommend, whisper_vulkan
    assert any(whisper_vulkan.ENGINE_SIZE_TEXT in n for n in recommend.COMPATIBILITY_NOTES)
