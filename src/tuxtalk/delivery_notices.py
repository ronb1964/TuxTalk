"""What TuxTalk says when a dictation didn't reach the window, by how its
delivery ended (delivery.inject_text's outcome): a title for the notification
and the sentence under it, and the label a password field gets over the
window.
"""
from .modifier_hotkeys import HELD_LIMIT_S

PASSWORD_FIELD_LABEL = "TuxTalk doesn't type into password fields"
PASSWORD_FIELD_TITLE = "Not typed into a password field"
PASSWORD_FIELD_NOTICE = ("TuxTalk doesn't type into password fields, so it stopped there. "
                         "Type the password on the keyboard.")

# The text is in Recent Dictations by then (it's saved before injection
# starts), unless a password field refused it (last_dictation.unsay).
TITLES = {
    "failed": "Couldn't type the dictation",
    "partial": "Only part of the dictation was typed",
    "password_field": PASSWORD_FIELD_TITLE,
    "key_held": "Typing stopped: a dictation key stayed down",
    "moved": "Not typed: the window changed while waiting",
}
NOTICES = {
    "failed": "It's saved in Recent Dictations in the TuxTalk menu: click it to copy, "
              "then paste with Ctrl+V.",
    "partial": "The whole text is saved in Recent Dictations in the TuxTalk menu: click "
               "it to copy, then paste with Ctrl+V.",
    # Said its own way, with a label over the window (notices.password_field_refused).
    "password_field": PASSWORD_FIELD_NOTICE,
    "key_held": f"Right Ctrl or Right Alt read as held down for "
                f"{HELD_LIMIT_S:g} seconds, and the text would have gone "
                "in as shortcuts. It's saved in Recent Dictations in the TuxTalk menu: "
                "click it to copy, then paste with Ctrl+V.",
    "moved": "Another window or a password field took the focus while TuxTalk waited for "
             "Right Ctrl or Right Alt to come up. The text is saved in Recent Dictations "
             "in the TuxTalk menu: click it to copy, then paste with Ctrl+V.",
}


def key_not_pressed(key: str) -> tuple[str, str]:
    """(title, sentence) for a said "press enter" or "press tab" whose key a
    dictation key stuck down kept back. The text is in the box."""
    name = key.title()
    return (f"{name} not pressed: a dictation key stayed down",
            f"Right Ctrl or Right Alt read as held down for {HELD_LIMIT_S:g} seconds, and "
            f"{name} would have gone in as a shortcut. The text is in the box: press "
            f"{name} yourself when you're ready.")
