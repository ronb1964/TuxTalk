"""
The dialogs for setting up the Vulkan graphics engine (whisper_vulkan.py).

Shared by Preferences (the Device dropdown, the Large-v3 prompt) and the tray
(the Recommended preset on a graphics card), so every route to Vulkan asks the
same questions, downloads the same way, and runs the same speed check.
"""
import functools
import threading

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
from .ui_style import message_dialog, show_message

from . import whisper_vulkan as wv
from .cuda_helper import DOWNLOAD_SIZE_TEXT as CUDA_SIZE_TEXT
from .logger import setup_logger
from .recommend import model_title

logger = setup_logger(__name__)

LIGHT, FULL = "vulkan", "cuda"


# These boxes also open from the tray, with no TuxTalk window to stay over.
message = functools.partial(show_message, keep_above=True)


def choose_light_or_full(parent, model):
    """NVIDIA without CUDA: Light (Vulkan) or Full (CUDA) for *model*?
    Returns LIGHT, FULL, or None for Cancel."""
    name, vulkan_size = model_title(model), wv.MODEL_FILES[model][1]
    dialog = message_dialog(transient_for=parent, modal=True,
                               message_type=Gtk.MessageType.QUESTION,
                               buttons=Gtk.ButtonsType.NONE,
                               text=f"{name} needs your graphics card")
    dialog.format_secondary_text(
        "There are two ways to set it up:\n\n"
        f"  • Light (Vulkan): a {wv.ENGINE_SIZE_TEXT} graphics engine plus\n"
        f"    {name} in its format ({vulkan_size}).\n"
        "    About as fast as CUDA in our tests.\n\n"
        f"  • Full (CUDA): NVIDIA's CUDA libraries ({CUDA_SIZE_TEXT}) plus the model.\n"
        "    TuxTalk's long-standing NVIDIA setup.\n\n"
        "Either is a one-time download.")
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Light (Vulkan)", Gtk.ResponseType.APPLY)
    dialog.add_button("Full (CUDA)", Gtk.ResponseType.YES)
    dialog.set_keep_above(True)
    response = dialog.run()
    dialog.destroy()
    return {Gtk.ResponseType.APPLY: LIGHT, Gtk.ResponseType.YES: FULL}.get(response)


def ensure_files(parent, model, confirm):
    """Make sure the engine and *model*'s whisper.cpp file are downloaded.
    True when both are there."""
    from .download_progress_dialog import DownloadTask, UnifiedDownloadDialog
    name = model_title(model)
    tasks, needed = [], []
    # Engine build 1 (0.12.0 to 0.13.1) can't run Parakeet, so it's fetched
    # again, as a superset, the first time someone sets Parakeet up.
    if not wv.is_engine_installed(model):
        tasks.append(DownloadTask("Graphics engine", wv.ENGINE_SIZE_TEXT,
                                  wv.make_engine_download_func(model)))
        needed.append(f"the graphics engine ({wv.ENGINE_SIZE_TEXT})")
    if wv.model_path(model) is None:
        tasks.append(DownloadTask("Speech model", wv.MODEL_FILES[model][1],
                                  wv.make_model_download_func(model)))
        needed.append(f"the {name} model in the format it uses ({wv.MODEL_FILES[model][1]})")
    if not tasks:
        return True
    if confirm:
        answer = message(
            parent, Gtk.MessageType.QUESTION, "Set up your graphics card with Vulkan?",
            f"TuxTalk needs a one-time download: {' and '.join(needed)}.\n\n"
            "Afterwards TuxTalk checks whether your graphics chip is really faster than "
            "your processor, and only uses it if it is. Everything runs on this computer.",
            buttons=Gtk.ButtonsType.OK_CANCEL)
        if answer != Gtk.ResponseType.OK:
            return False
    dialog = UnifiedDownloadDialog(
        parent=parent, title="Setting Up Vulkan",
        description="One-time download. Everything runs on this computer.")
    for task in tasks:
        dialog.add_task(task)
    results = dialog.run()
    if all(r.get("success") for r in results.values()) and wv.is_installed(model):
        return True
    if not any(r.get("cancelled") for r in results.values()):
        message(parent, Gtk.MessageType.ERROR, "The download did not finish",
                "Check your internet connection and try again.")
    return False


# How long the speed check runs before the spinner offers a way out. A hung
# graphics driver can keep it going for many minutes (each run has a 5-minute
# timeout), and a modal window with no way to close it traps the user.
_STOP_OFFER_MS = 90_000


