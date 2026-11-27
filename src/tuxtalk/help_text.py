"""Help's pages, filled in for this computer; help_dialog.py shows them,
each topic a card. The words are in help/<page>.txt, one file a page, so
Help can grow without this file growing: Pango markup with marks where
cards begin ("#" the title, "##" a feature, "###" a card; help_outline
reads them) and {placeholders} this module fills in (_values). Lines are
broken by hand at about 85 characters, and flow on to the card's width.

Help is sorted by what you're doing (chatting, writing, in a terminal,
hands-free, fixing mistakes, something wrong), not by feature, so it can be
opened with a situation in mind; Tips gathers the practical advice. Labs
features are explained where they're used, marked as Labs. Each section
starts with what to do, then what's optional, then what to try if it
doesn't work. Keys are named as the
settings have them ({hold}, {toggle}), never a fixed F8.

The text is format-aware: TuxTalk ships as a Flatpak, an AppImage, and
.deb/.rpm/AUR packages, and some instructions differ (hotkeys, typing method,
file paths, updates, troubleshooting). Rather than maintain separate help files,
this branches on how TuxTalk is installed: the same runtime detection
(FLATPAK_ID / get_install_type) that onboarding, Preferences, and the updater
use. Change it once, every format stays correct.
"""
import os

from gi.repository import GLib

from .ai_download import DOWNLOAD_SIZE_TEXT as AI_SIZE_TEXT
from .ai_download import MEMORY_TEXT as AI_MEMORY_TEXT
from .dictation_keys import CTRL_SPACE
from .cuda_download import DOWNLOAD_SIZE_TEXT as CUDA_SIZE_TEXT
from .whisper_vulkan import ENGINE_SIZE_TEXT

# Where the Help window's feedback link goes; the Setup Complete screen
# points to the same page.
FEEDBACK_URL = "https://github.com/ronb1964/TuxTalk/discussions"
ISSUES_URL = "https://github.com/ronb1964/TuxTalk/issues"
# A link in Help that opens Report a Problem instead of a web page.
REPORT_LINK = "tuxtalk:report"
# The section that opens with a Report a Problem button (help_dialog), and
# the button and the line beside it, which Help's search finds too.
PROBLEMS_TITLE = "Problems"
REPORT_BUTTON = "Report a Problem…"
REPORT_NOTE = ("Gathers what helps find a problem: your system, your settings and the end of "
               "TuxTalk's log. You see all of it first, then copy it, save it, or open a "
               "GitHub issue with it.")
# Help's Commands tab is drawn as cards (commands_cards): its intro here, the
# cards from commands_reference, then commands_examples().
COMMANDS_TITLE = "Commands"


def install_facts():
    """Format-specific strings the help text interpolates. One place to keep the
    Flatpak vs non-Flatpak differences straight."""
    is_flatpak = bool(os.environ.get("FLATPAK_ID"))
    from .config import APP_ID as app_id
    if is_flatpak:
        cfg_dir = f"~/.var/app/{app_id}/config/tuxtalk/"
        cache_dir = f"~/.var/app/{app_id}/cache/huggingface/"
    else:
        cfg_dir = "~/.config/tuxtalk/"
        cache_dir = "~/.cache/huggingface/"
    return {
        "is_flatpak": is_flatpak,
        "cfg_dir": cfg_dir,
        "cache_dir": cache_dir,
        "log_path": cfg_dir + "tuxtalk.log",
    }


def _keys(is_flatpak):
    """(hold, toggle, paste) as Help names them: the keys in the settings, so
    Help never says F8 to someone who picked F6. On the Flatpak the desktop
    assigns the two dictation keys and there is no Paste Last shortcut."""
    if is_flatpak:
        return "your Hold-to-talk key", "your Toggle key", ""
    from .config import load_config, saved_hold_key
    cfg = load_config()
    return (saved_hold_key(), cfg.toggle_hotkey or "F9",
            getattr(cfg, "paste_last_hotkey", ""))


