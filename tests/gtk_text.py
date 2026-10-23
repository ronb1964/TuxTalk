"""What a GTK window shows, for tests that check it as the user would read it.

Tests used to look for a label's text in the module's source. That breaks
when the code moves to another file and keeps passing when the label is no
longer shown; reading the built widgets does neither.
"""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


def widgets(root):
    """*root* and every widget inside it, with notebook tabs and submenus."""
    yield root
    if isinstance(root, Gtk.MenuItem) and root.get_submenu() is not None:
        yield from widgets(root.get_submenu())
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            yield from widgets(child)
            if isinstance(root, Gtk.Notebook):
                yield from widgets(root.get_tab_label(child))


def texts(widget):
    """The text one widget shows: its label, a dropdown's entries, its tooltip."""
    out = []
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_text())
    elif isinstance(widget, (Gtk.Button, Gtk.MenuItem)) and widget.get_label():
        out.append(widget.get_label())
    if isinstance(widget, Gtk.ComboBoxText):
        out += [row[0] for row in widget.get_model()]
    if widget.get_tooltip_text():
        out.append(widget.get_tooltip_text())
    return out


def tree_text(*roots):
    """Every text in *roots*, shown or not: for checking that a window never
    says something, even in a fold that is closed."""
    return [text for root in roots for w in widgets(root) for text in texts(w)]


def is_shown(widget):
    """Whether *widget* is on screen once its window is: it and every
    container above it are visible, and no closed fold hides it. A notebook
    page counts, whichever tab is open: its tab is one click away."""
    while widget is not None:
        parent = widget.get_parent()
        if not widget.get_visible():
            return False
        if isinstance(parent, Gtk.Revealer) and not parent.get_reveal_child():
            return False
        if not widget.get_child_visible() and not isinstance(parent, Gtk.Notebook):
            return False
        widget = parent
    return True


def shown_text(*roots):
    """The text a user can see in *roots*, one string per label, entry or tooltip."""
    return [text for root in roots for w in widgets(root) if is_shown(w) for text in texts(w)]


def find(root, kind, text=None):
    """The first *kind* widget in *root* whose own text is *text* (any, if None)."""
    for w in widgets(root):
        if isinstance(w, kind) and (text is None or text in texts(w)):
            return w
    raise LookupError(f"no {kind.__name__} showing {text!r}")


def combo_ids(combo):
    """A Gtk.ComboBoxText's entry ids, in order."""
    return [row[1] for row in combo.get_model()]


def below(widget):
    """Every widget in the rows of *widget*'s Gtk.Grid under it: a section
    that starts with a heading, such as Preferences' TuxTalk Labs."""
    grid = widget.get_parent()
    top = grid.child_get_property(widget, "top-attach")
    return [w for child in grid.get_children()
            if grid.child_get_property(child, "top-attach") > top
            for w in widgets(child)]


def beside(label):
    """The widget right of *label* in its Gtk.Grid: the control a row names."""
    grid = label.get_parent()
    row = grid.child_get_property(label, "top-attach")
    column = grid.child_get_property(label, "left-attach") + 1
    return grid.get_child_at(column, row)
