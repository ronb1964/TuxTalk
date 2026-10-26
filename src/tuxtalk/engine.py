"""The dictation service's speech model: loading it (build), falling back
to another one when it can't be, resting it for Power Saving and waking it
again, and turning a recording into text (transcribe) on the processor,
CUDA or the graphics card through Vulkan.

Every engine takes 16 kHz mono (SAMPLE_RATE); recordings are resampled to
it."""
import sys
import threading
import time

from . import hallucinations, silence, tray_link
from . import live_settings as live
from . import notify as notify_module
from .config import Settings, load_config
from .logger import setup_logger

logger = setup_logger(__name__)

# --- Runtime state ---
SAMPLE_RATE = 16000

# The loaded speech model, built by main(). None until then, and while Power
# Saving rests it (rest) until the next dictation loads it (wake).
model = None
# The settings main() built it from, after guard_large_v3_without_cuda and
# build_or_exit's fallback changed them: what wake loads again, rather than
# the model the settings file names.
built_from = None
# The last wake failed: only the next recording's start tries again.
_wake_failed = False

_model_lock = threading.Lock()


def _strip_hallucinations(text: str, no_speech_prob: float = 0.0) -> str:
    """hallucinations.strip, logged the way transcripts are (live.loggable)."""
    stripped = hallucinations.strip(text, no_speech_prob)
    if stripped != text.strip():
        logger.info(f"Stripped a hallucination (no_speech_prob={no_speech_prob:.2f}): "
                    f"{live.loggable(text)} -> {live.loggable(stripped)}")
    return stripped


def transcribe(audio_f32, language: str | None) -> str | None:
    """Transcribe audio with the loaded engine (Whisper or Parakeet) and
    filter hallucinations: the raw text, or None if no speech was detected."""
    started = time.time()
    engine = wake()
    if engine is None:
        return None
    from . import custom_words, language_limit
    from .parakeet_engine import ParakeetModel
    from .parakeet_gpu import ParakeetGpuModel
    from .whisper_vulkan import VulkanWhisperModel
    words_prompt = custom_words.prompt(live.words)
    # Auto-detect held to the user's languages: one language is simply used,
    # two or more are checked against Whisper's own pick (language_limit.py).
    # Parakeet has no language setting, so this changes nothing there.
    if not language:
        language = language_limit.forced_language(live.auto_languages)
    limit_to = () if language else live.auto_languages
    if isinstance(engine, VulkanWhisperModel):
        return _transcribe_vulkan(engine, audio_f32, language, limit_to, words_prompt, started)
    if isinstance(engine, (ParakeetModel, ParakeetGpuModel)):
        return _transcribe_parakeet(engine, audio_f32, started)
    return _transcribe_whisper(engine, audio_f32, language, limit_to, words_prompt, started)


def _transcribe_vulkan(engine, audio_f32, language, limit_to, words_prompt, started):
    """transcribe() on whisper.cpp's Vulkan engine."""
    from . import custom_words
    # Only pass a prompt or a language list when there is one, so nothing
    # changes without them.
    extra = {}
    if words_prompt:
        extra["prompt"] = words_prompt
    if limit_to:
        extra["limit_to"] = limit_to
    raw, _no_speech = engine.transcribe(audio_f32, language, **extra)
    logger.info(f"TIMING: Transcription completed in {time.time() - started:.2f}s (Vulkan GPU)")
    logger.info(f"Raw transcription (Vulkan GPU): {live.loggable(raw)}")
    # whisper.cpp's no-speech score isn't reliable (0.90 on clear speech,
    # 0.00 on silence, see whisper_vulkan.parse_result), so the recording's
    # own loudness stands in for it. With nobody talking, a lone "Thanks
    # for watching!" is invented; said aloud, it is kept.
    if custom_words.is_echo(raw, live.words):
        logger.info("Whisper read your words back with nothing said; dropped")
        return None
    no_speech = 1.0 if silence.has_no_speech(audio_f32, SAMPLE_RATE) else 0.0
    return _strip_hallucinations(raw, no_speech_prob=no_speech) or None


