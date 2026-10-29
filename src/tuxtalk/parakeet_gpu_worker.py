"""
The helper process that runs Parakeet on the graphics chip.

Started by parakeet_gpu.py as

    python -m tuxtalk.parakeet_gpu_worker <engine dir> <model> <device> <threads>

It loads whisper.cpp's libparakeet (part of the Vulkan engine, build 2 and
later) through ctypes, keeps the model on the graphics chip, and transcribes
whatever the dictation service sends it. It is a separate process because a
failed check inside the engine (GGML_ASSERT) aborts the whole process: here
that loses the helper, not the dictation service.

Protocol, over stdin and stdout:
  service -> helper: a 4-byte little-endian sample count, then that many
                     float32 samples (16 kHz mono)
  helper -> service: one line of JSON, {"text": ...} or {"error": ...}
  service -> helper: WORDS_REQUEST as the count, a 4-byte length and a JSON
                     list of Your Words (parakeet_bias.py)
  helper -> service: {"words": number of spellings}, or {"words": null} when
                     the engine predates TuxTalk's word-boost patch
Once the model is loaded and warmed up the helper sends {"ready": true}, or
{"error": ...} if it couldn't load.
"""
import ctypes as C
import json
import os
import signal
import struct
import sys

SAMPLE_RATE = 16000
WORDS_REQUEST = 0xFFFFFFFF        # in the count's place: a word list follows
WARM_UP_SECONDS = (1, 30, 90)     # silence, covering short, long and very long dictations
PR_SET_PDEATHSIG = 1


# --- The protocol ------------------------------------------------------------------

def encode_request(samples) -> bytes:
    """A request for float32 *samples* (a numpy array)."""
    return struct.pack("<I", len(samples)) + samples.astype("<f4").tobytes()


def encode_words(words) -> bytes:
    """A request that sets Your Words."""
    data = json.dumps(list(words)).encode()
    return struct.pack("<II", WORDS_REQUEST, len(data)) + data


def _read_exact(stream, n):
    """Exactly *n* bytes from *stream*, or None if it ends first."""
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def read_request(stream):
    """The raw float32 bytes of the next request, a list for a word list,
    or None when the service has closed the pipe."""
    head = _read_exact(stream, 4)
    if head is None:
        return None
    (count,) = struct.unpack("<I", head)
    if count == WORDS_REQUEST:
        size = _read_exact(stream, 4)
        if size is None:
            return None
        data = _read_exact(stream, struct.unpack("<I", size)[0])
        return None if data is None else json.loads(data.decode())
    return _read_exact(stream, count * 4) if count else b""


def write_reply(stream, obj):
    """Send *obj* as one line of JSON (json.dumps escapes newlines inside strings)."""
    stream.write((json.dumps(obj) + "\n").encode())
    stream.flush()


def join_segments(texts) -> str:
    """The segments' texts joined by single spaces, empty ones dropped."""
    return " ".join(t.strip() for t in texts if t.strip())


# --- The engine ----------------------------------------------------------------------
# Mirrors include/parakeet.h of whisper.cpp 1.9.5 (unchanged from 1.9.4), the version the engine is
# built from (whisper_vulkan.ENGINE_VERSION). Recheck these when it changes.

class _ContextParams(C.Structure):
    _fields_ = [("use_gpu", C.c_bool), ("gpu_device", C.c_int)]


class _FullParams(C.Structure):
    _fields_ = [("strategy", C.c_int), ("n_threads", C.c_int),
                ("offset_ms", C.c_int), ("duration_ms", C.c_int),
                ("no_context", C.c_bool), ("audio_ctx", C.c_int)] + \
               [(f"callback_{i}", C.c_void_p) for i in range(12)]   # 6 callbacks + user data


class Engine:
    """The loaded model: transcribe(raw float32 bytes) -> text, and
    set_words(words) -> number of spellings, or None without the patch."""

    def __init__(self, transcribe, set_words):
        self.transcribe = transcribe
        self.set_words = set_words


def _word_booster(lib, ctx, words):
    """parakeet_bias's spellings of *words* in this engine's vocabulary."""
    from .parakeet_bias import WordBooster
    lib.parakeet_n_vocab.argtypes = [C.c_void_p]
    lib.parakeet_token_to_str.restype = C.c_char_p
    lib.parakeet_token_to_str.argtypes = [C.c_void_p, C.c_int]
    n_vocab = lib.parakeet_n_vocab(ctx)
    vocab = {i: (lib.parakeet_token_to_str(ctx, i) or b"").decode("utf-8", "replace")
             for i in range(n_vocab)}
    return WordBooster(vocab, words, n_vocab)    # the blank comes right after the pieces