def _wait_dialog(parent, work):
    """Run *work* on a thread behind a modal "Checking Speed" spinner.

    It can't be closed at first, so a stray Escape doesn't cut the check
    short. Once it runs long, a Stop button appears and Escape works too; the
    caller then finds no result and keeps the processor.
    """
    waiting = Gtk.Dialog(title="Checking Speed", transient_for=parent, modal=True)
    waiting.set_deletable(False)
    waiting.set_keep_above(True)
    box = waiting.get_content_area()
    box.set_spacing(12)
    box.set_border_width(18)
    row = Gtk.Box(spacing=12)
    spinner = Gtk.Spinner()
    spinner.start()
    row.pack_start(spinner, False, False, 0)
    label = Gtk.Label(label="Checking how fast your graphics chip is compared to your\n"
                            "processor. This can take up to a minute.", xalign=0)
    row.pack_start(label, False, False, 0)
    box.add(row)
    waiting.show_all()
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    state = {"closed": False, "stoppable": False}

    def poll():
        if state["closed"]:
            return False
        if thread.is_alive():
            return True
        waiting.response(Gtk.ResponseType.OK)
        return False

    def offer_stop():
        if not state["closed"] and thread.is_alive():
            state["stoppable"] = True
            label.set_text("This is taking longer than it should. You can stop it\n"
                           "and keep using the processor.")
            waiting.add_button("Stop", Gtk.ResponseType.CANCEL).show()
        return False

    # set_deletable(False) only hides the title-bar X: Escape and Alt+F4 still
    # send delete-event, which would end run() while the work goes on. It is
    # refused until the work is done or the Stop button has been offered.
    waiting.connect("delete-event", lambda *_: thread.is_alive() and not state["stoppable"])
    GLib.timeout_add(200, poll)
    GLib.timeout_add(_STOP_OFFER_MS, offer_stop)
    waiting.run()
    state["closed"] = True
    waiting.destroy()


def _remember_speed_result(processor_won):
    """Save whether the processor won (vulkan_slower), so recommend.py stops
    proposing a graphics chip that lost."""
    try:
        from .config import load_config, save_config
        cfg = load_config()
        if cfg.vulkan_slower != processor_won:
            cfg.vulkan_slower = processor_won
            save_config(cfg)
    except Exception:
        pass


def run_speed_check(parent, model):
    """Time the graphics chip against the processor on a short sample, tell
    the user the result, and return True if the graphics chip should be used."""
    result = {}

    def work():
        try:
            device = wv.find_device()
            if device is None:
                result["error"] = "TuxTalk couldn't find a graphics chip it can use."
            else:
                result["times"] = wv.speed_check(model, device)
        except Exception as e:
            result["error"] = f"The speed check didn't work: {e}"

    _wait_dialog(parent, work)

    times = result.get("times")
    if times is None:
        message(parent, Gtk.MessageType.WARNING, "Keeping the processor",
                result.get("error") or "The speed check was interrupted.")
        return False
    gpu, cpu = times
    if wv.graphics_is_worth_it(gpu, cpu):
        _remember_speed_result(False)
        message(parent, Gtk.MessageType.INFO, "Your graphics chip is faster",
                f"It transcribed the test in {gpu:.1f} seconds, your processor in {cpu:.1f}, "
                f"about {cpu / gpu:.0f} times faster. TuxTalk will use your graphics chip.")
        return True
    _remember_speed_result(True)
    message(parent, Gtk.MessageType.INFO, "Your processor is faster here",
            f"Your processor transcribed the test in {cpu:.1f} seconds and your graphics chip "
            f"in {gpu:.1f}. The graphics built into some processors is too small to help, so "
            "TuxTalk will keep using the processor.")
    return False


def set_up(parent, model, confirm=True):
    """Download and speed-check the Vulkan engine for *model*. True if TuxTalk
    should use the graphics chip."""
    if not wv.supports_model(model):
        # English-only and older Whisper variants (small.en, large-v2, ...) have
        # no file in whisper.cpp's format here.
        message(parent, Gtk.MessageType.INFO, "Pick another model first",
                f"{model_title(model)} can't run on your graphics chip through Vulkan.\n\n"
                "To use your graphics chip, choose Parakeet or a Whisper model (Small, Medium "
                "or Large-v3) and then choose Vulkan again.")
        return False
    if not ensure_files(parent, model, confirm):
        return False
    return run_speed_check(parent, model)