def _transcribe_parakeet(engine, audio_f32, started):
    """transcribe() on Parakeet, on the processor or the graphics chip.

    Parakeet detects the language itself and has no Whisper-style decoding
    options. It has no no-speech score either, yet it does invent a word in
    silence: an F9 tap with nothing said typed "Mm-hmm." So, as for the
    Vulkan engine, a recording with nobody talking counts as no speech, and
    a filler alone is dropped. Your Words steer its decoder (parakeet_bias.py).
    """
    from .parakeet_gpu import ParakeetGpuModel, _steer
    _steer(engine, live.words)
    raw = engine.recognize(audio_f32)
    where = " on GPU" if isinstance(engine, ParakeetGpuModel) else ""
    logger.info(f"TIMING: Transcription completed in {time.time() - started:.2f}s "
                f"(Parakeet{where})")
    logger.info(f"Raw transcription (Parakeet): {live.loggable(raw)}")
    no_speech = 1.0 if raw and silence.has_no_speech(audio_f32, SAMPLE_RATE) else 0.0
    return _strip_hallucinations(raw, no_speech_prob=no_speech) or None


# faster-whisper's decoding options for dictation.
WHISPER_OPTIONS = dict(
    beam_size=5,
    condition_on_previous_text=True,
    temperature=0.0,
    # Word-level timestamps strengthen Whisper's long-form seek logic,
    # preventing dropped words at pause boundaries.
    word_timestamps=True,
    # Repetition-loop guards: Whisper can get stuck repeating one phrase
    # many times in a row. A mild penalty plus n-gram blocking breaks the
    # loop without changing normal decoding.
    repetition_penalty=1.1,
    no_repeat_ngram_size=5,
    # vad_filter is off: Silero VAD trims speech onsets at segment
    # boundaries (especially the start of recordings and after sentence
    # pauses), causing leading words to vanish from raw Whisper output.
    # See: github.com/SYSTRAN/faster-whisper/issues/925. Push-to-talk
    # dictation has little non-speech audio to filter, so it costs next to
    # nothing. hallucination_silence_threshold stays unset for the same
    # reason: it drops real speech after natural 2+ second pauses. Trailing
    # hallucination phrases are handled by _strip_hallucinations().
    vad_filter=False,
)


def _transcribe_whisper(engine, audio_f32, language, limit_to, words_prompt, started):
    """transcribe() on faster-whisper (processor or CUDA). Your Words go in
    as hotwords, which faster-whisper puts in every 30-second window's prompt."""
    from . import custom_words, language_limit
    options = dict(WHISPER_OPTIONS, hotwords=words_prompt)
    segments, info = engine.transcribe(audio_f32, language=(language or None), **options)
    # faster-whisper detects the language before it decodes a word (segments
    # is a generator), so starting again in another language costs only that
    # detection, and only when its pick is off the user's list.
    better = (language_limit.redirect(info.language, dict(info.all_language_probs or ()),
                                      limit_to) if limit_to else None)
    if better:
        logger.info(f"Language: Whisper heard {info.language}, which isn't on your list; "
                    f"using {better}")
        segments, info = engine.transcribe(audio_f32, language=better, **options)
    transcribe_time = time.time() - started
    logger.info(f"TIMING: Transcription completed in {transcribe_time:.2f}s")
    if transcribe_time > 2.0:
        logger.warning(f"Transcription took {transcribe_time:.2f}s "
                       "(first run may be slower due to CUDA compilation)")
    seg_list = list(segments)       # a generator, read once
    raw = " ".join(seg.text for seg in seg_list).strip()
    max_no_speech_prob = max((seg.no_speech_prob for seg in seg_list), default=0.0)
    logger.info(f"Raw transcription (before hallucination filter): {live.loggable(raw)}  "
                f"[no_speech_prob={max_no_speech_prob:.2f}]")
    if custom_words.is_echo(raw, live.words):
        logger.info("Whisper read your words back with nothing said; dropped")
        return None
    # Strip common Whisper hallucinations like "thank you" from the end
    raw = _strip_hallucinations(raw, no_speech_prob=max_no_speech_prob)
    if raw:
        logger.info(f"Raw transcription (after hallucination filter): {live.loggable(raw)}")
    return raw or None


def rest(minutes):
    """Power Saving: let go of the speech model, and the processor and
    graphics card time it holds, after *minutes* without dictation. The
    hotkeys keep working; the next dictation loads it again (wake).
    It used to end the service, and F8 then did nothing until dictation was
    turned back on from the menu, which looked like TuxTalk had broken."""
    global model
    with _model_lock:
        resting, model = model, None
    # The Vulkan engine and Parakeet on the graphics card run a helper process.
    close = getattr(resting, "close", None)
    if close is not None:
        close()
    del resting
    import gc
    gc.collect()
    logger.info(f"Power saving: no dictation for {minutes} minute{'s' if minutes != 1 else ''}; "
                "let go of the speech model until the next dictation")


