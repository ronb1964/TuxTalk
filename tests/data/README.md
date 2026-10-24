# Test recordings

## jfk-ask.wav and jfk-not.wav

Two quick words, "ask" and "not", said by a person, used by
`tests/test_silence.py` to check that a word said before the second tap of a
double-tap still counts when the start beep is filtered out.

- Source: `samples/jfk.wav` from whisper.cpp
  (https://github.com/ggml-org/whisper.cpp), unchanged since the commit that
  added it, b0a11594aec50892a02cd8d129eee2dfe93a8bb8. Its SHA256 is
  `59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e`.
- Cut points, in samples of the 16 kHz original: `jfk-ask.wav` is samples
  51840 to 60160 (3.24 s to 3.76 s), `jfk-not.wav` samples 63200 to 69760
  (3.95 s to 4.36 s). Both are 16 kHz mono 16-bit PCM, the samples copied
  as they are.
- Rights: the recording is of John F. Kennedy's inaugural address of
  20 January 1961, a work of the United States government made by the
  President in his official duties, so it is in the public domain in the
  United States (17 U.S.C. 105).

## How the start beep's filter was measured

`silence.has_a_word(audio, rate, ignoring_hz=...)` counts a 20 ms block as
speech only where it is loud on its own and filtering out the beep's pitch
and harmonics leaves at least 0.4 of what makes it louder than the room. The
notch width (80 Hz either side, eased over 80 Hz more) and the 0.4 came from
this:

1. The beep: `beeps.tone(1200, 0.12, rate)` at 16, 44.1 and 48 kHz, at 0.1,
   1, and 4 times the level it plays at, squashed with `tanh` at 4 times, and
   clipped at 1.1 and 1.6 times full scale, both hard (`np.clip`) and with
   `tanh`. Each was put into 1.2 s of room noise (int16 RMS 6, 100, 300 and
   400) starting at every millisecond of a 20 ms block, and also cut off at
   the start and at the end of the recording.
2. The speech: 121 recordings of quick phrases and sentences, 16 kHz, each
   resampled to 44.1 and 48 kHz as well: 40 one-to-three-word phrases ("it",
   "yes", "no wait", "sounds good") in a computer voice (Piper), the same 40
   with noise 20 dB under them, 14 sentences in that voice, 25 sentences read
   by a person on a desk microphone, and the two words above; and "not" at
   0.01 of its level in a room of RMS 6, only just loud enough to count.
   Only the two JFK words are in the repository; the rest stayed on the
   computer they were measured on.
3. For every block that counts as loud on its own, the share of its
   loudness over the room that the filter left: the beep's blocks against
   the speech's, for notches from 10 to 200 Hz either side.

What was found:

- 99% of the beep's energy lies within 40 Hz of 1200 Hz, and all but 0.2%
  within 100 Hz. Clipping at 1.6 times full scale puts 3% of it into
  harmonics on multiples of 1200 Hz; what clipping aliases off those
  multiples at 16 and 44.1 kHz was under 0.1%.
- A notch of 40 Hz either side or narrower rings on for a block as the
  beep starts and stops, so two of its blocks still counted. At 80 Hz, one
  block at most counted, and every other beep block kept at most 0.31.
- No block of speech kept less than 0.47 at 80 Hz, so no recording lost any
  of its loud time. The old filter, 200 Hz either side eased over 200 Hz and
  judged on what it left, took the soft "not" from 0.22 s of loud time to
  0.04 s, under the 0.08 s a word needs.
- The filter costs under 5 ms for 2.4 s at 48 kHz.

The tests pin both limits: of a beep, one block at most counts
(`test_of_a_start_beep_one_block_at_most_counts`), and the filter takes no
block from a word (`test_the_filter_for_the_start_beep_takes_nothing_from_a_word`).
