"""Closing the download window must stop the downloads it started.

Each task carries a threading.Event that the download loop checks between
chunks. The per-task Cancel button sets it, and closing the window must too;
otherwise multi-gigabyte downloads carry on in background threads writing to
disk, invisible, uncancellable, and reported to the caller as failures.
"""


from tuxtalk.download_progress_dialog import DownloadTask, UnifiedDownloadDialog


def _task(name="CUDA Libraries"):
    return DownloadTask(name=name, size_text="~1.4GB",
                        download_func=lambda cb, ev: True)


def test_closing_the_window_cancels_unfinished_tasks():
    dlg = UnifiedDownloadDialog.__new__(UnifiedDownloadDialog)
    running, done = _task("CUDA Libraries"), _task("large-v3 AI Model")
    done.completed = True
    dlg.tasks = [running, done]

    dlg._cancel_unfinished_tasks()

    assert running.cancel_event.is_set(), "download kept running after the window closed"


def test_closing_the_window_leaves_finished_tasks_alone():
    """A completed download must not be marked cancelled on the way out."""
    dlg = UnifiedDownloadDialog.__new__(UnifiedDownloadDialog)
    done = _task()
    done.completed = True
    done.success = True
    dlg.tasks = [done]

    dlg._cancel_unfinished_tasks()

    assert not done.cancelled
    assert done.success is True


class _Widget:
    def __init__(self):
        self.markup = []
        self.labels = []

    def set_markup(self, text):
        self.markup.append(text)

    def set_label(self, text):
        self.labels.append(text)

    def set_sensitive(self, value):
        pass

    def set_fraction(self, value):
        pass

    def set_text(self, value):
        pass


def _dialog_with_widgets(monkeypatch, task):
    import tuxtalk.download_progress_dialog as dpd
    monkeypatch.setattr(dpd.GLib, "idle_add", lambda fn, *a: fn(*a))
    monkeypatch.setattr(dpd.GLib, "timeout_add", lambda ms, fn, *a: None)
    dlg = UnifiedDownloadDialog.__new__(UnifiedDownloadDialog)
    dlg.tasks = [task]
    dlg.all_completed = False
    dlg.dialog = None
    dlg.task_widgets = {task: {"status": _Widget(), "cancel_btn": _Widget(),
                               "progress": _Widget()}}
    return dlg


def test_a_cancelled_task_is_not_relabelled_failed(monkeypatch):
    task = _task()
    dlg = _dialog_with_widgets(monkeypatch, task)

    dlg._cancel_task(task)
    dlg._mark_task_complete(task, False)

    widgets = dlg.task_widgets[task]
    assert "Cancelled" in widgets["status"].markup[-1]
    assert widgets["cancel_btn"].labels[-1] == "Cancelled"


def test_progress_reported_after_cancel_doesnt_bring_back_downloading(monkeypatch):
    """The download notices Cancel only between chunks, and reports once
    more meanwhile; the status mustn't flip back to "Downloading..."."""
    task = _task()
    dlg = _dialog_with_widgets(monkeypatch, task)

    dlg._cancel_task(task)
    dlg._update_task_progress(task, "Downloading...", 40)

    assert "Cancelled" in dlg.task_widgets[task]["status"].markup[-1]


def test_progress_text_is_escaped(monkeypatch):
    """An error like '<urlopen error ...>' is not valid Pango markup."""
    task = _task()
    dlg = _dialog_with_widgets(monkeypatch, task)

    dlg._update_task_progress(task, "Failed: <urlopen error> & more", 0)

    assert "&lt;urlopen error&gt; &amp; more" in dlg.task_widgets[task]["status"].markup[-1]


def test_closing_the_window_drops_its_widgets(monkeypatch):
    """Downloads that finish after the window is gone must not touch it."""
    class FakeGtkDialog:
        def show_all(self): pass
        def run(self): return 0
        def destroy(self): pass

    task = _task()
    dlg = UnifiedDownloadDialog(title="t")
    dlg.add_task(task)
    monkeypatch.setattr(dlg, "_build_dialog",
                        lambda: (setattr(dlg, "dialog", FakeGtkDialog()),
                                 dlg.task_widgets.update({task: {}})))
    monkeypatch.setattr(dlg, "_run_task", lambda t: None)

    dlg.run()

    assert dlg.dialog is None
    assert dlg.task_widgets == {}


def test_the_stylesheet_only_reaches_the_download_dialog():
    import re
    import tuxtalk.download_progress_dialog as dpd
    selectors = re.findall(rb"^\s*([^{}\n]+?)\s*\{", dpd._CSS, re.MULTILINE)
    assert selectors
    for selector in selectors:
        assert selector.startswith(b"." + dpd._STYLE_CLASS.encode()), selector


def test_work_that_cant_stop_offers_no_cancel_and_isnt_called_cancelled():
    """The GNOME extension's install ignores Cancel and is a moment long:
    Cancel said "Cancelled" while it went on, and Setup Complete then left
    out "log out and back in"."""
    import pytest
    from gi.repository import Gtk
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    install = DownloadTask(name="GNOME Extension", size_text="~10KB",
                           download_func=lambda cb, ev: True, can_stop=False)
    dlg = UnifiedDownloadDialog(title="t")
    dlg.add_task(install)
    frame = dlg._create_task_widget(install)
    try:
        assert not dlg.task_widgets[install]["cancel_btn"].get_sensitive()
        dlg._cancel_unfinished_tasks()            # the window closed meanwhile
        assert not install.cancelled and not install.cancel_event.is_set()
    finally:
        frame.destroy()


def test_escape_doesnt_close_the_window_on_work_that_cant_stop(monkeypatch):
    """Escape or the X ended the window while the extension went on
    installing, and its result came back as not done."""
    from gi.repository import Gtk
    install = DownloadTask(name="GNOME Extension", size_text="~10KB",
                           download_func=lambda cb, ev: True, can_stop=False)
    dlg = UnifiedDownloadDialog(title="t")
    dlg.add_task(install)
    answers = []

    class Window:
        def show_all(self):
            pass

        def run(self):
            if not answers:                      # Escape, mid-install
                answers.append("escape")
                return Gtk.ResponseType.DELETE_EVENT
            install.completed = install.success = True   # it finishes...
            answers.append("closed when done")           # ...and the timer closes it
            return Gtk.ResponseType.OK

        def destroy(self):
            pass
    monkeypatch.setattr(dlg, "_build_dialog", lambda: setattr(dlg, "dialog", Window()))
    monkeypatch.setattr(dlg, "_run_task", lambda task: None)
    results = dlg.run()
    assert answers == ["escape", "closed when done"]
    assert results["GNOME Extension"]["success"] is True
