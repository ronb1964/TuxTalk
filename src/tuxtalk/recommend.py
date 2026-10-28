"""What TuxTalk should run on this computer.

How fast and accurate dictation is depends on the model, the device and the
language, and they only work in some combinations: Parakeet knows 25
languages and can't use CUDA; large-v3 needs a graphics card; a small
built-in graphics chip can be slower than the processor. This module turns
the facts about the computer and the user's language into one Setup. First
run, the tray presets, the GNOME menu (over D-Bus) and the update notice all
ask it, so they can't disagree. Pure logic apart from detect_hardware().
"""
import functools
import os
from dataclasses import dataclass

from .parakeet_engine import PARAKEET_LANGUAGES, effective_device, is_parakeet
from .whisper_vulkan import ENGINE_SIZE_TEXT

PARAKEET = "parakeet-v3"

LANGUAGES = [
    ("en", "English"), ("es", "Spanish"), ("fr", "French"), ("de", "German"),
    ("it", "Italian"), ("pt", "Portuguese"), ("ru", "Russian"), ("ja", "Japanese"),
    ("ko", "Korean"), ("zh", "Chinese"), ("ar", "Arabic"), ("hi", "Hindi"),
    ("nl", "Dutch"), ("sv", "Swedish"), ("no", "Norwegian"), ("da", "Danish"),
    ("fi", "Finnish"), ("pl", "Polish"), ("tr", "Turkish"), ("he", "Hebrew"),
    ("th", "Thai"), ("vi", "Vietnamese"), ("uk", "Ukrainian"), ("cs", "Czech"),
    ("hu", "Hungarian"), ("ro", "Romanian"), ("bg", "Bulgarian"), ("hr", "Croatian"),
    ("sk", "Slovak"), ("sl", "Slovenian"), ("et", "Estonian"), ("lv", "Latvian"),
    ("lt", "Lithuanian"), ("el", "Greek"), ("mt", "Maltese"),
]
_NAMES = dict(LANGUAGES)

# Each language's name in itself, so someone who doesn't read English can
# still find theirs in the pickers. Spellings checked against Qt's
# QLocale.nativeLanguageName(), capitalised for a list. No flags: a language
# isn't a country, and a flag can turn political.
_NATIVE = {
    "es": "Español", "fr": "Français", "de": "Deutsch", "it": "Italiano",
    "pt": "Português", "ru": "Русский", "ja": "日本語", "ko": "한국어",
    "zh": "中文", "ar": "العربية", "hi": "हिन्दी", "nl": "Nederlands",
    "sv": "Svenska", "no": "Norsk", "da": "Dansk", "fi": "Suomi",
    "pl": "Polski", "tr": "Türkçe", "he": "עברית", "th": "ไทย",
    "vi": "Tiếng Việt", "uk": "Українська", "cs": "Čeština", "hu": "Magyar",
    "ro": "Română", "bg": "Български", "hr": "Hrvatski", "sk": "Slovenčina",
    "sl": "Slovenščina", "et": "Eesti", "lv": "Latviešu", "lt": "Lietuvių",
    "el": "Ελληνικά", "mt": "Malti",
}

_TITLES = {PARAKEET: "Parakeet", "small": "Whisper Small", "large-v3": "Whisper Large-v3",
           "medium": "Whisper Medium", "base": "Whisper Base", "tiny": "Whisper Tiny"}

_BENEFIT = {
    (PARAKEET, "vulkan"): "Very accurate and nearly instant.",
    (PARAKEET, "cpu"): "Very accurate, and fast even without a graphics card.",
    ("small", "vulkan"): "Knows 99 languages. Quick on most computers.",
    ("small", "cpu"): "Knows 99 languages. Quick on most computers.",
    ("large-v3", "vulkan"): "Whisper's most accurate, and it knows 99 languages.",
}

_DOWNLOAD = {
    (PARAKEET, "vulkan"): "About 700 MB",
    (PARAKEET, "cpu"): "About 670 MB",
    ("small", "vulkan"): "About 290 MB",
    ("small", "cpu"): "About 490 MB",
    ("large-v3", "vulkan"): "About 1.1 GB",
}


def language_name(code):
    """English name of a language code, or the code itself if unknown."""
    return _NAMES.get(code, code)


