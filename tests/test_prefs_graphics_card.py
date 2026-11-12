"""The Graphics card row in Preferences (prefs_graphics_card.py)."""
from types import SimpleNamespace

import gi
import pytest
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from tuxtalk.graphics_cards import parse_nvidia_smi
from tuxtalk.prefs_graphics_card import GraphicsCardRow

TWO_CARDS = parse_nvidia_smi("00000000:01:00.0, NVIDIA GeForce RTX 5090, 31641, 32607\n"
                             "00000000:02:00.0, NVIDIA GeForce RTX 5070 Ti, 13824, 16303\n")
ONE_CARD = parse_nvidia_smi("00000000:01:00.0, NVIDIA GeForce RTX 4070 SUPER, 10000, 12282\n")


def _at_once(work, done):
    """prefs_graphics_card.in_background without the thread."""
    done(work())


def _row(config, cards_by_device, run=_at_once):
    """The row in a grid, for a window holding *config*, with the cards each
    device lists; returns (row, asked) where asked counts the listings."""
    asked = []

    def list_cards(device):
        asked.append(device)
        return cards_by_device.get(device, [])

    # The grid is kept, as Preferences keeps it: GTK destroys the widgets of
    # a grid that's thrown away.
    win = SimpleNamespace(config=config, grid=Gtk.Grid())
    win.update_config = lambda key, value: config.__setitem__(key, value)
    row = GraphicsCardRow(win, list_cards=list_cards, run=run)
    row.build(win.grid, 0, config.get("device", "cpu"))
    return row, asked


def _entries(combo):
    return [(entry[1], entry[0]) for entry in combo.get_model()]      # (id, text)


def test_one_card_shows_no_new_setting():
    row, _ = _row({"device": "cuda", "graphics_card": ""}, {"cuda": ONE_CARD})
    assert not row.combo.get_visible()
    assert not row.label.get_visible()


def test_two_cards_show_the_choice_with_automatic_first():
    row, _ = _row({"device": "cuda", "graphics_card": ""}, {"cuda": TWO_CARDS})
    assert row.combo.get_visible()
    assert _entries(row.combo) == [
        ("", "Automatic"),
        ("NVIDIA GeForce RTX 5090", "NVIDIA GeForce RTX 5090 (30.9 of 31.8 GB free)"),
        ("NVIDIA GeForce RTX 5070 Ti", "NVIDIA GeForce RTX 5070 Ti (13.5 of 15.9 GB free)"),
    ]
    assert row.combo.get_active_id() == ""


def test_picking_a_card_changes_the_setting():
    config = {"device": "cuda", "graphics_card": ""}
    row, _ = _row(config, {"cuda": TWO_CARDS})
    row.combo.set_active_id("NVIDIA GeForce RTX 5070 Ti")
    assert config["graphics_card"] == "NVIDIA GeForce RTX 5070 Ti"


def test_opening_preferences_shows_the_saved_card_and_changes_nothing():
    config = {"device": "cuda", "graphics_card": "NVIDIA GeForce RTX 5070 Ti"}
    row, _ = _row(config, {"cuda": TWO_CARDS})
    assert row.combo.get_active_id() == "NVIDIA GeForce RTX 5070 Ti"
    assert config["graphics_card"] == "NVIDIA GeForce RTX 5070 Ti"


@pytest.mark.parametrize("saved", ["NVIDIA GeForce RTX 3090",
                                   "AMD Radeon RX 7900 XTX (RADV NAVI31)"])
def test_a_saved_card_the_device_cant_use_stays_listed_instead_of_being_dropped(saved):
    """Taken out, or an AMD card picked under Vulkan with the Device now on
    CUDA: "not found" was untrue for the second, which is still in the computer."""
    config = {"device": "cuda", "graphics_card": saved}
    row, _ = _row(config, {"cuda": TWO_CARDS})
    assert row.combo.get_active_id() == saved
    assert _entries(row.combo)[-1] == (saved, f"{saved} (not usable here, Automatic is used)")
    assert config["graphics_card"] == saved


def test_the_processor_has_no_cards_and_nothing_is_asked():
    row, asked = _row({"device": "cpu", "graphics_card": ""}, {"cuda": TWO_CARDS})
    assert not row.combo.get_visible()
    assert asked == []


def test_switching_device_shows_that_devices_cards_and_asks_each_once():
    config = {"device": "cpu", "graphics_card": ""}
    row, asked = _row(config, {"cuda": TWO_CARDS, "vulkan": ONE_CARD})
    row.follow_device("cuda")
    assert row.combo.get_visible()
    row.follow_device("vulkan")
    assert not row.combo.get_visible()
    row.follow_device("cuda")
    row.follow_device("cuda")
    assert asked == ["cuda", "vulkan"]
    assert config["graphics_card"] == ""        # refilling isn't a pick


def test_cards_listed_after_the_device_changed_again_are_not_shown():
    """Listing runs in the background: a slow answer for CUDA mustn't show
    CUDA's cards once the Device has moved on to the processor."""
    waiting = []
    row, _ = _row({"device": "cuda", "graphics_card": ""}, {"cuda": TWO_CARDS},
                  run=lambda work, done: waiting.append((work, done)))
    assert not row.combo.get_visible()          # nothing until the answer comes
    row.follow_device("cpu")
    work, done = waiting.pop()
    done(work())
    assert not row.combo.get_visible()


def test_no_cards_yet_is_asked_again_once_the_engine_is_set_up():
    """Before Vulkan's engine is downloaded it lists nothing; after its
    setup the cards show without reopening Preferences."""
    listings = {"vulkan": []}
    row, _ = _row({"device": "vulkan", "graphics_card": ""}, listings)
    assert not row.combo.get_visible()
    listings["vulkan"] = TWO_CARDS
    row.forget("vulkan")
    row.follow_device("vulkan")
    assert row.combo.get_visible()


def test_parakeet_on_cuda_gets_no_card_choice():
    """It runs on the processor there, so a card choice would do nothing."""
    from tuxtalk.prefs_speech import SpeechSection
    for model, device, lists in (("parakeet-v3", "cuda", "cpu"), ("large-v3", "cuda", "cuda"),
                                 ("parakeet-v3", "vulkan", "vulkan")):
        section = SimpleNamespace(device_combo=SimpleNamespace(get_active_id=lambda d=device: d),
                                  selected_model=lambda m=model: m)
        assert SpeechSection._card_device(section) == lists


def test_an_older_answer_coming_back_last_doesnt_hide_the_newer_one():
    """Vulkan's engine set up while its first listing (none, no engine yet)
    was still running: the second listing's cards stay shown."""
    waiting = []
    row, _ = _row({"device": "vulkan", "graphics_card": ""},
                  {"vulkan": TWO_CARDS}, run=lambda work, done: waiting.append(done))
    row.forget("vulkan")
    row.follow_device("vulkan")
    first, second = waiting
    second(TWO_CARDS)
    first([])
    assert row.combo.get_visible()
