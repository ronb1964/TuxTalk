"""Preferences asks one question about other sound while you dictate.

"Pause music and videos" and "Turn other sounds down" were two tickboxes; one
dropdown now sets both settings, which keep their meaning, so a config with
both on (pause players, turn a call down) still shows as it was.
"""
from tuxtalk.prefs_audio import SOUND_CHOICES, sound_choice


def test_every_combination_of_the_two_settings_has_an_entry():
    for pause in (True, False):
        for duck in (True, False):
            choice = sound_choice({"pause_media": pause, "duck_audio": duck})
            _label, chosen_pause, chosen_duck = SOUND_CHOICES[choice]
            assert (chosen_pause, chosen_duck) == (pause, duck)


def test_a_new_config_pauses_players():
    assert sound_choice({}) == "pause"
    assert SOUND_CHOICES["pause"][0] == "Pause music and videos"
