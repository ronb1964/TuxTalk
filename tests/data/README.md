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
and harmonics leaves at least 0.5 of what makes it louder than the room.
Each side is measured against its own room, the 20th percentile of its
blocks: the filter takes about a fifth of a room's noise too. The notch
width (80 Hz either side, eased over 80 Hz more) and the 0.5 came from this:

1. The beep: `beeps.tone(1200, 0.12, rate)` at 16, 44.1 and 48 kHz, at 0.1,
   1 and 4 times the level it plays at, with a small speaker's 2nd and 3rd
   harmonics, squashed with `tanh` at 4 times, and clipped at 1.1, 1.6 and
   4 times full scale, both hard (`np.clip`) and with `tanh`. Each was put
   into 1.2 s of steady room noise (int16 RMS 6, 60, 300, 400, 450, 480,
   500, 520, 600 and 700, 24 different noises each) starting at every
   millisecond of a 20 ms block, and also cut off at the start and at the
   end of the recording.
2. The speech: 121 recordings of quick phrases and sentences, 16 kHz, each
   resampled to 44.1 and 48 kHz as well: 40 one-to-three-word phrases ("it",
   "yes", "no wait", "sounds good") in a computer voice (Piper), the same 40
   with noise 20 dB under them, 14 sentences in that voice, 25 sentences read
   by a person on a desk microphone, and the two words above. Each was put
   into rooms of int16 RMS 6, 60, 300, 450, 600 and 700 (three different
   noises), from full level down to 60 dB under it in steps of 0.5 dB,
   with the beep and without it. Only the two JFK words are in the
   repository; the rest stayed on the computer they were measured on.
3. For each, how many blocks count as speech, filtered and not.

What was found, in rooms up to int16 RMS 450 at all three rates:

- 99% of the beep's energy lies within 40 Hz of 1200 Hz, and all but 0.2%
  within 100 Hz. Clipped at 1.6 times full scale, 2.8% of it is in
  harmonics on multiples of 1200 Hz and under 0.2% lies more than 160 Hz
  from one; clipped at 4 times, 10% and under 0.8%.
- A notch of 40 Hz either side or narrower rings on for a block as the
  beep starts and stops, so two of its blocks still counted. At 80 Hz and
  0.5, a beep never added more than one block to what the room alone
  counted, and steady room noise alone was never a word.
- How loud a word must be to count went up by at most 0.5 dB with the
  filter, or 2 dB in a room of 450, beep heard or not.
- 0.48 let a faint beep in a room of 450 at 16 kHz add two blocks: there it
  is loud only with the room's help, and the room's own unevenness is as
  loud as what it adds. 0.54 cost a soft word more. Measured against the
  room as recorded instead, the filter's share of the room's noise looked
  like lost speech, and a word in a room of 450 had to be up to 16 dB
  louder over all the recordings, and 4 dB for the two JFK words alone.
- From a room of about 480 up the room counts as a word by itself, beep or
  not, as it does with the beeps off: it is as loud as speaking. Asking each
  block to stand a quarter above the room as well made steady noise no word,
  but dropped 6% of the 0.3 to 0.5 s stretches of the speech above at full
  level, with no room added: words said in a quiet room.
- The filter costs under 5 ms for 2.4 s at 48 kHz.

The tests pin these limits: of a beep, one block at most counts
(`test_of_a_start_beep_one_block_at_most_counts`), the room alone is no word
(`test_the_room_on_its_own_is_no_word`), and a word only just loud enough
still counts within the measured cost
(`test_a_word_only_just_loud_enough_still_counts_with_the_filter`).