def wake(fresh: bool = False):
    """The speech model, loaded again first if Power Saving let it go, or
    None if it won't load. Called with *fresh* when a recording starts,
    and without before its transcription, which doesn't try again after
    the start's try failed: one dictation, one notice."""
    global model, _wake_failed
    with _model_lock:
        if model is None:
            if _wake_failed and not fresh:
                return None
            logger.info("Power saving: loading the speech model again for this dictation")
            try:
                # Not build_or_exit: its fallback saves another model
                # over the user's choice, which a graphics card that fails
                # once on waking shouldn't do, and its exit would end only
                # this dictation's thread. The next dictation tries again.
                # The model that was running, after the startup checks.
                model = build(built_from or load_config())
            except Exception as e:
                # On a thread of its own when a recording starts: anything
                # not caught here would be lost to the log.
                logger.error(f"Power saving: the speech model wouldn't load again: {e}",
                             exc_info=not isinstance(e, ModelUnavailable))
                _wake_failed = True
                notify_module.notify("The speech model wouldn't load",
                "Choose Restart Service in the TuxTalk menu, or pick a model in Preferences.")
                return None
            _wake_failed = False
            notify_tray()
        return model


class ModelUnavailable(Exception):
    """The speech model is not downloaded and the user cancelled the download,
    or it failed. Nothing to retry: the service cannot run without a model."""


class ModelWontLoad(ModelUnavailable):
    """The speech model is downloaded but won't load. Unlike a missing model,
    choosing it again in Preferences downloads nothing, so the fallback is for
    this run only and the user's choice stays."""


# Speech models from best quality to fastest. When the chosen model is not
# downloaded, the service falls back to the downloaded model nearest to it here.
_QUALITY_ORDER = ("large-v3", "parakeet-v3", "medium", "small", "base", "tiny")


def guard_large_v3_without_cuda(cfg):
    """large-v3 without any CUDA libraries runs as medium.

    Without CUDA, large-v3 would run on the processor, far too slowly to use.
    This keeps the service usable for someone who set up large-v3 with CUDA
    and later lost the libraries (a fresh profile, a cleaned data folder). It
    uses the same check as Preferences, which accepts a system-wide CUDA
    install, so a model Preferences offers is never swapped here. It applies
    to every device but Vulkan: large-v3 runs there without any CUDA library,
    and Vulkan has its own fallback, which tells the user.
    """
    if cfg.model != "large-v3" or str(cfg.device).lower() == "vulkan":
        return
    try:
        from .cuda_helper import has_cuda_libraries
        if not has_cuda_libraries():
            logger.warning(
                "large-v3 selected but CUDA libraries not found — "
                "falling back to 'medium' to prevent non-functional state."
            )
            cfg.model = "medium"
            notify_module.notify("Using Medium instead of Large-v3",
                                 "Large-v3 needs the CUDA files, which aren't installed. Pick "
                                 "CUDA under Preferences, General, Device to download them and "
                                 "use Large-v3 again.")
    except Exception as e:
        # The model's own load reports a real problem with CUDA.
        logger.info(f"Could not check large-v3's CUDA files: {e}")


def _pick_fallback_model(wanted, downloaded, has_cuda):
    """The downloaded model closest in quality to *wanted*, or None.

    Ties go to the faster model. large-v3 is skipped without CUDA, where it
    is too slow to use (main() refuses it there for the same reason).
    """
    rank = {m: i for i, m in enumerate(_QUALITY_ORDER)}
    target = rank.get(wanted, rank["small"])
    options = [m for m in downloaded
               if m in rank and m != wanted and (m != "large-v3" or has_cuda)]
    if not options:
        return None
    return min(options, key=lambda m: (abs(rank[m] - target), -rank[m]))


