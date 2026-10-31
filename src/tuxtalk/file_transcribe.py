"""Turning an audio or video file into text (Tray → Transcribe a File...).

This is the part with no window in it: reading the file, cutting it into
pieces, running the speech model over each piece, and writing the result as
text or as subtitles. transcribe_window.py is the window around it.

It runs in its own process with its own copy of the model, never inside the
dictation service: a long file takes minutes, and dictation has to keep
working the whole time. The model is the one the user dictates with, on the
same device, so nothing new is downloaded.

Every engine gets the same treatment, so progress, Cancel and the subtitle
timing work alike for all of them:

- The file is decoded with PyAV, which faster-whisper already brings along
  (with FFmpeg's libraries inside it), so MP3, M4A, OGG, FLAC, WAV and the
  sound of MP4, MKV and WebM videos all read without anything installed.
- It is cut into pieces of 16 to 30 seconds, each cut at the quietest moment
  in that stretch, which is nearly always a pause between words. 30 seconds
  is what Whisper reads at once, and it keeps Parakeet's memory use small.
- A piece that is silent, or as good as, is skipped. It saves time, and
  it's where Whisper invents "Thanks for watching!". Only near silence: a
  quiet talk taped from the back of a room still goes to the model, since
  losing real words without a word said is worse than an odd phrase. And
  nothing else is filtered: unlike a dictation, a video's real "like and
  subscribe" should stay.
"""
import os
import re
from dataclasses import dataclass

import numpy as np

from .logger import setup_logger

logger = setup_logger(__name__)

SAMPLE_RATE = 16000
MIN_PIECE_S = 16
MAX_PIECE_S = 30

# What the file picker offers. PyAV reads far more than this, so the picker
# also has an "All files" choice.
AUDIO_EXTENSIONS = ("mp3", "m4a", "aac", "ogg", "oga", "opus", "flac", "wav", "wma", "amr", "aiff")
VIDEO_EXTENSIONS = ("mp4", "mkv", "webm", "mov", "avi", "m4v", "wmv", "flv", "3gp", "mpeg", "mpg",
                    "ts")


class NotDownloaded(Exception):
    """The model the user dictates with isn't downloaded."""


class NoSound(Exception):
    """The file has no sound TuxTalk can read."""


@dataclass
class Piece:
    """One piece of the file: where it is (seconds) and what was said in it."""
    start: float
    end: float
    text: str


# --- reading and cutting ---------------------------------------------------------------

def decode(path):
    """The file's sound as 16 kHz mono float32. Raises NoSound for a file
    PyAV can't open or one with no sound in it."""
    try:
        audio = _decode_with_av(path)
    except Exception as e:
        raise NoSound(f"TuxTalk couldn't read the sound in this file ({e})") from e
    if audio is None or len(audio) == 0:
        raise NoSound("This file has no sound in it.")
    return np.asarray(audio, dtype=np.float32)


def _decode_with_av(path):
    """The first sound track of *path* as 16 kHz mono float32, read with
    PyAV. Not faster-whisper's decode_audio: it passes av.open() an option
    PyAV 19 removed (metadata_errors), so with a current PyAV it can't read
    any file. A frame that won't decode is skipped, as there."""
    import av
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks = []

    def add(frames):
        for frame in frames:
            chunks.append(frame.to_ndarray().reshape(-1))

    with av.open(path, mode="r") as container:
        if not container.streams.audio:
            return np.zeros(0, dtype=np.float32)
        # Packet by packet: container.decode() is a generator, and the first
        # frame that won't decode ends it, with the rest of the file unread.
        for packet in container.demux(container.streams.audio[0]):
            try:
                frames = packet.decode()
            except av.error.InvalidDataError:
                continue
            for frame in frames:
                add(resampler.resample(frame))
        add(resampler.resample(None))       # what the resampler still holds
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def cut_points(audio, sample_rate=SAMPLE_RATE, min_s=MIN_PIECE_S, max_s=MAX_PIECE_S):
    """(start, end) sample indexes of the pieces, end exclusive. Each piece
    is at most *max_s* long and ends in the quietest half second between
    *min_s* and *max_s*; the last one takes whatever is left.

    Half a second, not the single quietest moment: a comma's short pause can
    be as quiet as the gap between two sentences, and cutting there splits a
    sentence. Averaged over half a second, the longer gap wins."""
    frame = sample_rate // 20
    smooth = np.ones(9) / 9           # nine 50 ms frames
    pieces, start, n = [], 0, len(audio)
    while n - start > max_s * sample_rate:
        lo, hi = start + min_s * sample_rate, start + max_s * sample_rate
        frames = (hi - lo) // frame
        energy = np.square(audio[lo:lo + frames * frame].reshape(frames, frame)).mean(axis=1)
        # Divided by the same average of ones: "same" pads the ends with
        # zeros, which would make the window's edges look quieter than they are.
        energy = (np.convolve(energy, smooth, mode="same")
                  / np.convolve(np.ones_like(energy), smooth, mode="same"))
        cut = lo + int(np.argmin(energy)) * frame + frame // 2
        pieces.append((start, cut))
        start = cut
    if n > start:
        pieces.append((start, n))
    return pieces


