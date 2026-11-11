"""Choosing the graphics card on a computer with more than one (graphics_cards.py)."""
import os
import subprocess

import pytest

from tuxtalk import graphics_cards as gc

# nvidia-smi --query-gpu=pci.bus_id,name,memory.free,memory.total
#   --format=csv,noheader,nounits, on a workstation like the one in Discussion
# #32: two pairs of identical cards, not listed in slot order.
FIVE_CARDS = """\
00000000:02:00.0, NVIDIA RTX PRO 6000 Blackwell Workstation Edition, 9523, 97887
00000000:01:00.0, NVIDIA GeForce RTX 5090, 31641, 32607
00000000:03:00.0, NVIDIA GeForce RTX 5090, 31641, 32607
00000000:41:00.0, NVIDIA GeForce RTX 5070 Ti, 5632, 16303
00000000:42:00.0, NVIDIA GeForce RTX 5070 Ti, 13824, 16303
"""
ONE_CARD = "00000000:01:00.0, NVIDIA GeForce RTX 4070 SUPER, 10000, 12282\n"


def test_cuda_cards_are_numbered_by_slot_as_cuda_counts_them():
    cards = gc.parse_nvidia_smi(FIVE_CARDS)
    assert [(c.index, c.key) for c in cards][:2] == [
        (0, "NVIDIA GeForce RTX 5090"),
        (1, "NVIDIA RTX PRO 6000 Blackwell Workstation Edition"),
    ]


def test_cards_with_the_same_name_can_still_be_told_apart():
    cards = gc.parse_nvidia_smi(FIVE_CARDS)
    assert [c.key for c in cards] == [
        "NVIDIA GeForce RTX 5090",
        "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
        "NVIDIA GeForce RTX 5090 #2",
        "NVIDIA GeForce RTX 5070 Ti",
        "NVIDIA GeForce RTX 5070 Ti #2",
    ]
    assert [c.index for c in cards] == [0, 1, 2, 3, 4]


def test_each_cuda_card_shows_how_much_memory_is_free():
    cards = gc.parse_nvidia_smi(FIVE_CARDS)
    assert cards[1].label == ("NVIDIA RTX PRO 6000 Blackwell Workstation Edition "
                              "(9.3 of 95.6 GB free)")
    assert cards[4].label == "NVIDIA GeForce RTX 5070 Ti #2 (13.5 of 15.9 GB free)"


def test_a_card_whose_memory_nvidia_smi_cant_read_is_still_listed():
    """nvidia-smi prints [N/A] for some cards and in some containers."""
    cards = gc.parse_nvidia_smi("00000000:01:00.0, NVIDIA GeForce GTX 1050, [N/A], [N/A]\n")
    assert [(c.key, c.label) for c in cards] == [("NVIDIA GeForce GTX 1050",
                                                 "NVIDIA GeForce GTX 1050")]


def test_no_nvidia_smi_means_no_cuda_cards():
    def missing(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    def fails(*args, **kwargs):
        return subprocess.CompletedProcess(args, 9, stdout="", stderr="no driver")

    assert gc.cuda_cards(run=missing) == []
    assert gc.cuda_cards(run=fails) == []


def test_vulkan_lists_only_cards_not_the_chip_built_into_the_processor():
    """A Ryzen's or Intel's built-in graphics is no faster than the processor
    itself, so offering it would only be a way to slow dictation down."""
    devices = [(0, "NVIDIA GeForce RTX 4070 SUPER (NVIDIA)", True, False),
               (1, "AMD Ryzen 7 7800X3D 8-Core Processor (RADV RAPHAEL_MENDOCINO) (radv)",
                False, True),
               (2, "AMD Radeon RX 7900 XTX (RADV NAVI31) (radv)", False, False)]
    cards = gc.from_vulkan(devices)
    assert [(c.index, c.key) for c in cards] == [
        (0, "NVIDIA GeForce RTX 4070 SUPER"),
        (2, "AMD Radeon RX 7900 XTX (RADV NAVI31)"),
    ]


def test_an_nvidia_card_has_the_same_name_under_cuda_and_vulkan():
    """So a card picked with the Device on CUDA stays picked on Vulkan."""
    cuda = gc.parse_nvidia_smi(ONE_CARD)
    vulkan = gc.from_vulkan([(0, "NVIDIA GeForce RTX 4070 SUPER (NVIDIA)", True, False)])
    assert cuda[0].key == vulkan[0].key


def test_the_chosen_card_is_found_by_name_and_automatic_finds_none():
    cards = gc.parse_nvidia_smi(FIVE_CARDS)
    assert gc.index_for(cards, "NVIDIA GeForce RTX 5070 Ti #2") == 4
    assert gc.index_for(cards, gc.AUTOMATIC) is None


def test_a_card_that_was_taken_out_finds_none_so_the_automatic_choice_runs():
    cards = gc.parse_nvidia_smi(FIVE_CARDS)
    assert gc.index_for(cards, "NVIDIA GeForce RTX 3090") is None


def test_the_picker_only_shows_with_more_than_one_card():
    """Most computers have one card: no new setting for them (keep it simple)."""
    one = gc.parse_nvidia_smi(ONE_CARD)
    assert not gc.worth_choosing(one)
    assert gc.worth_choosing(gc.parse_nvidia_smi(FIVE_CARDS))


def _smi(output):
    """A stand-in for running nvidia-smi that prints *output*."""
    return lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=output, stderr="")