def build_or_exit(settings: Settings):
    """build, falling back to an already-downloaded model when the chosen
    one is not downloaded (the user cancelled, or the download failed).

    Cancelling usually means "not now", not "turn dictation off", so dictation
    carries on with the nearest model already on disk. The switch is saved, so
    every menu shows the model actually running, and the user is told. Only
    with no model downloaded at all does the service stop.

    A model that is downloaded but won't load (ModelWontLoad) gets the same
    fallback for this run only: saving it would replace the user's choice at
    every start, since choosing the model again downloads nothing.
    """
    try:
        return build(settings)
    except ModelUnavailable as e:
        from .model_helper import OFFERED_MODELS, is_model_cached_fast
        from .recommend import model_title
        try:
            from .cuda_helper import has_cuda_libraries
            has_cuda = has_cuda_libraries()
        except Exception as cuda_e:
            logger.info(f"Could not check for CUDA's files: {cuda_e}")
            has_cuda = False
        wanted = settings.model
        downloaded = [m for m in OFFERED_MODELS if is_model_cached_fast(m)]
        fallback = _pick_fallback_model(wanted, downloaded, has_cuda)
        if fallback is None:
            logger.warning(f"Dictation service stopping: {e}, and no other model is downloaded")
            print(f"{e}. Choose a model in Preferences to start dictating.")
            sys.exit(1)

        logger.warning(f"{e}; falling back to the downloaded {fallback} model")
        settings.model = fallback      # the D-Bus menus read this same object
        try:
            model = build(settings)
        except ModelUnavailable:
            logger.warning(f"Dictation service stopping: fallback {fallback} could not load either")
            sys.exit(1)
        if isinstance(e, ModelWontLoad):
            notify_module.notify(f"Using {model_title(fallback)} for now",
                                 f"{model_title(wanted)} is downloaded but wouldn't load. "
                                 "TuxTalk tries it again the next time dictation starts.")
            return model
        try:
            from .config import load_config, save_config
            saved = load_config()
            saved.model = fallback
            save_config(saved)
        except Exception as save_error:
            logger.warning(f"Could not save the fallback model to settings: {save_error}")
        notify_module.notify(f"Using {model_title(fallback)} for now",
                             f"{model_title(wanted)} wasn't downloaded. You can choose it again in "
                             "Preferences.")
        return model


def _build_vulkan_model(settings: Settings):
    """The Vulkan graphics engine for settings.model, or None to use the
    processor instead (with the reason logged and the user told). Never stops
    dictation: anything missing or broken falls back to the CPU."""
    from . import whisper_vulkan
    reason = None
    if not whisper_vulkan.is_installed(settings.model):
        reason = "its download isn't there"
    else:
        try:
            device_index = whisper_vulkan.find_device()
            if device_index is None:
                reason = "no graphics chip was found"
            else:
                model = whisper_vulkan.VulkanWhisperModel(settings.model, device_index)
                logger.info(f"Model loaded: {settings.model} on vulkan device {device_index}")
                _record_engine(settings.model, "vulkan")
                return model
        except Exception as e:
            reason = f"the graphics engine failed to start ({e})"
    logger.warning(f"Vulkan GPU not used for {settings.model}: {reason}; using the processor")
    notify_module.notify("Couldn't use the graphics card",
                "TuxTalk is using the processor for now. Choose the device again in "
                "Preferences to set it up.")
    return None


def _build_parakeet_gpu_model(settings: Settings):
    """Parakeet on the graphics chip through Vulkan (parakeet_gpu.py), or None
    to use the processor.

    Without its graphics files that's quiet: a setup with Vulkan as the device
    and Parakeet as the model can predate those files, and shouldn't get a
    warning at every start. Choosing Vulkan again in Preferences sets it up.
    A real failure is reported."""
    from . import parakeet_gpu, whisper_vulkan
    from .parakeet_engine import PARAKEET_MODEL
    if not whisper_vulkan.is_installed(PARAKEET_MODEL):
        logger.info("Parakeet's graphics files aren't downloaded; Parakeet runs on the processor")
        return None
    reason = None
    try:
        device_index = whisper_vulkan.find_device()
        if device_index is None:
            reason = "no graphics chip was found"
        else:
            model = parakeet_gpu.ParakeetGpuModel(device_index)
            logger.info(f"Model loaded: {PARAKEET_MODEL} on vulkan device {device_index}")
            _record_engine(PARAKEET_MODEL, "vulkan")
            return model
    except Exception as e:
        reason = f"the graphics helper failed to start ({e})"
    logger.warning(f"Vulkan GPU not used for Parakeet: {reason}; using the processor")
    notify_module.notify("Couldn't run Parakeet on the graphics card",
                "TuxTalk is using the processor for now. Choose the device again in "
                "Preferences to set it up.")
    return None


# What the service really loaded, as (model, device) with device "cpu", "cuda" or
# "vulkan". It can differ from the settings: Vulkan or CUDA falls back to the
# processor, Parakeet can't use CUDA, a missing model is swapped for one that's
# downloaded. The tray and the GNOME menu show this rather than the settings,
# which could say "GPU (Vulkan)" while the processor does the work.
_running_engine = None

# True once Gtk.main() runs on its own thread. From then on the service's main
# thread must not open the model download dialogs: two threads driving GTK at
# once crash the service.
gtk_loop_running = False


def _record_engine(model, device):
    """Remember what was really loaded (see _running_engine)."""
    global _running_engine
    _running_engine = (str(model), str(device).lower())


def notify_tray():
    """Tell the tray what's really running (see _running_engine)."""
    if _running_engine is None:
        return
    tray_link.call("NotifyRunningEngine", *_running_engine,
                   what="tell the tray which engine is running")