# --- the engine ---------------------------------------------------------------------------

class FileEngine:
    """The user's model, loaded for a file: .text(audio) -> what was said.

    *describe* is what the window shows, like "Parakeet on the graphics card"."""

    def __init__(self, model, kind, describe, language=None, limit_to=(), words=()):
        self.model, self.kind, self.describe = model, kind, describe
        self.language, self.limit_to, self.words = language, tuple(limit_to), list(words)

    def text(self, audio):
        """The text of one piece of the file, from whichever engine is loaded,
        with the user's words steered and spelled as dictation does."""
        from . import custom_words, language_limit
        if self.kind == "parakeet":
            from .parakeet_engine import steer
            steer(self.model, self.words)       # as dictation does (engine._transcribe_parakeet)
            text = self.model.recognize(audio)
        elif self.kind == "vulkan":
            text = self.model.transcribe(audio, self.language, timeout=600,
                                         prompt=custom_words.prompt(self.words) or None,
                                         limit_to=self.limit_to)
        else:
            from .engine import REPETITION_GUARDS
            options = dict(beam_size=5, vad_filter=False, **REPETITION_GUARDS,
                           hotwords=custom_words.prompt(self.words))
            segments, _ = language_limit.whisper_held_to(
                self.model.transcribe, audio, self.language, self.limit_to, **options)
            text = " ".join(s.text.strip() for s in segments)
        text = " ".join((text or "").split())
        if self.words:
            text = custom_words.fix_spelling(text, self.words)
        return text

    def reload_words(self):
        """Your Words as they are now: they can change between two files."""
        from . import custom_words
        self.words = custom_words.load()

    def close(self):
        close = getattr(self.model, "close", None)
        if close:
            try:
                close()
            except Exception as e:      # closing is the last thing it does: nothing to undo
                logger.info(f"Couldn't close the file engine cleanly: {e}")


def engine_settings(cfg):
    """The settings load_engine depends on; when any of them changes, the
    engine has to be loaded again."""
    return (cfg.model, (cfg.device or "cpu").lower(), cfg.language or "",
            getattr(cfg, "auto_languages", ""))


def load_engine(cfg):
    """The model *cfg* dictates with, loaded for files. Graphics first when
    that's the device, the processor when the graphics chip can't be used.
    Raises NotDownloaded when the model isn't on this computer."""
    from . import custom_words, language_limit, whisper_vulkan
    from .model_helper import is_model_cached_fast, whisper_model
    from .parakeet_engine import is_parakeet, load_parakeet_model
    from .recommend import model_title

    name, device = cfg.model, (cfg.device or "cpu").lower()
    parakeet = is_parakeet(name)
    title = model_title(name)
    language, limit_to = language_limit.language_and_limit(
        cfg.language, language_limit.parse(getattr(cfg, "auto_languages", "")))
    words = custom_words.load()
    common = dict(language=language, limit_to=limit_to, words=words)

    gpu_failed = False
    if device == "vulkan" and whisper_vulkan.is_installed(name):
        gpu_failed = True       # until it starts
        try:
            index = whisper_vulkan.find_device()
            if index is not None:
                if parakeet:
                    from .parakeet_gpu import ParakeetGpuModel
                    model = ParakeetGpuModel(index)
                    return FileEngine(model, "parakeet", f"{title} on the graphics card", **common)
                model = whisper_vulkan.VulkanWhisperModel(name, index)
                return FileEngine(model, "vulkan", f"{title} on the graphics card", **common)
        except Exception as e:
            logger.warning(f"File transcription: graphics card not used ({e}); using the processor")
    # The graphics copy is there but wouldn't start: say that, not "not downloaded".
    missing = (f"{title} couldn't start on the graphics card, and its processor copy isn't "
               f"downloaded." if gpu_failed else f"{title} isn't downloaded yet.")

    if parakeet:
        try:
            return FileEngine(load_parakeet_model(), "parakeet", f"{title} on the processor",
                              **common)
        except FileNotFoundError as e:
            raise NotDownloaded(missing) from e

    if not is_model_cached_fast(name):
        raise NotDownloaded(missing)
    if device == "cuda":
        try:
            from .cuda_helper import has_cuda_libraries
            if has_cuda_libraries():
                model = whisper_model(name, device="cuda", compute_type="float16",
                                      local_files_only=True)
                return FileEngine(model, "whisper", f"{title} on the graphics card (CUDA)",
                                  **common)
        except Exception as e:
            logger.warning(f"File transcription: CUDA not used ({e}); using the processor")
    model = whisper_model(name, device="cpu", compute_type="int8",
                          cpu_threads=os.cpu_count() or 4, local_files_only=True)
    return FileEngine(model, "whisper", f"{title} on the processor", **common)


