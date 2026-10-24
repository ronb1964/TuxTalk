"""What kind of app a window is, from its class (or Wayland app_id): a
terminal, a code editor, a document, or an app with a typing quirk.

The class arrives from several sources (the GNOME Shell extension, the KWin
script, the compositors) that don't agree on capitalisation, so the lists
are matched case-insensitively, but exactly: "kitty-photo-viewer" isn't a
terminal.
"""

# Window classes that need Ctrl+Shift+V for paste (terminals).
# All other apps get plain Ctrl+V — works in chat/editor/browser inputs and
# avoids a Chromium/Electron regression that mishandles synthetic Ctrl+Shift+V.
_TERMINAL_WM_CLASSES = frozenset({
    "org.gnome.Ptyxis", "Ptyxis",
    # GNOME Shell reports GNOME Terminal (Ubuntu 24.04, Zorin 18, Debian) by
    # its server process: without this one, Ctrl+V typed a raw ^V there.
    "org.gnome.Terminal", "Gnome-terminal", "gnome-terminal-server",
    "konsole", "org.kde.konsole",
    "yakuake", "org.kde.yakuake",
    "kitty",
    "Alacritty",
    "WezTerm", "org.wezfurlong.wezterm",
    "foot", "footclient",
    "Tilix", "com.gexperts.Tilix",
    "Terminator",
    "xterm", "XTerm", "UXTerm",
    "rxvt", "URxvt",
    # Common on Sway / Hyprland setups.
    "com.mitchellh.ghostty", "ghostty",
    "org.gnome.Console", "kgx",
    "xfce4-terminal", "Xfce4-terminal",
    "com.system76.CosmicTerm",
    "mate-terminal", "lxterminal", "qterminal", "terminology",
    "st", "st-256color", "sakura", "Termite", "contour",
    # X11 desktops (XFCE, MATE, Cinnamon...) report these WM_CLASS names.
    "Guake", "Tilda", "Roxterm", "cool-retro-term", "Eterm",
})

# Lower-cased once, because the class arrives from several independent sources
# (the GNOME Shell extension, the KWin script, the compositors) that don't
# agree on capitalisation.
_TERMINAL_WM_CLASSES_LOWER = frozenset(c.lower() for c in _TERMINAL_WM_CLASSES)


# Code editors, for "Plain text in terminals and code editors": no period
# typed at the end. Matched case-insensitively, plus any JetBrains IDE
# (jetbrains-idea, jetbrains-pycharm...). Kate is left out: it's used for
# prose as much as code.
_CODE_EDITOR_CLASSES_LOWER = frozenset({
    "code", "code-oss", "code-url-handler", "codium", "vscodium",
    "dev.zed.zed", "zed", "sublime_text", "geany", "org.gnome.builder",
    "org.kde.kdevelop", "neovide",
})


# Text editors, word processors and note apps, where Enter only starts a new
# line: "Send each dictation automatically" leaves them alone, as it does code
# editors (KWrite got a new line after every dictation).
# Saying "press enter" still presses it.
_DOCUMENT_CLASSES_LOWER = frozenset({
    "org.kde.kwrite", "kwrite", "org.kde.kate", "kate",
    "org.gnome.texteditor", "gnome-text-editor", "org.gnome.gedit", "gedit",
    "org.xfce.mousepad", "mousepad", "xed", "pluma", "featherpad", "notepadqq",
    "libreoffice-writer", "libreoffice-calc", "libreoffice-impress", "soffice",
    "abiword", "org.kde.calligrawords", "calligrawords", "org.kde.ghostwriter",
    "ghostwriter", "org.gnome.gitlab.somas.apostrophe", "obsidian", "joplin",
    "typora", "marktext", "desktopeditors", "wps", "wpp", "et",
})


def is_document_window(wm_class: str | None) -> bool:
    """Whether Enter in the focused window only starts a new line."""
    if not wm_class:
        return False
    return wm_class.lower() in _DOCUMENT_CLASSES_LOWER or app_kind(wm_class) == "code"


def app_kind(wm_class: str | None) -> str:
    """"terminal", "code" or "" for the focused window, for plain_for_app."""
    if not wm_class:
        return ""
    if is_terminal_class(wm_class):
        return "terminal"
    lower = wm_class.lower()
    if lower in _CODE_EDITOR_CLASSES_LOWER or lower.startswith("jetbrains-"):
        return "code"
    return ""


def is_terminal_class(wm_class: str | None) -> bool:
    """Whether the focused window is a terminal, so paste uses Ctrl+Shift+V.

    A terminal treats plain Ctrl+V as readline's quoted-insert and shows
    nothing, so getting this wrong is silent. None means nobody reported focus
    (no provider on this desktop) and must not be read as a terminal.

    Matching is exact-but-case-insensitive: substring matching would make
    "kitty-photo-viewer" a terminal.
    """
    if not wm_class:
        return False
    return wm_class.lower() in _TERMINAL_WM_CLASSES_LOWER


# Electron/Chromium apps where synthetic paste (Ctrl+V from /dev/uinput) is
# broken on Wayland — modifier-key chords are silently dropped and the focused
# input blurs. These apps are typed into instead (plain character keystrokes
# still work, per OpenWhispr #240 and others).
ELECTRON_PASTE_BROKEN_CLASSES = frozenset({
    "Claude", "claude", "claude-desktop", "Claude Desktop", "anthropic-claude",
    # Claude Desktop reports this reverse-DNS id or one of the names above,
    # depending on how it was packaged; a missing one means a paste that
    # silently does nothing.
    "com.anthropic.Claude",
})


# KDE's text editor component takes Shift+Enter as Insert Smart Newline,
# which starts the new line with the symbols the line above starts with: "$45",
# new line, "$25" came out as "$$25", and one more "$" each line after.
SMART_NEWLINE_CLASSES_LOWER = frozenset({
    "org.kde.kwrite", "kwrite", "org.kde.kate", "kate", "org.kde.kdevelop",
})
