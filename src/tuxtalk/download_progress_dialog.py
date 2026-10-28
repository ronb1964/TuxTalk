#!/usr/bin/env python3
"""
Unified Download Progress Dialog for TuxTalk
Shows one or more downloads (the Vulkan engine, speech models, CUDA libraries,
the GNOME extension) in one window, each with its own progress bar and Cancel.
"""

import threading
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib

try:
    from tuxtalk.logger import setup_logger
    logger = setup_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


# Every rule is scoped to this class on the dialog, because a screen-wide
# provider otherwise restyles every window in the process (Preferences too).
_STYLE_CLASS = "tuxtalk-download-dialog"
_CSS = b"""
    .tuxtalk-download-dialog {
        background-color: #2b2b2b;
    }
    .tuxtalk-download-dialog .task-box {
        background-color: #363636;
        border-radius: 8px;
        padding: 15px;
        margin: 8px;
    }
    .tuxtalk-download-dialog .task-title {
        color: #ffffff;
        font-weight: bold;
        font-size: 14px;
    }
    .tuxtalk-download-dialog .task-status {
        color: #b8b8b8;
        font-size: 12px;
    }
    .tuxtalk-download-dialog progressbar {
        min-height: 20px;
    }
    .tuxtalk-download-dialog progressbar progress {
        background-color: #4285f4;
        border-radius: 4px;
    }
    .tuxtalk-download-dialog progressbar trough {
        background-color: #1a1a1a;
        border-radius: 4px;
    }
"""
_style_installed = False


def _install_style():
    """Add the dialog's stylesheet to the screen once per process."""
    global _style_installed
    if _style_installed:
        return
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _style_installed = True


class DownloadTask:
    """Represents a single download task with progress tracking."""

    def __init__(self, name, size_text, download_func):
        """
        Initialize a download task.

        Args:
            name: Display name (e.g., "CUDA Libraries")
            size_text: Size indicator (e.g., "~1.4GB")
            download_func: Function that performs the download
                          Should accept (progress_callback, cancel_event) args
                          progress_callback signature: func(message, percent)
        """
        self.name = name
        self.size_text = size_text
        self.download_func = download_func
        self.cancelled = False
        self.completed = False
        self.success = False
        self.cancel_event = threading.Event()


