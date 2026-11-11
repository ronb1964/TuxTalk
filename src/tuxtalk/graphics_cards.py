"""The graphics cards the speech model can run on, and which one the user
picked, for computers with more than one (Preferences, Graphics card).

The setting names a card rather than numbering it, so it still means the same
card after a driver update renumbers them. Two cards with the same name can
only be told apart by where they come in a list, so the second is "<name> #2":
each engine counts in its own order, and with identical cards CUDA's #2 may be
Vulkan's #1. Identical cards do the same work, so that only matters for how
busy each one is. Each engine has its own list: CUDA's from nvidia-smi, sorted
by PCI bus, the order cuda_preload has CUDA count in once a card is picked,
and Vulkan's from the list the graphics engine prints when it starts
(whisper_vulkan.list_devices).
"""
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass

from .logger import setup_logger

logger = setup_logger(__name__)

# The setting's value when nobody picked a card: each engine's own choice.
AUTOMATIC = ""

# The Vulkan driver's name in brackets at the end of a card's name, as in
# "NVIDIA GeForce RTX 4070 SUPER (NVIDIA)": without it the card has the name
# nvidia-smi gives it, so a pick holds when the Device moves to CUDA.
_DRIVER_SUFFIX = re.compile(r"\s*\([^()]*\)$")

_MIB_PER_GB = 1024


@dataclass(frozen=True)
class Card:
    """One graphics card as an engine sees it."""
    index: int      # the engine's own number for it
    key: str        # what the setting saves for it
    label: str      # what the Graphics card dropdown shows


def _keyed(entries):
    """Cards for [(index, name, detail)], in that order: a name that repeats
    gets " #2", " #3" and so on, and *detail*, when there is one, goes in
    brackets after it in the label."""
    seen = Counter()
    cards = []
    for index, name, detail in entries:
        seen[name] += 1
        key = name if seen[name] == 1 else f"{name} #{seen[name]}"
        cards.append(Card(index, key, f"{key} ({detail})" if detail else key))
    return cards


def _memory(free_mib, total_mib):
    """"9.3 of 95.6 GB free", or "" when nvidia-smi couldn't read it ([N/A])."""
    try:
        free, total = float(free_mib) / _MIB_PER_GB, float(total_mib) / _MIB_PER_GB
    except ValueError:
        return ""
    return f"{free:.1f} of {total:.1f} GB free"


def parse_nvidia_smi(output):
    """The cards in nvidia-smi's pci.bus_id,name,memory.free,memory.total
    CSV, sorted and numbered by PCI bus as CUDA counts them in that order."""
    rows = []
    for line in output.splitlines():
        bus, _, rest = line.partition(",")
        parts = [p.strip() for p in rest.rsplit(",", 2)]
        if len(parts) == 3 and parts[0]:
            rows.append((bus.strip().lower(), parts[0], _memory(parts[1], parts[2])))
    rows.sort()
    return _keyed([(number, name, memory) for number, (_bus, name, memory) in enumerate(rows)])


def cuda_cards(run=subprocess.run):
    """The NVIDIA cards CUDA can use, [] when nvidia-smi is missing or fails."""
    from .cuda_helper import NVIDIA_SMI_TIMEOUT
    try:
        result = run(["nvidia-smi", "--query-gpu=pci.bus_id,name,memory.free,memory.total",
                      "--format=csv,noheader,nounits"],
                     capture_output=True, text=True, errors="replace",
                     timeout=NVIDIA_SMI_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.info(f"Could not list the NVIDIA cards: {e}")
        return []
    if result.returncode != 0:
        logger.info(f"nvidia-smi could not list the cards: {result.stderr.strip()}")
        return []
    return parse_nvidia_smi(result.stdout)


def from_vulkan(devices):
    """The cards among whisper_vulkan's [(index, name, is_nvidia, built_in)].
    Graphics built into the processor isn't one: it's no faster than the
    processor, so offering it would only be a way to slow dictation down."""
    return _keyed([(index, _DRIVER_SUFFIX.sub("", name), "")
                   for index, name, _nvidia, built_in in devices if not built_in])


def vulkan_cards():
    """The cards the Vulkan engine can use, [] when it isn't downloaded."""
    from . import whisper_vulkan
    return from_vulkan(whisper_vulkan.list_devices())


def index_for(cards, key):
    """The engine's number for the card saved as *key*, or None: nothing was
    picked, or that card isn't in the computer any more."""
    if key == AUTOMATIC:
        return None
    for card in cards:
        if card.key == key:
            return card.index
    logger.info(f"The graphics card chosen in Preferences ({key}) isn't there; "
                "using the automatic choice")
    return None


def worth_choosing(cards):
    """Whether to offer the choice: only with two cards or more, so the
    computers that have one get no new setting."""
    return len(cards) > 1


def cuda_index(key, run=subprocess.run):
    """CUDA's number for the card saved as *key*: 0, CUDA's first, when
    nothing is picked or that card is gone. The number only means that card
    while CUDA counts by PCI bus (cuda_preload); a CUDA_DEVICE_ORDER or
    CUDA_VISIBLE_DEVICES the user set counts another way, so then they decide
    and the pick is left out."""
    if key == AUTOMATIC:
        return 0
    if os.environ.get("CUDA_VISIBLE_DEVICES") or \
            os.environ.get("CUDA_DEVICE_ORDER") != "PCI_BUS_ID":
        logger.info("CUDA_VISIBLE_DEVICES or CUDA_DEVICE_ORDER chooses the card here, "
                    "not Preferences")
        return 0
    picked = index_for(cuda_cards(run), key)
    return 0 if picked is None else picked