def test_cuda_uses_the_picked_cards_number(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    assert gc.cuda_index("NVIDIA GeForce RTX 5070 Ti #2", run=_smi(FIVE_CARDS)) == 4
    assert gc.cuda_index(gc.AUTOMATIC, run=_smi(FIVE_CARDS)) == 0
    assert gc.cuda_index("NVIDIA GeForce RTX 3090", run=_smi(FIVE_CARDS)) == 0


@pytest.mark.parametrize("variable,value", [("CUDA_VISIBLE_DEVICES", "3"),
                                            ("CUDA_DEVICE_ORDER", "FASTEST_FIRST")])
def test_cuda_settings_the_user_made_win_over_the_pick(monkeypatch, variable, value):
    """Either one numbers CUDA's cards another way than nvidia-smi's slot
    order: using the pick then would load on the wrong card."""
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setenv(variable, value)
    assert gc.cuda_index("NVIDIA GeForce RTX 5070 Ti #2", run=_smi(FIVE_CARDS)) == 0


@pytest.mark.parametrize("picked,order", [("NVIDIA GeForce RTX 5090 #2", "PCI_BUS_ID"),
                                          ("", None),
                                          ("NVIDIA GeForce RTX 3090", None)])
def test_cuda_counts_by_slot_only_with_a_picked_card_thats_there(monkeypatch, picked, order):
    """Automatic, or a picked card since taken out, keeps CUDA's own order,
    fastest card first, as it always had: counting by slot moved dictation to
    whatever card sits in the first slot."""
    from tuxtalk import config, cuda_preload
    monkeypatch.setattr(os, "environ", {})      # what the call sets stays in this test
    monkeypatch.setattr(config, "load_config",
                        lambda: config.Settings(device="cuda", graphics_card=picked))
    monkeypatch.setattr(gc, "cuda_cards", lambda: gc.parse_nvidia_smi(FIVE_CARDS))
    cuda_preload._order_cards_by_pci_bus()
    assert os.environ.get("CUDA_DEVICE_ORDER") == order


def test_an_order_the_user_set_is_kept(monkeypatch):
    from tuxtalk import config, cuda_preload
    monkeypatch.setattr(os, "environ", {"CUDA_DEVICE_ORDER": "FASTEST_FIRST"})
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(
        device="cuda", graphics_card="NVIDIA GeForce RTX 5090 #2"))
    monkeypatch.setattr(gc, "cuda_cards", lambda: gc.parse_nvidia_smi(FIVE_CARDS))
    cuda_preload._order_cards_by_pci_bus()
    assert os.environ["CUDA_DEVICE_ORDER"] == "FASTEST_FIRST"


def test_cuda_settings_an_earlier_tuxtalk_set_are_decided_afresh(monkeypatch):
    """The service starts Preferences, which can start the service again:
    the new one inherited the old pick's order and took it for the user's,
    and a card hidden before CUDA was downloaded stayed hidden after."""
    from tuxtalk import cuda_preload, launch_env
    monkeypatch.setattr(os, "environ", {
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID", launch_env.CUDA_ORDER_SET_BY_TUXTALK: "1",
        "CUDA_VISIBLE_DEVICES": "", launch_env.CUDA_HIDDEN_BY_TUXTALK: "1"})
    cuda_preload._forget_what_tuxtalk_set()
    assert os.environ == {}


def test_cuda_settings_the_user_made_are_never_dropped(monkeypatch):
    from tuxtalk import cuda_preload
    users = {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": "1"}
    monkeypatch.setattr(os, "environ", dict(users))
    cuda_preload._forget_what_tuxtalk_set()
    assert os.environ == users


def test_programs_tuxtalk_starts_keep_cudas_own_order():
    """Blender or a game opened by voice mustn't get TuxTalk's slot order."""
    from tuxtalk import launch_env
    ours = launch_env.clean({"CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                             launch_env.CUDA_ORDER_SET_BY_TUXTALK: "1", "PATH": "/usr/bin"},
                            venv="")
    users = launch_env.clean({"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "PATH": "/usr/bin"}, venv="")
    assert "CUDA_DEVICE_ORDER" not in ours
    assert users["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"