# --- transcribing ---------------------------------------------------------------------------

# Near silence: no 50 ms of the piece louder than about -50 dBFS. A faint
# voice is well above it; a recorder's hiss in a pause is below it.
SILENT_RMS = 0.003


def is_silent(chunk, sample_rate=SAMPLE_RATE):
    """Whether nothing in *chunk* is louder than near silence.

    Not silence.has_no_speech, which is built for dictation: it judges talk
    against the recording's own background, and a quiet lecture 6 dB over
    the room's hum would count as nobody talking."""
    frame = sample_rate // 20
    frames = len(chunk) // frame
    if frames == 0:
        return float(np.sqrt(np.mean(np.square(chunk)))) < SILENT_RMS if len(chunk) else True
    rms = np.sqrt(np.square(chunk[:frames * frame].reshape(frames, frame)).mean(axis=1))
    return float(rms.max()) < SILENT_RMS


def transcribe(audio, engine, progress=None, cancelled=None):
    """The pieces of *audio* with what was said in each; pieces nobody
    talked in are left out. *progress(done_seconds, total_seconds)* is called
    after each piece; *cancelled()* returning True stops early, with the
    pieces done so far."""
    total = len(audio) / SAMPLE_RATE
    pieces = []
    for start, end in cut_points(audio):
        if cancelled and cancelled():
            break
        chunk = audio[start:end]
        if not is_silent(chunk):
            text = engine.text(chunk)
            if text:
                pieces.append(Piece(start / SAMPLE_RATE, end / SAMPLE_RATE, text))
        if progress:
            progress(end / SAMPLE_RATE, total)
    return pieces


# --- earlier transcripts, kept for next time ------------------------------------------------
# So closing the window or opening another file loses nothing: the window
# shows the newest one on opening, and Earlier transcripts lists them all.

RECENT_FILE = "recent-transcripts.json"
MAX_RECENT = 10
_OLD_LAST_FILE = "last-transcript.json"     # one kept transcript, before the list


class Kept:
    """One transcript kept for next time."""
    def __init__(self, path, pieces, total, when):
        self.path, self.pieces, self.total, self.when = path, pieces, total, when

    @property
    def name(self):
        return os.path.basename(self.path or "") or "A file"


def recent_path():
    from . import config
    return os.path.join(config.get_data_dir(), RECENT_FILE)


def _read_list(target):
    import json
    with open(target, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


def _entry(data):
    try:
        pieces = [Piece(float(a), float(b), str(t)) for a, b, t in data["pieces"]]
        if not pieces:
            return None
        return Kept(data.get("path"), pieces, float(data.get("total", 0)),
                    float(data.get("when", 0)))
    except (KeyError, TypeError, ValueError):
        return None


def recent():
    """The transcripts kept, newest first. Never raises."""
    target = recent_path()
    entries = []
    try:
        entries = _read_list(target)
    except FileNotFoundError:
        # The one transcript kept before there was a list comes along once.
        old = os.path.join(os.path.dirname(target), _OLD_LAST_FILE)
        try:
            entries = _read_list(old)
            kept = [e for e in (_entry(d) for d in entries) if e]
            if _write(kept):
                os.remove(old)
            return kept
        except (OSError, ValueError):
            return []
    except (OSError, ValueError):
        return []
    return [e for e in (_entry(d) for d in entries) if e][:MAX_RECENT]


def _write(entries):
    """Write the list whole or not at all, readable only by this user."""
    import json
    target = recent_path()
    tmp = f"{target}.tmp"
    data = [{"path": e.path, "total": e.total, "when": e.when,
             "pieces": [[p.start, p.end, p.text] for p in e.pieces]} for e in entries]
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, target)
        return True
    except OSError as e:
        logger.warning(f"Couldn't keep the transcripts for next time: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def remember(path, pieces, total):
    """Put this transcript first in the list (a file done again replaces its
    older copy) and keep MAX_RECENT. Returns the Kept, or None when it
    couldn't be written."""
    import time
    entry = Kept(path, list(pieces), total, time.time())
    others = [e for e in recent() if not (path and e.path == path)]
    entries = [entry] + others[:MAX_RECENT - 1]
    return entry if _write(entries) else None


def forget_all():
    """Clear the list. True when nothing is kept any more."""
    try:
        os.remove(recent_path())
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"Couldn't clear the earlier transcripts: {e}")
        return False
    return True


