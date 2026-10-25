"""What TuxTalk says when a voice edit, or select and move by voice, didn't
happen (notices.edit_problem), by kind: the whole sentence for the notification,
and a few words for the label floating over the window. explain.py has the
longer explanations for the kinds that are rules rather than errors.
"""

NOTICES = {
    "not_installed": "Voice editing needs its AI. Turn it on in Preferences, Advanced.",
    "not_ready": "Voice editing's AI is still starting. Try again in a few seconds.",
    "no_selection": "Select the text to change first, then hold Shift with your dictation "
                    "key and say how to change it.",
    "too_long": "That's too much text to edit at once. Select a smaller part.",
    "no_instruction": "I didn't catch what to change. Hold Shift with your dictation key "
                      "and say it, like \u201cmake this more formal\u201d.",
    "no_answer": "No change: the text may already read that way. Try saying it another way.",
    "terminal": "Voice editing doesn't work in a terminal: there's no selection there to "
                "replace.",
    "needs_wayland": "Voice editing works only in a Wayland session for now. This desktop "
                     "is running X11.",
    "no_clipboard": "TuxTalk couldn't use the clipboard to read the selection, so nothing "
                    "was changed.",
    "window_changed": "You switched windows while the AI was working, so the rewrite "
                      "wasn't typed. It's in Recent Dictations in the TuxTalk menu.",
    "focus_moved": "You switched windows before the edit was done, so nothing was changed.",
    "cant_check_window": "TuxTalk couldn't tell which window has the focus, so it didn't "
                         "press Delete. Press it yourself to delete the selection.",
    "no_shortcuts": "Bold, italics and underline work in LibreOffice and other word "
                    "processors. This app has no formatting TuxTalk can use.",
    "cant_format": "Voice editing changes the words. It can't change the font, size or color.",
    "shortcut_failed": "TuxTalk couldn't press the keys for that. Try doing it from the "
                       "keyboard yourself.",
    "nothing_said": "TuxTalk can only select what you just dictated, right after you "
                    "dictate it. For other text, say \u201cselect the previous 3 words\u201d "
                    "or \u201cselect the line\u201d, or use the mouse.",
    "nav_terminal": "Select and move by voice doesn't work in a terminal, where those "
                    "keys mean other things.",
    "undo_other_window": "TuxTalk can only delete what you just dictated, right after "
                         "you dictate it. Use Ctrl+Z in this app instead.",
    "edit_that_what": "Say what to change after \u201cedit that\u201d, for example "
                      "\u201cedit that, make it shorter\u201d.",
    "already_sent": "That dictation was already sent, so there's nothing to edit.",
    "already_sent_auto": "That dictation was already sent, so there's nothing to edit. "
                         "With Send each dictation automatically on, tap the dictation "
                         "hotkey during the countdown to keep it, then say \u201cedit "
                         "that\u201d.",
}


# The same problems in a few words, for the label floating over the window
# (screen_message): glanced at, gone in seconds. The notification keeps the
# full sentence, in the notification history.
LABELS = {
    "not_installed": "Turn on voice editing's AI in Preferences",
    "not_ready": "The AI is still starting",
    "no_selection": "Select some text first",
    "too_long": "Too much text, select less",
    "no_instruction": "Didn't catch what to change",
    "no_answer": "No change, try other words",
    "terminal": "Can't edit in a terminal",
    "needs_wayland": "Voice editing needs Wayland",
    "no_clipboard": "Couldn't read the selection",
    "window_changed": "Window changed, see Recent Dictations",
    "focus_moved": "Window changed, nothing done",
    "cant_check_window": "Couldn't check the window, nothing deleted",
    "no_shortcuts": "No bold or italics in this app",
    "cant_format": "Can't change font, size or color",
    "shortcut_failed": "Couldn't press the keys",
    "nothing_said": "Works only right after dictating",
    "nav_terminal": "Can't select or move in a terminal",
    "undo_other_window": "Works only right after dictating",
    "edit_that_what": "Say what to change",
    "already_sent": "Already sent, nothing to edit",
    "already_sent_auto": "Already sent. Tap the hotkey sooner",
}