def _no_hotkey_tips(is_flatpak, keys_where):
    """What to try when no dictation key works, as Problems lists it. It
    differs completely by format: on the Flatpak the desktop assigns the keys.
    *keys_where* says where the keys are set."""
    if is_flatpak:
        return (
            "• On the Flatpak your keys are assigned by the desktop, not by TuxTalk.\n"
            f"• Open {keys_where}\n  and make sure a key is set for both "
            "<b>Hold to talk</b> and <b>Toggle dictation</b>.\n"
            "• If the assign prompt never appeared on first run, choose Restart Service in the\n"
            "  TuxTalk menu and it will ask again.")
    return (
        "• Open <b>Preferences → Advanced → System check</b>. If Typing setup says something\n"
        "  needs fixing, click its button. You'll be asked for your password once. TuxTalk's\n"
        "  hotkey helper then reads the dictation keys, and nothing else, with no restart\n"
        "  needed.\n"
        "• On a system without systemd the helper can't be used, and TuxTalk joins you to\n"
        "  the \"input\" group instead. Then the computer needs a restart: logging out is\n"
        "  often not enough. Typing setup says when this applies.\n"
        "• Another app may use the same key: pick another in Preferences → General and try\n"
        "  it with Test Hotkeys.\n"
        "• On many laptops and small keyboards the F-keys work only with Fn held. Hold Fn\n"
        "  with your key, or pick Right Ctrl, Right Alt, Scroll Lock or Left Ctrl+Space in\n"
        "  Preferences → General: where a laptop has them, they need no Fn. Or pick Other key\n"
        "  or combination… there and press the keys you like, such as Alt+D.")


def _uninstall_text(is_flatpak):
    """Problems' Uninstalling card: Preferences' button, what it takes and
    keeps, and the terminal command for this install when TuxTalk won't open.
    The Flatpak is removed by Flatpak itself (uninstall.py turns it away)."""
    if is_flatpak:
        return ("Flatpak removes the Flatpak: run <tt>flatpak uninstall --delete-data\n"
                "io.github.ronb1964.TuxTalk</tt> in a terminal, or remove TuxTalk in your\n"
                "software center.")
    from .control import own_command
    command = GLib.markup_escape_text(own_command())
    return ("<b>Preferences → Updates &amp; Backup → Uninstall TuxTalk…</b> removes TuxTalk and\n"
            "everything it set up: the program, the hotkey helper and the typing permission,\n"
            "its own downloads (the graphics and AI engines, CUDA), start at login, the menu\n"
            "entry and the GNOME extension, and turns the desktop's accessibility off again if\n"
            "TuxTalk turned it on. It may ask for your password once. The speech and\n"
            "AI models sit in a download folder other apps share: they go only if you tick\n"
            "that box too. Your settings and Your Words stay unless you tick theirs, so\n"
            "installing again picks up where you left off.\n"
            f"If TuxTalk won't open, <tt>{command} --uninstall</tt>\n"
            "in a terminal does the same.")


def _key_choice_tip(is_flatpak, keys_where):
    """The tip on picking a dictation key without Fn, or anywhere on the
    Flatpak, whose keys are set in *keys_where* (the desktop's settings)."""
    if is_flatpak:
        return f"Pick a dictation key that's easy to reach in\n    {keys_where}."
    return ("Pick <b>Right Alt</b>, <b>Right Ctrl</b>, <b>Scroll Lock</b> or <b>Left "
            "Ctrl+Space</b> as your key in <b>Preferences →\n    General</b>: on a laptop "
            "they need no Fn, where it has them. Where Right Alt is AltGr,\n    the list "
            "says which characters you'd lose, like @ or €. Or pick <b>Other key or\n    "
            "combination…</b> and press the keys you want, such as <b>Alt+D</b>.")


def _clipboard_privacy(is_flatpak):
    """Privacy's line on the clipboard; none on the Flatpak, which never pastes."""
    if is_flatpak:
        return ""
    return ("\nPasting puts a dictation on the clipboard, where it stays unless <b>Put back\n"
            "what I'd copied</b> is on. It's marked so a clipboard history that honours the\n"
            "mark (KDE's Klipper, GPaste, CopyQ, most GNOME clipboard extensions) doesn't keep\n"
            "it. On GNOME with none of those, TuxTalk's extension sets it unmarked, so no\n"
            "window flashes. To keep it off the clipboard entirely, pick <b>Keystroke Typing\n"
            "(No Clipboard)</b> in Preferences → Advanced → Fine-tuning → Text Injection.\n"
            "Voice editing still copies the text you select to read it, then puts your\n"
            "clipboard back.")


def _ctrl_space_line(keys):
    """Terminals' note on what Left Ctrl+Space costs, empty unless the hold or
    toggle key (the first two of *keys*) is that key."""
    if CTRL_SPACE not in keys[:2]:
        return ""
    return (
        "\n  • With <b>Left Ctrl+Space</b> as your key, Ctrl+Space (and Right Ctrl+Space)\n"
        "    doesn't reach your apps while dictation is on, so code completion on it waits\n"
        "    until you turn dictation off. On a desktop where TuxTalk can't hold keys back\n"
        "    (anything but KDE, GNOME with the TuxTalk extension, COSMIC and X11), the app\n"
        "    gets Ctrl+Space as well.")