class UnifiedDownloadDialog:
    """
    Unified download progress dialog that can handle multiple downloads simultaneously.
    Each download task gets its own progress bar and cancel button.
    """

    def __init__(self, parent=None, title=None, description=None):
        """
        Initialize the unified download dialog.

        Args:
            parent: Optional parent window for the dialog
            title: Optional custom title shown at the top of the dialog
            description: Optional custom description shown below the title
        """
        self.parent = parent
        self.tasks = []
        self.task_widgets = {}  # Maps task to its UI widgets
        self.dialog = None
        self.all_completed = False
        self._title = title
        self._description = description

    def add_task(self, task):
        """Add a download task to the dialog."""
        self.tasks.append(task)

    def _build_dialog(self):
        """Build the GTK dialog with progress bars for each task."""
        self.dialog = Gtk.Dialog(title=self._title or "Setting up TuxTalk")
        # Height: base 200px + 120px per task + 100px extra if we have a long description
        _extra = 100 if self._description else 0
        self.dialog.set_modal(True)
        self.dialog.set_position(Gtk.WindowPosition.CENTER)
        from .ui_style import fit_dialog_to_screen
        fit_dialog_to_screen(self.dialog, 620, 200 + _extra + (len(self.tasks) * 120))

        if self.parent:
            self.dialog.set_transient_for(self.parent)

        self.dialog.get_style_context().add_class(_STYLE_CLASS)
        _install_style()

        content = self.dialog.get_content_area()
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(30)
        content.set_margin_end(30)
        content.set_spacing(15)

        # Main title
        title_text = self._title or "Setting up TuxTalk"
        title_label = Gtk.Label()
        title_label.set_markup(
            f'<span size="x-large"><b>{GLib.markup_escape_text(title_text)}</b></span>')
        content.pack_start(title_label, False, False, 0)

        # Description (can be multi-line for user-friendly explanations).
        # It is Pango markup on purpose: callers bold the item names.
        desc_text = self._description or "Downloading optional components..."
        desc_label = Gtk.Label()
        desc_label.set_markup(f'<span>{desc_text}</span>')
        desc_label.set_opacity(0.8)
        desc_label.set_line_wrap(True)
        desc_label.set_max_width_chars(70)
        desc_label.set_xalign(0.0)
        content.pack_start(desc_label, False, False, 5)

        # Separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(5)
        sep.set_margin_bottom(10)
        content.pack_start(sep, False, False, 0)

        # Create UI for each task
        for task in self.tasks:
            task_box = self._create_task_widget(task)
            content.pack_start(task_box, False, False, 0)

    def _create_task_widget(self, task):
        """Create UI widget for a single download task."""
        # Main container with rounded background
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.get_style_context().add_class('task-box')

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_top(5)
        vbox.set_margin_bottom(5)

        # Header row: title, size and Cancel button
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # Task title
        title_label = Gtk.Label()
        title_label.set_markup(
            f'<b>{GLib.markup_escape_text(task.name)}</b> '
            f'<span size="small" alpha="70%">({GLib.markup_escape_text(task.size_text)})</span>')
        title_label.set_halign(Gtk.Align.START)
        title_label.get_style_context().add_class('task-title')
        header_box.pack_start(title_label, True, True, 0)

        # Cancel button
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.set_size_request(80, -1)
        cancel_btn.connect("clicked", lambda w: self._cancel_task(task))
        header_box.pack_end(cancel_btn, False, False, 0)

        vbox.pack_start(header_box, False, False, 0)

        # Status label
        status_label = Gtk.Label()
        status_label.set_markup('<span size="small">Waiting to start...</span>')
        status_label.set_halign(Gtk.Align.START)
        status_label.get_style_context().add_class('task-status')
        vbox.pack_start(status_label, False, False, 0)

        # Progress bar
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_show_text(True)
        progress_bar.set_text("0%")
        vbox.pack_start(progress_bar, False, False, 0)

        frame.add(vbox)

        # Store widget references
        self.task_widgets[task] = {
            'frame': frame,
            'title': title_label,
            'status': status_label,
            'progress': progress_bar,
            'cancel_btn': cancel_btn
        }

        return frame

    def _cancel_task(self, task):
        """Handle task cancellation."""
        logger.info(f"User cancelled {task.name}")
        task.cancelled = True
        task.cancel_event.set()

        widgets = self.task_widgets[task]
        widgets['status'].set_markup('<span size="small" color="#ff6b6b">❌ Cancelled</span>')
        widgets['cancel_btn'].set_sensitive(False)
        widgets['cancel_btn'].set_label("Cancelled")

    def _cancel_unfinished_tasks(self):
        """Stop any download still running when the window goes away.

        The download loops check cancel_event between chunks. Without this, a
        multi-gigabyte transfer would carry on writing to disk after the
        window closed, with no progress shown and no way to stop it.

        Completed tasks are left untouched so a finished download is not
        retrospectively marked cancelled.
        """
        for task in self.tasks:
            if not task.completed:
                task.cancelled = True
                task.cancel_event.set()
                logger.info(f"Window closed — cancelling {task.name}")

    def _update_task_progress(self, task, message, percent):
        """Update progress for a specific task (thread-safe)."""
        def update_ui():
            # A download reports once more before it notices Cancel, and a
            # finished one has its last word already.
            if task not in self.task_widgets or task.cancelled or task.completed:
                return False

            widgets = self.task_widgets[task]
            widgets['status'].set_markup(
                f'<span size="small">{GLib.markup_escape_text(str(message))}</span>')
            widgets['progress'].set_fraction(percent / 100.0)
            widgets['progress'].set_text(f"{percent}%")
            return False

        GLib.idle_add(update_ui)

    def _mark_task_complete(self, task, success):
        """Mark a task as complete (thread-safe)."""
        def update_ui():
            if task not in self.task_widgets:
                return False

            task.completed = True
            task.success = success

            widgets = self.task_widgets[task]
            widgets['cancel_btn'].set_sensitive(False)

            if task.cancelled:
                # The download returns False when cancelled; it didn't fail.
                pass
            elif success:
                widgets['status'].set_markup('<span size="small" color="#51cf66">✅ Complete!</span>')
                widgets['progress'].set_fraction(1.0)
                widgets['progress'].set_text("100%")
                widgets['cancel_btn'].set_label("Done")
            else:
                widgets['status'].set_markup('<span size="small" color="#ff6b6b">❌ Failed</span>')
                widgets['cancel_btn'].set_label("Failed")

            # Check if all tasks are done
            self._check_all_complete()
            return False

        GLib.idle_add(update_ui)

    def _check_all_complete(self):
        """Check if all tasks are complete and close dialog if so."""
        all_done = all(task.completed or task.cancelled for task in self.tasks)

        if all_done and not self.all_completed:
            self.all_completed = True
            # Close dialog after short delay
            GLib.timeout_add(1500, lambda: self.dialog.response(Gtk.ResponseType.OK) if self.dialog else False)

    def _run_task(self, task):
        """Run a download task in a background thread."""
        def progress_callback(message, percent):
            self._update_task_progress(task, message, percent)

        def do_download():
            try:
                success = task.download_func(progress_callback, task.cancel_event)
                self._mark_task_complete(task, success)
            except Exception as e:
                logger.error(f"Error in {task.name} download: {e}", exc_info=True)
                self._mark_task_complete(task, False)

        thread = threading.Thread(target=do_download)
        thread.daemon = True
        thread.start()

    def run(self):
        """
        Show the dialog and run all download tasks.

        Returns:
            dict: Results for each task {task_name: (success: bool, cancelled: bool)}
        """
        if not self.tasks:
            logger.warning("No download tasks to run")
            return {}

        # Build the dialog
        self._build_dialog()
        self.dialog.show_all()

        # Start all download tasks
        for task in self.tasks:
            self._run_task(task)

        # Run the dialog (blocks until closed)
        self.dialog.run()
        # Whatever closed it — the X, Escape, or a button — nothing is left to
        # show progress in, so no download may outlive it.
        self._cancel_unfinished_tasks()
        self.dialog.destroy()
        # Cancelled downloads still report back for a moment; with no widgets
        # left, their updates and the auto-close timer do nothing.
        self.task_widgets.clear()
        self.dialog = None

        # Return results
        results = {}
        for task in self.tasks:
            results[task.name] = {
                'success': task.success,
                'cancelled': task.cancelled,
                'completed': task.completed
            }

        return results