def language_label(code):
    """How the language pickers show *code*: "Spanish (Español)", the
    English name first so the list keeps its order. English is just
    "English"."""
    native = _NATIVE.get(code)
    return f"{language_name(code)} ({native})" if native else language_name(code)


def _locale_code(value):
    """"de_DE.UTF-8" -> "de"; None for "C", "POSIX" or anything unreadable."""
    value = (value or "").strip()
    if not value or value == "POSIX" or value.split(".")[0] == "C":
        return None
    code = value.split(".")[0].split("@")[0].split("_")[0].lower()
    return code if code.isalpha() and 2 <= len(code) <= 3 else None


def system_language(env=None):
    """ISO 639-1 code of the desktop's language, from the locale variables."""
    env = os.environ if env is None else env
    # LANGUAGE names the display language ahead of the others, as in gettext,
    # which ignores it when the locale itself is C. KDE sets it when the
    # display language differs from the formats locale.
    locale = next((env.get(v) for v in ("LC_ALL", "LC_MESSAGES", "LANG") if env.get(v)), "")
    if _locale_code(locale):
        for entry in (env.get("LANGUAGE") or "").split(":"):
            code = _locale_code(entry)
            if code:
                return code
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        code = _locale_code(env.get(var))
        if code:
            return code
    return "en"


def effective_language(cfg):
    """The language to recommend for: a language picked by hand in Preferences
    (always the latest choice: first run never sets one, and it keeps an
    existing Japanese setup off Parakeet), then the first-run choice, then
    the desktop's language."""
    if getattr(cfg, "language_mode", "auto") == "manual" and getattr(cfg, "language", ""):
        return cfg.language
    return getattr(cfg, "dictation_language", "") or system_language()


@dataclass(frozen=True)
class Hardware:
    """What detect_hardware() found."""
    gpu_name: str | None      # None: no graphics chip worth using
    has_nvidia: bool = False

    @property
    def gpu(self):
        """Whether there is a graphics chip worth using."""
        return self.gpu_name is not None


@functools.lru_cache(maxsize=1)
def _nvidia_name():
    """The NVIDIA card's name from nvidia-smi, once per process."""
    try:
        from .cuda_helper import detect_nvidia_gpu
        found = detect_nvidia_gpu()
    except Exception:
        return None
    if isinstance(found, str) and found.strip():
        return found.strip().splitlines()[0]
    return None


@functools.lru_cache(maxsize=1)
def _vulkan_facts():
    """(Vulkan usable, graphics vendor ids), once per process."""
    from . import whisper_vulkan as wv
    return wv.is_offered(), frozenset(wv.graphics_vendors())


def detect_hardware(cfg=None):
    """The graphics chip TuxTalk should use, if any. Never raises.

    No chip on the Flatpak (no Vulkan engine in the sandbox), and none once
    the speed check found the processor faster (cfg.vulkan_slower), so a
    small built-in chip isn't recommended over and over."""
    if os.environ.get("FLATPAK_ID"):
        return Hardware(None, False)
    try:
        usable, vendors = _vulkan_facts()
    except Exception:
        return Hardware(None, False)
    from . import whisper_vulkan as wv
    nvidia = _nvidia_name()
    has_nvidia = bool(nvidia) or wv.NVIDIA_VENDOR in vendors
    if not usable or (cfg is not None and getattr(cfg, "vulkan_slower", False)):
        return Hardware(None, has_nvidia)
    if has_nvidia:
        name = nvidia or "NVIDIA graphics card"
    elif wv.AMD_VENDOR in vendors:
        name = "AMD graphics"
    elif wv.INTEL_VENDOR in vendors:
        name = "Intel graphics"
    else:
        name = "graphics card"
    return Hardware(name, has_nvidia)


@dataclass(frozen=True)
class Setup:
    """A recommended model and device, with the text that presents them."""
    model: str
    device: str
    title: str
    explanation: str
    download_text: str


