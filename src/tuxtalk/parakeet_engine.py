"""
NVIDIA Parakeet speech engine for TuxTalk.

Parakeet TDT 0.6B v3 is an alternative to Whisper for English and 24 other
European languages. On a plain CPU it is several times faster than Whisper and
more accurate than Whisper large-v3, a combination no Whisper model offers on
a CPU. It adds its own punctuation and capitalisation, and it detects the
language by itself.

It runs on onnxruntime, which the AppImage already ships because faster-whisper
depends on it, so supporting it costs only the small `onnx-asr` package. The
model itself (~670 MB) is downloaded at runtime: Parakeet Ultra, a more
accurate post-training of the same model (parakeet_ultra.py).

The models are published under CC-BY-4.0, which requires attribution; see
PARAKEET_ATTRIBUTION, shown in the About dialog.

The rest of TuxTalk treats "parakeet-v3" as just another model name, so the
model pickers, config validation and download dialogs all work unchanged.

With the device set to "Vulkan (any GPU)", Parakeet runs on the graphics chip
instead, through whisper.cpp (parakeet_gpu.py). This processor version stays
downloaded as the fallback.
"""
import os

# The 25 languages Parakeet TDT 0.6B v3 knows (ISO 639-1), from NVIDIA's model
# card. It detects which one is spoken by itself and ignores TuxTalk's
# language setting, so for any other language the user needs a Whisper model.
PARAKEET_LANGUAGES = frozenset({
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
    "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
})

from .logger import setup_logger

logger = setup_logger(__name__)

# The name stored in config.toml and shown in the model pickers.
PARAKEET_MODEL = "parakeet-v3"

# The original v3 that TuxTalk downloaded before Parakeet Ultra: istupakov's
# int8 export (the author of onnx-asr). Still used where it's on disk and Ultra
# isn't, until the user takes the update; nothing downloads it any more.
V3_REPO = "istupakov/parakeet-tdt-0.6b-v3-onnx"
V3_FILES = (
    "config.json",
    "vocab.txt",
    "nemo128.onnx",                    # audio preprocessor
    "decoder_joint-model.int8.onnx",
    "encoder-model.int8.onnx",
)

# The model type name onnx-asr uses for this model.
_ONNX_ASR_MODEL = "nemo-parakeet-tdt-0.6b-v3"

PARAKEET_ATTRIBUTION = (
    "Parakeet Ultra speech model by Moondream, post-trained from Parakeet TDT 0.6B v3 "
    "by NVIDIA; ONNX export by Olicorne. All licensed under CC-BY-4.0."
)


def is_parakeet(model_name) -> bool:
    """True if *model_name* selects the Parakeet engine rather than Whisper."""
    return model_name == PARAKEET_MODEL


def effective_device(model_name, device) -> str:
    """The device a model really runs on, for the menus. Parakeet runs on the
    graphics chip only through Vulkan, and only once its files for that are
    downloaded (parakeet_gpu.py); otherwise it's on the processor, so a
    leftover "cuda" setting must not make the menus claim it's on the GPU."""
    if not is_parakeet(model_name):
        return device or "cpu"
    if (device or "").lower() == "vulkan":
        from . import whisper_vulkan
        if whisper_vulkan.is_installed(model_name):
            return "vulkan"
    return "cpu"


def cached_model_dir():
    """Folder holding a complete Parakeet for the processor, or None: Parakeet
    Ultra once it's downloaded, else the original v3 an earlier TuxTalk did.

    Checks file presence only, so it is cheap enough for the GTK main thread.
    Both downloads put a file in place only once it has fully arrived, so a
    cancelled download reports None.
    """
    from . import parakeet_ultra
    ultra = parakeet_ultra.cpu_dir()
    if ultra is not None:
        return ultra
    from .download_utils import cached_hub_file
    first = cached_hub_file(V3_REPO, V3_FILES[0])
    if first is None or any(cached_hub_file(V3_REPO, name) is None for name in V3_FILES[1:]):
        return None
    # All files come from one repo snapshot, so they share a folder.
    return os.path.dirname(first)


class ParakeetModel:
    """Loaded Parakeet model.

    Offers a `recognize` method in place of faster-whisper's `transcribe`,
    and the dictation code branches on is_parakeet(). The two engines return
    such different results that forcing them behind one interface would hide
    more than it saved.
    """

    def __init__(self, model_dir: str):
        import onnx_asr
        # CPU only. The onnxruntime we ship is the CPU build, and on CPU
        # Parakeet already transcribes a 10 s clip in well under a second.
        self._model = onnx_asr.load_model(
            _ONNX_ASR_MODEL,
            model_dir,
            quantization="int8",
            providers=["CPUExecutionProvider"],
        )

    def recognize(self, audio_f32) -> str:
        """Transcribe 16 kHz mono float32 audio and return the text."""
        return (self._model.recognize(audio_f32, sample_rate=16000) or "").strip()

    def set_words(self, words) -> None:
        """Lean toward Your Words (parakeet_bias.py). Cheap to call before
        every dictation: nothing happens unless the list changed. Anything
        unexpected inside onnx-asr leaves the plain decoder in place."""
        words = tuple(words or ())
        if words == getattr(self, "_words", ()):
            return
        self._words = words
        asr = getattr(self._model, "asr", None)
        if asr is None:
            return
        plain = getattr(self, "_plain_decoding", None)
        if plain is None:
            plain = self._plain_decoding = asr._decoding
        asr._decoding = plain
        if not words:
            logger.info("Your Words for Parakeet: none")
            return
        try:
            from . import parakeet_bias
            booster = parakeet_bias.WordBooster(asr._vocab, words, asr._blank_idx)
            if booster:
                asr._decoding = parakeet_bias.boosted_tdt_decoding(asr, booster, on_word=_log_word)
            logger.info(f"Your Words for Parakeet: {len(booster.targets)} spellings of "
                        f"{len(words)} words")
        except Exception as e:
            asr._decoding = plain
            logger.warning("Your Words can't steer Parakeet, so only the spelling pass "
                           f"applies: {e}")


def _log_word(word, cost, kept):
    """Log that parakeet_bias wrote one of Your Words (not every try: there
    are many). Not which word: that is something the user said, and the log
    keeps none of that unless log_transcripts is on, when the transcription
    line shows it anyway."""
    if kept:
        logger.info(f"Your Words: Parakeet wrote one of your words (cost {cost:.1f})")


def load_parakeet_model():
    """Load Parakeet from the local download.

    Raises FileNotFoundError if it has not been downloaded, so the caller can
    offer the download instead of failing mysteriously inside onnx-asr.
    """
    model_dir = cached_model_dir()
    if model_dir is None:
        raise FileNotFoundError("Parakeet model has not been downloaded")
    logger.info(f"Loading Parakeet from {model_dir}")
    return ParakeetModel(model_dir)
