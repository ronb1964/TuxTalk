"""Model pickers offer exactly the models that can be downloaded.

A model can only be offered if there is somewhere to download it from: a
Whisper repository in MODEL_REPOS, or TuxTalk's own release for Parakeet.
VALID_MODELS fixes the display order and is what every model picker must
build from, so a separate hardcoded list can't drift and hide a model.
"""


def test_offered_models_matches_what_can_actually_be_downloaded():
    from tuxtalk.settings_schema import VALID_MODELS
    from tuxtalk.model_helper import MODEL_REPOS
    from tuxtalk.parakeet_engine import PARAKEET_MODEL

    downloadable = set(MODEL_REPOS) | {PARAKEET_MODEL}
    assert set(VALID_MODELS) == downloadable, (
        f"VALID_MODELS {sorted(VALID_MODELS)} does not match the models that "
        f"can be downloaded {sorted(downloadable)}. Offering a model with no repo "
        f"gives the user a choice that fails at download time; omitting one hides "
        f"a model that works."
    )


def test_offered_models_has_no_duplicates():
    from tuxtalk.settings_schema import VALID_MODELS

    assert len(VALID_MODELS) == len(set(VALID_MODELS)), (
        f"VALID_MODELS contains duplicates: {VALID_MODELS}"
    )


def test_every_model_picker_builds_from_the_shared_list(prefs_window):
    """No picker may keep its own hardcoded list, or the lists drift apart:
    that is how 'base' went missing from the first-run setup screen. (First
    run has no full picker: its card offers the models recommend.py picks.)"""
    from tuxtalk.settings_schema import VALID_MODELS
    offered = [row[0] for row in prefs_window().general_tab.speech.model_combo.get_model()]
    assert offered == list(VALID_MODELS)


def test_the_setup_line_names_the_model_and_where_it_runs():
    from tuxtalk.recommend import Hardware, setup_title
    card = Hardware("NVIDIA GeForce RTX 4070", True)
    assert setup_title("parakeet-v3", "vulkan", card) == "Parakeet on your NVIDIA GeForce RTX 4070"
    assert setup_title("small", "cpu", card) == "Whisper Small on this computer's processor"
    assert setup_title("medium", "cuda", card) == "Whisper Medium on your NVIDIA GeForce RTX 4070 (CUDA)"
    # Parakeet can't use CUDA: it runs on the processor, and the line says so.
    assert setup_title("parakeet-v3", "cuda", card) == "Parakeet on this computer's processor"