def tabs():
    """[(title, Pango markup)] for each of Help's sections, in order. For
    Commands it is the text over the cards, which help_dialog adds."""
    values = _values()
    return [(title, page(name).format(**values)) for title, name in _TABS]


def _mouse_tip(hold: str, is_flatpak: bool) -> str:
    """Tips' line on the mouse grid, with its line break; none on the
    Flatpak, which has no TuxTalk Labs to turn it on in."""
    if is_flatpak:
        return ""
    return (f"  • No mouse? Hold <b>{hold}</b>, say <b>mouse grid</b>, let go, then say a "
            "number and\n    <b>click</b> (Writing → The mouse).\n")


def _values():
    """What the sections' {placeholders} say on this computer."""
    f = install_facts()
    is_flatpak = f["is_flatpak"]
    keys = _keys(is_flatpak)
    hold, toggle, paste_key = (GLib.markup_escape_text(k) for k in keys)

    # --- format-aware phrasing --------------------------------------------------
    keys_where = ("your desktop's <b>System Settings → Keyboard → Shortcuts</b> "
                  "(find <b>TuxTalk</b> under Applications)" if is_flatpak
                  else "<b>Preferences → General</b>")

    # A desktop shortcut or script can run the toggle itself (control.py).
    shortcut_line = ""
    if not is_flatpak:
        from .control import shortcut_command
        shortcut_line = (
            "\n\n### Your own shortcut\nA desktop shortcut or script can run "
            f"<tt>{GLib.markup_escape_text(shortcut_command())}</tt> (or --start, --stop). "
            "It starts TuxTalk if needed and waits until the speech model is loaded.")

    if is_flatpak:
        typing_para = (
            "TuxTalk types straight into the focused app through your desktop's secure\n"
            "    input portal (libei): there's no typing setup to configure, and clipboard\n"
            "    paste isn't used.")
    else:
        typing_para = (
            "Still nothing in one particular app? <b>Preferences → Advanced → Fine-tuning →\n"
            "    Text Injection</b>: Auto pastes (it types on X11); try\n"
            "    <b>Keystroke Typing</b> or <b>Clipboard Paste</b> there. <b>Keystroke Typing\n"
            "    (No Clipboard)</b> keeps your dictation off the clipboard: a character your\n"
            "    keyboard layout can't type (an emoji) then stops that line, and Recent\n"
            "    Dictations has the whole dictation.")

    no_hotkey_block = _no_hotkey_tips(is_flatpak, keys_where)
    uninstall_text = _uninstall_text(is_flatpak)

    # KDE-on-Flatpak: the per-dictation "remote control" popup.
    kde_notice = ""
    if is_flatpak and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        kde_notice = (
            "\n\n### Turning off KDE's per-dictation popup\n"
            "KDE shows a \"Remote control session started\" popup each time TuxTalk "
            "types. To stop it, use the <b>🔔</b> button in <b>Preferences → General</b>, "
            "or: click the sliders icon on that popup → under <b>Started Remote "
            "Desktop</b> uncheck <b>Show a message in a pop-up</b> → Apply.")

    # Update guidance per install type.
    if is_flatpak:
        update_block = (
            "TuxTalk updates through Flatpak. Run <tt>flatpak update\n"
            "    io.github.ronb1964.TuxTalk</tt>, or use your software center: the built-in\n"
            "    \"Check for Updates\" points you at the same command.")
    else:
        update_block = (
            "Updating matches how you installed TuxTalk:\n"
            "   – <b>.deb / .rpm:</b> click \"Download &amp; Install\". It downloads the new\n"
            "     package and installs it through your system package manager (you'll be\n"
            "     asked for your password once), then offers to restart.\n"
            "   – <b>AppImage:</b> click \"Download &amp; Install\". It swaps in the new version\n"
            "     and restarts automatically.\n"
            "   – <b>From source:</b> Check for Updates shows the command that installs the\n"
            "     new version.\n"
            "   – <b>Arch (AUR):</b> update with your AUR helper, e.g.\n"
            "     <tt>yay -S tuxtalk-appimage</tt> or <tt>yay -S tuxtalk</tt>.")

    paste_tip = (f"Press <b>{paste_key}</b> in the right window and your last dictation is "
                 "typed again. " if paste_key else "")
    paste_line = (f"<b>{paste_key}</b> types your last dictation again, and\n    "
                  if paste_key else "")
    key_choice_tip = _key_choice_tip(is_flatpak, keys_where)

    # The Flatpak's shortcuts portal never hears Esc.
    esc_tip = "" if is_flatpak else (
        "### Changed your mind as you let go\n"
        f"Press <b>Esc</b> right after you let go of <b>{hold}</b>: nothing more is typed,\n"
        "and nothing is sent after it. Esc can only stop what hasn't happened yet: on a\n"
        "fast graphics card a short dictation is usually in before you can reach it.\n"
        "A line already going in finishes, and with Type at pause the phrases typed\n"
        f"before stay where they are. To take back the last one, hold <b>{hold}</b>, say\n"
        "<b>delete last sentence</b>, let go (after a line cut short, your app's own\n"
        "Ctrl+Z).\n\n")

    return {"hold": hold, "toggle": toggle, "paste_tip": paste_tip, "esc_tip": esc_tip,
            "ctrl_space_line": _ctrl_space_line(keys),
            "paste_line": paste_line, "key_choice_tip": key_choice_tip,
            "mouse_tip": _mouse_tip(hold, is_flatpak),
            "shortcut_line": shortcut_line, "typing_para": typing_para,
            "clipboard_privacy": _clipboard_privacy(is_flatpak),
            "no_hotkey_block": no_hotkey_block, "kde_notice": kde_notice,
            "update_block": update_block, "uninstall_text": uninstall_text,
            "log_path": f["log_path"],
            "cfg_dir": f["cfg_dir"], "cache_dir": f["cache_dir"],
            "FEEDBACK_URL": FEEDBACK_URL, "ISSUES_URL": ISSUES_URL, "REPORT_LINK": REPORT_LINK,
            "ENGINE_SIZE_TEXT": ENGINE_SIZE_TEXT, "AI_SIZE_TEXT": AI_SIZE_TEXT,
            "AI_MEMORY_TEXT": AI_MEMORY_TEXT,
            "CUDA_SIZE_TEXT": CUDA_SIZE_TEXT, **examples()}


