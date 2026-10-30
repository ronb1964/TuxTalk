"""Your Words for Parakeet: let the decoder pick the user's words.

Whisper can be told about names before it listens (custom_words.prompt).
Parakeet can't, but its decoder writes one word piece at a time ("▁T",
"uck", "St", "ock"), and at a point where one of the user's words could
start, it can try spelling that word out instead. This is the idea of NVIDIA
NeMo's word boosting (Apache-2.0), on the greedy decoder TuxTalk uses:

- Each of Your Words, as written and capitalised, is matched letter by
  letter, so any way of spelling it in Parakeet's pieces counts.
- Where a piece that starts one of the words is close to Parakeet's own first
  choice, the decoder tries the whole word on a side branch: at each step it
  takes the next piece of the word if Parakeet rates it nearly as high as
  its favourite. The "cost" of the branch is how far below Parakeet's
  favourites its pieces were, added up.
- The branch is kept only if the word is complete and the cost is within a
  budget. Otherwise it is thrown away and decoding goes on exactly as
  Parakeet would have, so a word is either written whole or not at all,
  and ordinary speech nearby stays as it was.
- Nothing is ever written where Parakeet heard nothing: the branch may only
  replace a word piece with another, never start on silence.

The budgets are in the decoder's own score units (logits, which differ by
the same amount as log-probabilities). Tuned on synthetic speech in Oct
2026; tests/test_parakeet_bias.py has the cases.
"""

import numpy as np

# Word pieces start with this mark when they begin a word (SentencePiece).
WORD_START = "▁"

START_MARGIN = 6.0      # a word may start where its first piece is this close to the favourite
STEP_MARGIN = 6.0       # each later piece may be this far below the favourite
BLANK_MARGIN = 3.0      # mid-word, a piece may beat "nothing here" by this little
WORD_BUDGET = 8.0       # most a whole word may cost
MAX_FRAMES = 30         # a word longer than this many frames (~2.4 s) is given up
MAX_WORDS = 300         # far beyond any real list; keeps the tables small
MAX_TRIES = 3           # words tried at one starting point


def word_variants(word):
    """The spellings Parakeet might write *word* in: as given, and
    capitalised (sentence start, or a name typed in lower case)."""
    word = " ".join(word.split())
    variants = [word]
    if word[:1].islower():
        variants.append(word[:1].upper() + word[1:])
    return list(dict.fromkeys(v for v in variants if v))


class _Target:
    """One spelling of one word as a string of pieces' letters, and for each
    position the pieces that fit there: steps[pos] = [(piece id, next pos)]."""
    __slots__ = ("text", "steps")

    def __init__(self, text, piece_ids, longest):
        s = WORD_START + text.replace(" ", WORD_START)
        self.text = s
        self.steps = []
        for pos in range(len(s)):
            fits = []
            for n in range(1, min(longest, len(s) - pos) + 1):
                pid = piece_ids.get(s[pos:pos + n])
                if pid is not None:
                    fits.append((pid, pos + n))
            self.steps.append(fits)

    def reachable(self):
        """Whether the pieces can spell the whole word."""
        can = [False] * (len(self.text) + 1)
        can[0] = True
        for pos, fits in enumerate(self.steps):
            if can[pos]:
                for _pid, nxt in fits:
                    can[nxt] = True
        return can[len(self.text)]


class WordBooster:
    """The user's words in Parakeet's pieces, and the rules for trying them."""

    def __init__(self, vocab, words, blank_idx, *, start_margin=START_MARGIN,
                 step_margin=STEP_MARGIN, blank_margin=BLANK_MARGIN,
                 word_budget=WORD_BUDGET, max_frames=MAX_FRAMES):
        """Spell each of *words* (up to MAX_WORDS, with their variants) in the
        pieces of *vocab* that can make it, dropping the ones no pieces can
        spell. The margins and budget say how far Parakeet's own guess may
        be overruled."""
        self.blank_idx = blank_idx
        self.start_margin = start_margin
        self.step_margin = step_margin
        self.blank_margin = blank_margin
        self.word_budget = word_budget
        self.max_frames = max_frames
        # Usable pieces only: no <special> tokens and no blank. onnx-asr
        # stores the word-start mark as a plain space; the vocabulary file
        # (and whisper.cpp) keep "▁". Both are read the same.
        piece_ids = {piece.replace(" ", WORD_START): pid for pid, piece in vocab.items()
                     if pid != blank_idx and not (piece.startswith("<") and piece.endswith(">"))}
        longest = max((len(p) for p in piece_ids), default=0)
        self.targets = []
        for word in list(words)[:MAX_WORDS]:
            for variant in word_variants(word):
                target = _Target(variant, piece_ids, longest)
                if target.reachable():
                    self.targets.append(target)
        # Starting pieces: piece id -> [(target, next pos)]
        self.starts = {}
        for target in self.targets:
            for pid, nxt in target.steps[0]:
                self.starts.setdefault(pid, []).append((target, nxt))

    def __bool__(self):
        return bool(self.targets)

    def starts_near(self, logits, best):
        """The (score, piece, target, next pos) a word could start with at
        this step, best first."""
        floor = float(logits[best]) - self.start_margin
        found = []
        for pid, entries in self.starts.items():
            score = float(logits[pid])
            if score >= floor:
                for target, nxt in entries:
                    found.append((score, pid, target, nxt))
        found.sort(key=lambda f: -f[0])
        return found[:MAX_TRIES]


