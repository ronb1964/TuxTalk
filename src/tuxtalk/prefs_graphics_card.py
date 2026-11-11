"""The Graphics card row of the speech settings: which card the speech model
runs on, shown only when the chosen Device sees more than one card
(graphics_cards.py). Everyone else never sees it."""
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from . import graphics_cards
from .logger import setup_logger
from .prefs_tab import Tab, dropdown, tip_label

logger = setup_logger(__name__)

# The devices that run on a graphics card, and so have cards to choose from.
_GPU_DEVICES = ("cuda", "vulkan")


def cards_for(device):
    """The cards *device* ("cuda" or "vulkan") can run on."""
    if device == "cuda":
        return graphics_cards.cuda_cards()
    return graphics_cards.vulkan_cards()


def in_background(work, done):
    """Run *work* on a thread and hand its result to *done* on GTK's thread.
    Listing the cards runs nvidia-smi or the graphics engine, which a slow
    driver can hold up for seconds; Preferences must not freeze meanwhile."""
    def run():
        try:
            cards = work()
        except Exception as e:      # any failure: no choice offered, as with no cards
            logger.warning(f"Could not list the graphics cards: {e}")
            cards = []
        GLib.idle_add(done, cards)
    threading.Thread(target=run, daemon=True).start()


class GraphicsCardRow(Tab):
    """The label and dropdown. *list_cards* and *run* are cards_for and
    in_background, or the tests' stand-ins."""

    def __init__(self, win, list_cards=None, run=None):
        """The row for Preferences window *win*, hidden until build and a
        device with more than one card show it."""
        super().__init__(win)
        self._list_cards = list_cards or cards_for
        self._run = run or in_background
        # Each device's cards, kept for the window: listing runs a program.
        # An empty answer isn't kept, so the Vulkan engine downloaded since
        # gets asked.
        self._cards = {}
        # Set while the entries are rebuilt: the "changed" then is no pick.
        self._filling = False
        # The device the row is set up for: a Model or Language change brings
        # follow_device here too, and that needs no rebuild.
        self._shown_for = None
        self._closed = False
        # Bumped by each listing and by forget: only the latest listing's
        # answer counts, as an older one can come back after it.
        self._asking = 0

    def build(self, grid, row, device):
        """The row at *row* of *grid*, shown if *device* has cards to choose from."""
        self.label = tip_label(
            "Graphics card",
            "The card the speech model runs on, for computers with more than one.\n"
            "Automatic lets TuxTalk choose, as it always has. Applying a change\n"
            "restarts dictation.")
        self.combo = dropdown(Gtk.ComboBoxText())
        self.label.set_mnemonic_widget(self.combo)
        for widget in (self.label, self.combo):
            widget.set_no_show_all(True)    # shown only by _show
        self.combo.connect("changed", self._on_changed)
        self.combo.connect("destroy", lambda _w: setattr(self, "_closed", True))
        grid.attach(self.label, 0, row, 1, 1)
        grid.attach(self.combo, 1, row, 1, 1)
        self.follow_device(device)

    def follow_device(self, device):
        """Show the row for *device* once its cards are known, if it sees
        more than one. Hidden until then, and for the processor."""
        if device == self._shown_for:
            return
        self._shown_for = device
        self._asking += 1           # an answer still on its way is out of date now
        asked = self._asking
        self._show([])
        if device not in _GPU_DEVICES:
            return
        if device in self._cards:
            self._show(self._cards[device])
            return

        def listed(cards):
            """On GTK's thread: keep and show the list, unless another
            listing or the window's closing has made it out of date."""
            if asked == self._asking and not self._closed:
                if cards:
                    self._cards[device] = cards
                self._show(cards)
            return False            # once (GLib.idle_add)
        self._run(lambda: self._list_cards(device), listed)

    def forget(self, device):
        """Ask *device*'s cards again at the next follow_device: its engine
        has just been set up."""
        self._cards.pop(device, None)
        self._shown_for = None
        self._asking += 1

    def _show(self, cards):
        """Fill and show the row for *cards*, or hide it for fewer than two."""
        shown = graphics_cards.worth_choosing(cards)
        if shown:
            self._fill(cards)
        self.label.set_visible(shown)
        self.combo.set_visible(shown)

    def _fill(self, cards):
        """The entries: Automatic, then each card. A saved card that isn't
        there any more stays listed and selected, so opening Preferences
        doesn't quietly change the setting."""
        saved = self.config.get("graphics_card", graphics_cards.AUTOMATIC)
        self._filling = True
        try:
            self.combo.remove_all()
            self.combo.append(graphics_cards.AUTOMATIC, "Automatic")
            for card in cards:
                self.combo.append(card.key, card.label)
            if saved not in {graphics_cards.AUTOMATIC, *(card.key for card in cards)}:
                self.combo.append(saved, f"{saved} (not found, Automatic is used)")
            self.combo.set_active_id(saved)
        finally:
            self._filling = False

    def _on_changed(self, combo):
        """A pick is saved with the rest on Apply or OK."""
        if not self._filling:
            self.update_config("graphics_card", combo.get_active_id())