def recommend(language, hw, model=None, use_gpu=None):
    """The setup for this computer. *model* and *use_gpu* are the user's own
    choices from Other options; one that can't work is replaced, and the
    explanation says why. *model* is Parakeet, small or large-v3 (the ones
    Other options offers); others have no entry in _BENEFIT or _DOWNLOAD."""
    language = language or "en"
    name = language_name(language)
    parakeet_ok = language in PARAKEET_LANGUAGES
    gpu = hw.gpu if use_gpu is None else bool(use_gpu and hw.gpu)
    note = ""
    if model in (None, PARAKEET) and not parakeet_ok:
        # The benefit line that follows already says Whisper knows 99 languages.
        note = f"Parakeet doesn't understand {name}, so this uses Whisper. "
        model = "large-v3" if gpu else "small"
    elif model is None:
        model = PARAKEET
    elif model == "large-v3" and not gpu:
        note = ("Large-v3 needs a graphics card, so this uses Whisper Small "
                "on the processor. ")
        model = "small"
    device = "vulkan" if gpu else "cpu"
    return Setup(
        model=model,
        device=device,
        title=setup_title(model, device, hw),
        explanation=note + _BENEFIT[(model, device)],
        download_text=_DOWNLOAD[(model, device)] + ", downloaded once.",
    )


def model_title(model):
    """"Parakeet", "Whisper Small": a model's name as the menus show it."""
    return _TITLES.get(model, f"Whisper {model.capitalize()}")


def engine_status(cfg_model, cfg_device, running=None):
    """(model id, device text) for what the menus say runs: "GPU (Vulkan)", "CPU".

    *running* is (model, device) as reported by the dictation service, or None
    when it hasn't reported (stopped, starting, or an older service). The lines
    show what really runs, so a fallback to the processor is not reported as
    the graphics card; when that differs from the setting, the device text
    says why in a few words.
    """
    names = {'cpu': 'CPU', 'cuda': 'GPU (CUDA)', 'vulkan': 'GPU (Vulkan)'}
    wanted = str(cfg_device or "cpu").lower()
    if running:
        model, device = running[0], str(running[1]).lower()
    else:
        model, device = cfg_model, effective_device(cfg_model, wanted)
    text = names.get(device, device.upper())
    if device == "cpu" and wanted in ("cuda", "vulkan"):
        if wanted == "cuda" and is_parakeet(model):
            text += " (Parakeet can't use CUDA)"
        elif running:
            text += f" ({'CUDA' if wanted == 'cuda' else 'Vulkan'} didn't start)"
    return model, text


def where_it_runs(device_text):
    """engine_status's device text in words, keeping any reason: "the
    processor", "the graphics card (Vulkan)", "the processor (Vulkan didn't start)"."""
    if device_text.startswith("CPU"):
        return "the processor" + device_text[len("CPU"):]
    return "the graphics card" + device_text[len("GPU"):]


def engine_line(cfg_model, cfg_device, running=None):
    """The menus' one line about what runs: "Parakeet on the graphics card (Vulkan)".
    The tray, the no-tray menu window and the GNOME extension (GetStatus) all show it."""
    model, device_text = engine_status(cfg_model, cfg_device, running)
    return f"{model_title(model)} on {where_it_runs(device_text)}"


def setup_title(model, device, hw):
    """"Parakeet on your NVIDIA GeForce RTX 4070", "Whisper Small on this
    computer's processor": a model and device in words. Parakeet on CUDA runs
    on the processor (parakeet_engine), so it says so."""
    name = model_title(model)
    if device == "cpu" or (device == "cuda" and model == PARAKEET):
        return f"{name} on this computer's processor"
    card = hw.gpu_name or "graphics card"
    if device == "cuda":
        return f"{name} on your {card} (CUDA)"
    return f"{name} on your {card}"


@dataclass(frozen=True)
class OptionState:
    model: str
    label: str
    size: str
    description: str
    available: bool
    reason: str


def option_states(language, hw, use_gpu=None):
    """The models offered under Other options, each with why it can't be
    picked when it can't. Tiny, Base and Medium live in Preferences.
    Sizes are what each one downloads with the graphics card on or off
    (*use_gpu*, default: on when there is a usable one)."""
    name = language_name(language or "en")
    parakeet_ok = (language or "en") in PARAKEET_LANGUAGES
    gpu = hw.gpu if use_gpu is None else bool(use_gpu and hw.gpu)
    return [
        OptionState(PARAKEET, "Parakeet", "about 670 MB",
                    "The most accurate for English and 24 European languages. "
                    "Fast even without a graphics card.",
                    parakeet_ok, "" if parakeet_ok else f"Doesn't understand {name}."),
        OptionState("small", "Whisper Small", "about 290 MB" if gpu else "about 490 MB",
                    "Knows 99 languages. A good choice on an older or slower computer.",
                    True, ""),
        OptionState("large-v3", "Whisper Large-v3", "about 1.1 GB",
                    "Whisper's most accurate, for languages Parakeet doesn't know. "
                    "Needs a graphics card.",
                    gpu, "" if gpu else ("Needs the graphics card ticked." if hw.gpu
                                         else "Needs a graphics card.")),
    ]


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    description: str
    model: str
    device: str
    extras: tuple = ()      # ((config key, value), ...)