def show_unified_download_dialog(cuda=False, extension=False, model=None, parent=None,
                                  title=None, description=None):
    """
    Convenience function to show unified download dialog.

    Args:
        cuda: Whether to download CUDA libraries
        extension: Whether to install GNOME extension
        model: Optional model name to download (e.g. "large-v3"). Files are fetched to
               HuggingFace cache only — the model is NOT loaded into memory here.
        parent: Optional parent window
        title: Optional dialog title override
        description: Optional description text override (shown below the title)

    Returns:
        dict: Download results keyed by task name, each with keys:
              'success' (bool), 'cancelled' (bool), 'completed' (bool)
    """
    # Build default title/description when downloading CUDA + a model together
    if title is None and cuda and model:
        title = f"Downloading {model} and CUDA"
    if description is None and cuda and model:
        from tuxtalk.model_helper import MODEL_DISPLAY_SIZES
        model_size = MODEL_DISPLAY_SIZES.get(model, "~3 GB")
        description = (
            f"To run {model} on your NVIDIA graphics card with CUDA, TuxTalk needs two things:\n\n"
            f"  • <b>CUDA GPU Libraries</b> (~1.4GB) — unlocks GPU acceleration on your NVIDIA card\n"
            f"  • <b>{model} AI Model</b> ({model_size}) — the highest-accuracy speech recognition model\n\n"
            f"Both are one-time downloads. This may take several minutes depending on your connection."
        )

    dialog = UnifiedDownloadDialog(parent=parent, title=title, description=description)

    if cuda:
        from tuxtalk import cuda_helper
        task = DownloadTask(
            name="CUDA Libraries",
            size_text="~1.4GB",
            download_func=cuda_helper.download_cuda_libraries
        )
        dialog.add_task(task)

    if model:
        from tuxtalk.model_helper import make_model_download_func, MODEL_DISPLAY_SIZES
        model_size = MODEL_DISPLAY_SIZES.get(model, "unknown size")
        task = DownloadTask(
            name=f"{model} AI Model",
            size_text=model_size,
            download_func=make_model_download_func(model)
        )
        dialog.add_task(task)

    if extension:
        from tuxtalk import extension_helper

        def extension_download(progress_callback, cancel_event):
            """Wrapper for extension installation with progress tracking."""
            progress_callback("Installing GNOME extension...", 30)
            success = extension_helper.install_extension()
            if success:
                progress_callback("Extension installed successfully!", 100)
            else:
                progress_callback("Installation failed", 0)
            return success

        task = DownloadTask(
            name="GNOME Extension",
            size_text="~10KB",
            download_func=extension_download
        )
        dialog.add_task(task)

    return dialog.run()


if __name__ == '__main__':
    # Show the dialog with two dummy downloads, for checking it by hand.
    import sys
    Gtk.init(sys.argv)
    from .ui_style import prepare_windows
    prepare_windows()

    def dummy_download(progress_callback, cancel_event):
        import time
        for i in range(0, 101, 10):
            if cancel_event.is_set():
                return False
            progress_callback(f"Downloading... {i}%", i)
            time.sleep(0.3)
        return True

    dialog = UnifiedDownloadDialog()

    task1 = DownloadTask(
        name="CUDA Libraries",
        size_text="~1.4GB",
        download_func=dummy_download
    )
    dialog.add_task(task1)

    task2 = DownloadTask(
        name="GNOME Extension",
        size_text="~10KB",
        download_func=dummy_download
    )
    dialog.add_task(task2)

    results = dialog.run()
    print(f"Results: {results}")