# --- writing it out -------------------------------------------------------------------------

def clock(seconds, srt=False):
    """1:05, 1:02:05, or SRT's 00:01:05,000."""
    ms = int(round(seconds * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    if srt:
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_SENTENCE_END = re.compile(r"[.!?…。！？]['\")\]]*$")


def cleaned(pieces):
    """The pieces with um, uh and repeated words taken out (cleanup.py's
    rules, no AI), for "Remove ums and repeats". The pieces themselves stay
    as heard. A piece that's left empty is dropped.

    The cleanup capitalizes what it leaves at the start ("uh, the" ->
    "The"); in a piece that carries on a sentence from the one before, the
    word keeps the case it was heard with."""
    from .cleanup import clean_fillers_and_repeats
    out, carries = [], False
    for piece in pieces:
        text = clean_fillers_and_repeats(piece.text)
        if text and carries:
            first = re.match(r"\w+", text)
            heard = first and re.search(rf"\b{re.escape(first.group())}\b", piece.text, re.I)
            if heard:
                text = heard.group() + text[first.end():]
        carries = not _SENTENCE_END.search(piece.text)
        if text:
            out.append(Piece(piece.start, piece.end, text))
    return out


def to_text(pieces, times=False):
    """The pieces as paragraphs. A piece that ends a sentence ends its
    paragraph; one cut mid-sentence carries on into the next. *times* starts
    each paragraph with where it is in the file, "[1:05]"."""
    paragraphs, current, began = [], [], 0.0
    for piece in pieces:
        if not current:
            began = piece.start
        current.append(piece.text)
        if _SENTENCE_END.search(piece.text):
            paragraphs.append((began, " ".join(current)))
            current = []
    if current:
        paragraphs.append((began, " ".join(current)))
    return "\n\n".join(f"[{clock(t)}] {p}" if times else p for t, p in paragraphs)


MAX_CUE_CHARS = 84      # two subtitle lines of 42, the usual limit
# Chinese and Japanese have no spaces and carry more in each character: two
# lines of 16 is the common limit there.
MAX_CUE_CHARS_UNSPACED = 32


def _cue_texts(text):
    """*text* in subtitle-sized parts: by sentence, and a long sentence in
    even parts, each cut at the comma or space nearest its share, so no
    single word is left on its own. Text written without spaces (Chinese,
    Japanese) is cut after its own commas, or anywhere near the share."""
    parts = []
    for sentence in re.split(r"(?<=[.!?…])\s+|(?<=[。！？])\s*", text):
        sentence = sentence.strip()
        spaced = " " in sentence
        limit = MAX_CUE_CHARS if spaced else MAX_CUE_CHARS_UNSPACED
        count = -(-len(sentence) // limit)
        while count > 1:
            target = len(sentence) // count
            # Once the last space is used up (an English word in Chinese
            # text), the rest is cut like unspaced text.
            places = [i for i, ch in enumerate(sentence) if ch == " "] if spaced else []
            places = places or list(range(1, len(sentence)))
            if not places:          # one character left: nothing to cut
                break
            # A comma close to the share is the better place to cut.
            cut = min(places, key=lambda i: abs(i - target)
                      - (8 if sentence[i - 1] in ",;、，；" else 0))
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
            count -= 1
        if sentence:
            parts.append(sentence)
    return parts


def to_srt(pieces):
    """The pieces as SRT subtitles. Each piece's time is shared among its
    sentences by their length: the engines don't time single words, so a
    cue can be off by a second or two, but never strays outside its piece."""
    cues = []
    for piece in pieces:
        parts = _cue_texts(piece.text)
        weights = [len(p) + 10 for p in parts]
        at, span = piece.start, piece.end - piece.start
        for part, weight in zip(parts, weights):
            length = span * weight / sum(weights)
            cues.append((at, at + length, part))
            at += length
    return "".join(f"{i}\n{clock(a, srt=True)} --> {clock(b, srt=True)}\n{t}\n\n"
                   for i, (a, b, t) in enumerate(cues, 1))