def presets(language, hw):
    """The tray's Performance menu, in order."""
    rec = recommend(language, hw)
    return [
        Preset("recommended", "Recommended for this computer", rec.title, rec.model, rec.device),
        Preset("lightest", "Lightest",
               "Whisper Base on the processor: a small download and little work",
               "base", "cpu"),
        Preset("battery", "Battery saver",
               "Whisper Tiny on the processor, rests after 2 idle minutes",
               "tiny", "cpu",
               (("auto_timeout_enabled", True), ("auto_timeout_minutes", 2))),
    ]


def match_preset(cfg, preset_list):
    """Id of the preset the settings match, or "custom". Presets with extra
    settings are checked first, since they share a model and device with
    nothing else but must also match their extras."""
    def same(p):
        return (getattr(cfg, "model", None) == p.model
                and getattr(cfg, "device", None) == p.device
                and all(getattr(cfg, k, None) == v for k, v in p.extras))
    for p in sorted(preset_list, key=lambda p: not p.extras):
        if same(p):
            return p.id
    return "custom"


def differs_from_recommendation(cfg, hw):
    """The recommended Setup when the settings aren't it, else None."""
    rec = recommend(effective_language(cfg), hw)
    if (getattr(cfg, "model", None), getattr(cfg, "device", None)) == (rec.model, rec.device):
        return None
    return rec


# What runs where, for Preferences' "What works with what?" window. Rows of
# (models, processor, Vulkan, CUDA); each cell is (works, note).
# tests/test_compatibility_help.py checks the Vulkan column against
# whisper_vulkan.MODEL_FILES, so the help can't drift from what really runs.
COMPATIBILITY = (
    ("Parakeet", (PARAKEET,),
     (True, "fast"), (True, "fastest"), (False, "runs on the processor")),
    ("Tiny, Base", ("tiny", "base"),
     (True, ""), (True, ""), (True, "")),
    ("Small, Medium", ("small", "medium"),
     (True, "slower"), (True, ""), (True, "")),
    ("Large-v3", ("large-v3",),
     (False, "too slow"), (True, ""), (True, "")),
)

COMPATIBILITY_NOTES = (
    "Languages: Parakeet knows English and 24 European languages. "
    "Whisper models know about 99.",
    f"Vulkan is a {ENGINE_SIZE_TEXT} engine, and each model needs its own copy for it the "
    "first time. CUDA is a one-time 1.4 GB download.",
)


def this_computer(hw, slower=False):
    """The line under the table saying which columns apply here. *slower*:
    the speed check found the processor faster than the graphics chip, which
    detect_hardware reports as no chip at all."""
    name = hw.gpu_name if hw.gpu_name not in (None, "graphics card") else "a graphics card"
    if hw.gpu and hw.has_nvidia:
        return f"Your computer: {name}, so all three columns work here."
    if hw.gpu:
        return (f"Your computer: {name}, so the processor and Vulkan columns work here. "
                f"CUDA needs an NVIDIA card.")
    if slower and hw.has_nvidia:
        return ("Your computer: an NVIDIA card, but your processor was faster through "
                "Vulkan in TuxTalk's speed check, so the processor and CUDA columns "
                "apply here.")
    if slower:
        return ("Your computer: its graphics chip was slower than the processor in "
                "TuxTalk's speed check, so the processor column applies here.")
    if hw.has_nvidia:
        return ("Your computer: an NVIDIA card, but Vulkan isn't working on it, so the "
                "processor and CUDA columns apply here.")
    return ("Your computer: no graphics chip TuxTalk can use, so the processor "
            "column applies here.")