def load_engine(engine_dir, model, device, threads):
    """Load *model* onto graphics chip *device*; returns an Engine."""
    # ggml's libraries first, shared, so libparakeet and the backends find them.
    # Opening libggml.so again only returns a handle to the loaded copy.
    for name in ("libggml-base.so", "libggml.so"):
        C.CDLL(os.path.join(engine_dir, name), mode=C.RTLD_GLOBAL)
    ggml = C.CDLL(os.path.join(engine_dir, "libggml.so"), mode=C.RTLD_GLOBAL)
    # The engine is built with its Vulkan and processor backends as plug-ins
    # (GGML_BACKEND_DL). They must be loaded before the model, or the engine
    # finds no devices and aborts. whisper-server does the same at start-up.
    ggml.ggml_backend_load_all_from_path.argtypes = [C.c_char_p]
    ggml.ggml_backend_load_all_from_path(engine_dir.encode())

    lib = C.CDLL(os.path.join(engine_dir, "libparakeet.so"))
    lib.parakeet_init_from_file_with_params.restype = C.c_void_p
    lib.parakeet_init_from_file_with_params.argtypes = [C.c_char_p, _ContextParams]
    lib.parakeet_full_default_params.restype = _FullParams
    lib.parakeet_full_default_params.argtypes = [C.c_int]
    lib.parakeet_full.argtypes = [C.c_void_p, _FullParams, C.POINTER(C.c_float), C.c_int]
    lib.parakeet_full_n_segments.argtypes = [C.c_void_p]
    lib.parakeet_full_get_segment_text.restype = C.c_char_p
    lib.parakeet_full_get_segment_text.argtypes = [C.c_void_p, C.c_int]

    ctx = lib.parakeet_init_from_file_with_params(model.encode(), _ContextParams(True, device))
    if not ctx:
        raise RuntimeError(f"the engine could not load {os.path.basename(model)}")
    params = lib.parakeet_full_default_params(0)        # PARAKEET_SAMPLING_GREEDY
    params.n_threads = threads

    def transcribe(raw):
        count = len(raw) // 4
        samples = (C.c_float * count).from_buffer_copy(raw)
        result = lib.parakeet_full(ctx, params, samples, count)
        if result != 0:
            raise RuntimeError(f"the engine failed to transcribe (code {result})")
        return join_segments(
            (lib.parakeet_full_get_segment_text(ctx, i) or b"").decode("utf-8", "replace")
            for i in range(lib.parakeet_full_n_segments(ctx)))

    def set_words(words):
        try:
            setter = lib.parakeet_set_word_boost
        except AttributeError:
            return None             # an engine without TuxTalk's patch
        from .parakeet_bias import engine_settings, engine_tables
        booster = _word_booster(lib, ctx, words)
        targets, positions, pids, nexts = engine_tables(booster)
        settings = engine_settings(booster)
        ints = lambda xs: (C.c_int * max(1, len(xs)))(*xs)
        setter.restype = C.c_int
        setter.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_int), C.c_int, C.POINTER(C.c_int),
                           C.c_int, C.POINTER(C.c_int), C.POINTER(C.c_int),
                           C.POINTER(C.c_float), C.c_int]
        n = setter(ctx, len(targets) - 1, ints(targets), len(positions) - 1, ints(positions),
                   len(pids), ints(pids), ints(nexts), (C.c_float * 6)(*settings), 6)
        if n < 0:
            raise RuntimeError("the engine refused the word tables")
        return n

    return Engine(transcribe, set_words)


def die_with_parent():
    """Have the kernel kill this helper when the thread that started it
    exits, even if the dictation service is killed with SIGKILL, so it can't
    sit orphaned on the graphics chip's memory. Set here rather than between
    fork and exec in the service (see engine_process.with_parent_death). Had
    the service died before this, stdin is already closed and main() ends."""
    if C.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGKILL) != 0:
        print(f"couldn't set the parent-death signal: {os.strerror(C.get_errno())}",
              file=sys.stderr)


def main(argv):
    """Load the model, report ready, then answer requests until stdin closes."""
    die_with_parent()
    engine_dir, model, device, threads = argv[0], argv[1], int(argv[2]), int(argv[3])
    # Replies go out on a private copy of stdout. The real stdout is pointed
    # at stderr, so nothing the engine prints can get mixed into a reply.
    out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    inp = sys.stdin.buffer
    try:
        engine = load_engine(engine_dir, model, device, threads)
        transcribe = engine.transcribe
        # The engine sets up its graphics shaders lazily, for each size range
        # of work the first time it meets one: 8 s for the first 1 s clip and
        # 7 s for the first 22 s one on an RTX 4070 Super. The driver keeps
        # them on disk after that, so this costs about 0.3 s on later starts,
        # and nobody's first long dictation stalls for seconds.
        for seconds in WARM_UP_SECONDS:
            transcribe(bytes(SAMPLE_RATE * seconds * 4))
    except Exception as e:
        write_reply(out, {"error": str(e)})
        return 1
    write_reply(out, {"ready": True})
    while (raw := read_request(inp)) is not None:
        try:
            if isinstance(raw, list):
                write_reply(out, {"words": engine.set_words(raw)})
            else:
                write_reply(out, {"text": transcribe(raw)})
        except Exception as e:
            write_reply(out, {"error": str(e)})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