def engine_settings(booster):
    """The six settings whisper.cpp's parakeet_set_word_boost takes (TuxTalk's
    patch of the Vulkan engine), in its order."""
    return [booster.start_margin, booster.step_margin, booster.blank_margin,
            booster.word_budget, float(booster.max_frames), float(MAX_TRIES)]


def engine_tables(booster):
    """The booster's spellings as the flat number lists the engine takes:
    target_pos_begin, pos_entry_begin, entry_pid, entry_next (see
    parakeet_set_word_boost in the patched include/parakeet.h)."""
    target_pos_begin, pos_entry_begin, entry_pid, entry_next = [0], [0], [], []
    for target in booster.targets:
        for fits in target.steps:
            for pid, nxt in fits:
                entry_pid.append(pid)
                entry_next.append(nxt)
            pos_entry_begin.append(len(entry_pid))
        target_pos_begin.append(len(pos_entry_begin) - 1)
    return target_pos_begin, pos_entry_begin, entry_pid, entry_next


def boosted_tdt_decoding(asr, booster, on_word=None):
    """A stand-in for onnx-asr's _AsrWithTransducerDecoding._decoding on *asr*.
    The plain path follows onnx-asr 0.12 (MIT) line for line; where one of
    the user's words could start, it tries the word on a branch first.
    *on_word(text, cost, kept)* hears about every try (for the log and tests)."""
    blank = asr._blank_idx
    max_per_step = asr._max_tokens_per_step

    def _advance(t, step, token, emitted):
        """onnx-asr's rule for the next frame after writing *token*."""
        if step > 0:
            return t + step, 0
        if token == blank or emitted == max_per_step:
            return t + 1, 0
        return t, emitted

    def _try_word(encodings, length, tokens, timestamps, state, t, emitted,
                  logits, step, new_state, start):
        """Spell one word on a branch from this step. The branch's (tokens,
        timestamps, state, t, emitted), or None if the word didn't fit."""
        score0, pid, target, pos = start
        cost = float(logits.max()) - score0
        tokens, timestamps = tokens + [pid], timestamps + [t]
        state = new_state
        emitted += 1
        t, emitted = _advance(t, step, pid, emitted)
        first_t = t
        while pos < len(target.text):
            if t >= length or t - first_t > booster.max_frames or cost > booster.word_budget:
                return None, cost
            logits, step, new_state = asr._decode(tokens, state, encodings[t])
            best = int(logits.argmax())
            fits = target.steps[pos]
            if not fits:
                return None, cost
            pid, nxt = max(fits, key=lambda f: logits[f[0]])
            gap = float(logits[best]) - float(logits[pid])
            if best == blank and gap > booster.blank_margin:
                # Parakeet hears nothing yet: wait for the next frame.
                t, emitted = _advance(t, step, blank, emitted)
                continue
            if best != blank and best != pid and gap > booster.step_margin:
                return None, cost
            if best != pid:
                cost += gap
            tokens, timestamps = tokens + [pid], timestamps + [t]
            state = new_state
            emitted += 1
            t, emitted = _advance(t, step, pid, emitted)
            pos = nxt
        if cost > booster.word_budget:
            return None, cost
        return (tokens, timestamps, state, t, emitted), cost

    def _decoding(encoder_out, encoder_out_lens, /, **kwargs):
        """onnx-asr's greedy TDT decoding, plus: where a frame's best piece
        could start one of the user's words, that word is tried, and kept if
        it fits within the budget. Yields tokens, timestamps and
        log-probabilities as onnx-asr's own decoding does; asked for
        log-probabilities, it steers nothing."""
        need_logprobs = kwargs.get("need_logprobs")
        if asr.use_low_precision:
            encoder_out_lens = np.minimum(encoder_out_lens, encoder_out.shape[1])
        for encodings, encodings_len in zip(encoder_out, encoder_out_lens, strict=True):
            prev_state = asr._create_state()
            tokens, timestamps, logprobs = [], [], []
            t = 0
            emitted_tokens = 0
            while t < encodings_len:
                logits, step, state = asr._decode(tokens, prev_state, encodings[t])
                token = int(logits.argmax())

                if token != blank and not need_logprobs:
                    kept = None
                    for start in booster.starts_near(logits, token):
                        branch, cost = _try_word(encodings, encodings_len, tokens, timestamps,
                                                 prev_state, t, emitted_tokens,
                                                 logits, step, state, start)
                        if on_word is not None:
                            on_word(start[2].text.replace(WORD_START, " ").strip(), cost,
                                    branch is not None)
                        if branch is not None:
                            kept = branch
                            break
                    if kept is not None:
                        tokens, timestamps, prev_state, t, emitted_tokens = kept
                        continue

                if token != blank:
                    prev_state = state
                    tokens.append(token)
                    timestamps.append(t)
                    emitted_tokens += 1
                    if need_logprobs:
                        shifted = logits - logits.max()
                        logprobs.append(float(shifted[token] - np.log(np.exp(shifted).sum())))
                t, emitted_tokens = _advance(t, step, token, emitted_tokens)
            yield tokens, timestamps, logprobs if need_logprobs else None

    return _decoding