# The pages (see the module's docstring); a brace meant as one is doubled.
_PAGES = os.path.join(os.path.dirname(__file__), "help")


def page(name: str) -> str:
    """The text of help/<*name*>.txt, its placeholders not yet filled in."""
    with open(os.path.join(_PAGES, f"{name}.txt"), encoding="utf-8") as f:
        return f.read().removesuffix("\n")


def commands_examples() -> str:
    """The Commands tab's examples, under its cards (help_dialog)."""
    return page("commands_examples").format(**_values())


def examples() -> dict:
    """{"examples_<group>": its lines} from help/examples.toml, for the pages:
    a line an example, what's said and what's typed, each of them checked by
    tests/test_help_examples.py against what TuxTalk really types."""
    from .config import load_toml_file
    groups = {}
    for example in load_toml_file(os.path.join(_PAGES, "examples.toml"))["example"]:
        groups.setdefault(f"examples_{example['group']}", []).append(_example_line(example))
    return {name: "\n".join(lines) for name, lines in groups.items()}


def _example_line(example: dict) -> str:
    """One example as a list item: “what you say” → what's typed, with the
    setting it needs, "something like" for the AI's and the key pressed."""
    typed = _typed(example["get"])
    if example.get("ai"):
        typed = f"something like {typed}"
    if example.get("press"):
        typed += f", then {GLib.markup_escape_text(example['press'])}"
    when = f"With {GLib.markup_escape_text(example['when'])}: " if example.get("when") else ""
    return f"  • {when}\u201c<i>{GLib.markup_escape_text(example['say'])}</i>\u201d → {typed}"


def _typed(text: str) -> str:
    """What's typed, in a typewriter font, its line breaks named."""
    def lines(paragraph):
        return " <i>(new line)</i> ".join(f"<tt>{GLib.markup_escape_text(line)}</tt>"
                                          for line in paragraph.split("\n"))
    return " <i>(new paragraph)</i> ".join(lines(p) for p in text.split("\n\n"))


# Commands right after Start Here: once someone can dictate, "what can I
# say?" comes next. Problems last.
_TABS = [
    ("Start Here", "start_here"),
    (COMMANDS_TITLE, "commands_intro"),
    ("Chatting", "chatting"),
    ("Writing", "writing"),
    ("Terminals", "terminals"),
    ("Hands-free", "hands_free"),
    ("Mistakes", "mistakes"),
    ("Tips", "tips"),
    (PROBLEMS_TITLE, "problems"),
]