def build(settings: Settings):
    """Load the speech model on the configured device, falling back to the
    processor when the graphics card can't be used."""
    from .model_helper import BrokenModelFiles, download_model_with_progress
    from .parakeet_engine import is_parakeet

    if settings.device.lower() == "vulkan":
        build_on_gpu = (_build_parakeet_gpu_model if is_parakeet(settings.model)
                        else _build_vulkan_model)
        vulkan_model = build_on_gpu(settings)
        if vulkan_model is not None:
            return vulkan_model
        # Fall through to the processor for this run only. settings.device is
        # left alone: a missing download or a driver hiccup is fixed from
        # Preferences, and saving "cpu" would quietly undo the user's choice.
        settings = Settings(**{**vars(settings), "device": "cpu"})
    settings = _without_missing_cuda(settings)

    compute_type = "float16" if settings.device.lower() == "cuda" else "int8"
    try:
        model = download_model_with_progress(
            settings.model,
            device=settings.device,
            compute_type=compute_type,
            dialogs=not gtk_loop_running,
        )
        if model is None:
            raise ModelUnavailable(
                f"The {settings.model} model was not downloaded (cancelled or failed)")
        # Parakeet gets here only on the processor (the graphics route returned above).
        where = "cpu" if is_parakeet(settings.model) else settings.device
        logger.info(f"Model loaded: {settings.model} on {where}")
        _record_engine(settings.model, where)
        return model
    except BrokenModelFiles as e:
        raise ModelWontLoad(str(e)) from e
    except ModelUnavailable:
        # Never "fall back to CPU" here. That fallback exists for a CUDA load
        # failure; treating a cancel as one would re-run the download without
        # asking and ignore the user's Cancel.
        raise
    except Exception as e:
        if settings.device.lower() == "cuda" and not is_parakeet(settings.model):
            return _cpu_fallback_after_cuda(settings, e)
        logger.error(f"Model loading failed: {e}")
        raise


def _without_missing_cuda(settings):
    """*settings*, on the processor for this run when they ask for CUDA but
    its files are missing. The model would load "on cuda" without them, then
    the first dictation crashes inside the speech library (libcudnn missing),
    past every fallback. The setting stays: downloading CUDA again in
    Preferences fixes it."""
    from . import cuda_helper
    from .parakeet_engine import is_parakeet
    if (settings.device.lower() != "cuda" or is_parakeet(settings.model)
            or cuda_helper.has_cuda_libraries()):
        return settings
    logger.warning("Device is CUDA but the CUDA libraries are missing; using the processor")
    notify_module.notify("The CUDA files are missing",
                         "TuxTalk is using the processor for now. Pick CUDA under Preferences, "
                         "General, Device to download them again.")
    return Settings(**{**vars(settings), "device": "cpu"})


def _cpu_fallback_after_cuda(settings, error):
    """The model on the processor after loading it on CUDA failed with
    *error*; the saved device becomes the processor, and the user is told."""
    from .model_helper import BrokenModelFiles, download_model_with_progress
    logger.error(f"CUDA error: {type(error).__name__}: {error}; falling back to the processor")
    logger.debug("CUDA traceback:", exc_info=error)
    try:
        # No confirmation: the user already chose this model, and a surprise
        # dialog in a background process would block silently and hang the
        # service.
        model = download_model_with_progress(
            settings.model,
            device="cpu",
            compute_type="int8",
            show_confirmation=False,
            dialogs=not gtk_loop_running,
        )
    except BrokenModelFiles as cpu_e:
        raise ModelWontLoad(str(cpu_e)) from cpu_e
    except Exception as cpu_e:
        logger.error(f"CPU fallback failed: {cpu_e}")
        raise
    if model is None:
        logger.error("CPU fallback failed: the model was not downloaded")
        raise ModelUnavailable(
            f"The {settings.model} model was not downloaded (cancelled or failed)")
    logger.info("Model loaded on CPU (fallback from CUDA)")
    _record_engine(settings.model, "cpu")
    notify_module.notify("Couldn't use the NVIDIA graphics card",
                         "TuxTalk switched to the processor. Choose the device again in "
                         "Preferences to try the graphics card.")
    # Saved, so future service restarts don't try CUDA again and crash in a loop.
    try:
        from .config import save_config
        cfg = load_config()
        if cfg.device == "cuda":
            cfg.device = "cpu"
            save_config(cfg)
            logger.info("Updated config: device=cpu (CUDA unavailable, preventing a crash loop)")
    except Exception as e:
        logger.warning(f"Could not persist device=cpu after CUDA fallback: {e}")
    return model
