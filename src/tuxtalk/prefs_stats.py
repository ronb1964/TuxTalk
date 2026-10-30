"""Preferences, Stats tab: how much has been dictated and the typing time
saved. Numbers only; stats.py says what is (and isn't) stored."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from . import stats as stats_module
from .prefs_tab import Tab
from .ui_style import show_message

_PERIODS = [("today", "Today"), ("week", "Last 7 days"), ("all", "All time")]
_ROWS = [("words", "Words"), ("dictations", "Dictations"),
         ("seconds", "Time dictating"), ("saved", "Time saved *")]


class StatsTab(Tab):
    """The Stats tab. The service counts while Preferences is open, so the
    window refreshes it each time the tab is shown."""

    def build(self):
        """The tab's page, filled from the stats file."""
        # Tight spacing: the whole page fits the window without scrolling.
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        header = Gtk.Label(xalign=0)
        header.set_markup('<span size="large"><b>Your Dictation Stats</b></span>')
        vbox.pack_start(header, False, False, 0)
        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.set_line_wrap(True)
        vbox.pack_start(self.subtitle, False, False, 0)
        vbox.pack_start(self._build_table(), False, False, 0)
        self._build_chart(vbox)
        self._build_settings(vbox)
        self.refresh()
        return vbox

    def _build_table(self):
        """Today, the last 7 days and all time, a column each."""
        table = Gtk.Grid(column_spacing=28, row_spacing=6)
        table.set_margin_top(6)
        self.cells = {}
        for col, (_key, title) in enumerate(_PERIODS, start=1):
            head = Gtk.Label(xalign=1)
            head.set_markup(f"<b>{title}</b>")
            table.attach(head, col, 0, 1, 1)
        for r, (field, title) in enumerate(_ROWS, start=1):
            table.attach(Gtk.Label(label=title, xalign=0), 0, r, 1, 1)
            for col, (key, _title) in enumerate(_PERIODS, start=1):
                cell = Gtk.Label(xalign=1)
                table.attach(cell, col, r, 1, 1)
                self.cells[(key, field)] = cell
        return table

    def _build_chart(self, vbox):
        """Words per day for the last two weeks, the speed line and the
        footnote on time saved."""
        chart_title = Gtk.Label(xalign=0)
        chart_title.set_markup("<b>Words per day, last 2 weeks</b>")
        chart_title.set_margin_top(6)
        vbox.pack_start(chart_title, False, False, 0)
        self.chart = Gtk.DrawingArea()
        self.chart.set_size_request(-1, 105)
        self.chart.connect("draw", self._draw_chart)
        self._daily = []
        vbox.pack_start(self.chart, False, False, 0)

        self.speed = Gtk.Label(xalign=0)
        self.speed.set_line_wrap(True)
        # Shown by refresh only when there is a speed to report; this keeps
        # the window's show_all() from showing it as an empty gap.
        self.speed.set_no_show_all(True)
        self.speed.set_margin_top(6)
        vbox.pack_start(self.speed, False, False, 0)

        self.footnote = Gtk.Label(xalign=0)
        self.footnote.set_line_wrap(True)
        self.footnote.get_style_context().add_class("dim-label")
        vbox.pack_start(self.footnote, False, False, 0)

    def _build_settings(self, vbox):
        """The typing speed, Keep usage stats and Reset Stats."""
        # A number box: type a value straight in, or nudge it with - and +;
        # it takes only a whole number between MIN and MAX.
        speed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        speed_row.set_margin_top(4)
        speed_label = Gtk.Label(label="Your typing speed:")
        speed_row.pack_start(speed_label, False, False, 0)
        self.typing_wpm_spin = Gtk.SpinButton.new_with_range(
            stats_module.MIN_TYPING_WPM, stats_module.MAX_TYPING_WPM, 1)
        self.typing_wpm_spin.set_numeric(True)
        # A screen reader names the box by its label.
        speed_label.set_mnemonic_widget(self.typing_wpm_spin)
        self.typing_wpm_spin.set_value(
            stats_module.clamp_typing_wpm(self.config.get("typing_wpm", stats_module.TYPING_WPM)))
        self.typing_wpm_spin.set_tooltip_text(
            "Most people type 35 to 45 words a minute. A free online typing test "
            "will tell you yours. Used only to work out time saved.")
        self.typing_wpm_spin.connect("value-changed", self._on_typing_wpm_changed)
        speed_row.pack_start(self.typing_wpm_spin, False, False, 0)
        speed_row.pack_start(Gtk.Label(label="words a minute"), False, False, 0)
        vbox.pack_start(speed_row, False, False, 0)

        vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        keep = Gtk.CheckButton(label="Keep usage stats")
        keep.set_active(bool(self.config.get("usage_stats", True)))
        keep.set_tooltip_text(
            "Count words, dictations and speaking time per day. Turning this off "
            "stops counting; the stats you already have stay until you reset them.")
        keep.connect("toggled", lambda c: self.update_config("usage_stats", c.get_active()))
        controls.pack_start(keep, False, False, 0)
        reset_btn = Gtk.Button(label="Reset Stats...")
        reset_btn.set_tooltip_text("Delete all usage stats and start counting from zero")
        reset_btn.connect("clicked", self._on_reset)
        controls.pack_end(reset_btn, False, False, 0)
        vbox.pack_start(controls, False, False, 0)

        privacy = Gtk.Label(xalign=0)
        privacy.set_line_wrap(True)
        privacy.set_text("Only these numbers are kept, on this computer. "
                         "TuxTalk never saves what you said.")
        vbox.pack_start(privacy, False, False, 0)

    def refresh(self):
        """Fill the tab from the stats file."""
        typing_wpm = self._typing_wpm()
        data = stats_module.summary(stats_module.load(), typing_wpm=typing_wpm)
        for (key, field), cell in self.cells.items():
            value = data[key][field]
            if field in ("seconds", "saved"):
                cell.set_text(stats_module.format_duration(value))
            else:
                cell.set_text(f"{value:,}")

        if stats_module.is_damaged():
            self.subtitle.set_text(
                "Your stats file couldn't be read, so nothing is being counted. "
                "Reset Stats below starts it fresh.")
        elif data["first_day"] is None:
            self.subtitle.set_text(
                "Nothing counted yet. Dictate something and your stats show up here.")
        else:
            since = data["first_day"].strftime("%B %-d, %Y")
            streak = data["streak"]
            streak_text = (f" You've dictated {streak} days in a row." if streak >= 2 else "")
            self.subtitle.set_text(f"Counting since {since}.{streak_text}")

        self.footnote.set_text(
            f"* Time saved is how long typing the same words would take at your "
            f"typing speed ({typing_wpm} words a minute), minus the time you spent dictating.")

        if data["wpm"]:
            times = data["wpm"] / typing_wpm
            compare = (f" That's about {times:.1f} times as fast as you type."
                       if times >= 1.2 else f" Your typing speed is set to {typing_wpm}.")
            self.speed.set_text(
                f"You dictate about {data['wpm']} words a minute, pauses included.{compare}")
            self.speed.show()
        else:
            self.speed.set_text("")
            self.speed.hide()

        self._daily = data["daily"]
        self.chart.queue_draw()

    def _typing_wpm(self):
        """The typing speed in the tab's box."""
        return stats_module.clamp_typing_wpm(self.typing_wpm_spin.get_value_as_int())

    def _on_typing_wpm_changed(self, spin):
        """Save the new speed (on Apply/OK, like every setting) and show its
        effect on time saved straight away."""
        self.update_config("typing_wpm", spin.get_value_as_int())
        self.refresh()

    def _draw_chart(self, widget, cr):
        """Bar chart of words per day, oldest on the left, today highlighted."""
        daily = self._daily
        if not daily:
            return False
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        label_h, top = 18, 16
        plot_h = height - label_h - top
        most = max((w for _d, w in daily), default=0)
        slot = width / len(daily)
        bar_w = max(4, slot * 0.6)

        style = widget.get_style_context()
        fg = style.get_color(Gtk.StateFlags.NORMAL)

        # Baseline
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.25)
        cr.rectangle(0, top + plot_h, width, 1)
        cr.fill()

        cr.select_font_face("Sans")
        cr.set_font_size(10)
        for i, (day, words) in enumerate(daily):
            x = i * slot + (slot - bar_w) / 2
            if words and most:
                h = max(2, plot_h * words / most)
                is_today = i == len(daily) - 1
                if is_today:
                    cr.set_source_rgb(0.30, 0.69, 0.31)      # TuxTalk green
                else:
                    cr.set_source_rgba(0.30, 0.69, 0.31, 0.55)
                cr.rectangle(x, top + plot_h - h, bar_w, h)
                cr.fill()
            # Day-of-month under each bar
            label = str(day.day)
            ext = cr.text_extents(label)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            cr.move_to(i * slot + (slot - ext.width) / 2, height - 4)
            cr.show_text(label)

        if most:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            cr.move_to(2, 11)
            cr.show_text(f"most: {most:,} word{'' if most == 1 else 's'}")
        return False

    def _on_reset(self, _button):
        """Reset Stats..., after asking: every count goes."""
        answer = show_message(
            self.window, Gtk.MessageType.WARNING, "Reset your usage stats?",
            "This deletes every count so far and starts again from zero. It can't be undone.",
            [("Cancel", Gtk.ResponseType.CANCEL), ("Reset Stats", Gtk.ResponseType.OK)])
        if answer == Gtk.ResponseType.OK:
            if not stats_module.reset():
                show_message(self.window, Gtk.MessageType.ERROR, "Your stats couldn't be reset",
                             "A stats file couldn't be deleted, or a dictation was "
                             "being counted. Try again; the log says why.")
            self.refresh()
