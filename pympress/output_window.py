"""Independent output-mode and display controls for the audience window."""

import math

from pympress.filelist_window import FileListWindow

import gi
# Select GTK before importing its widgets.
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk  # noqa: E402


class OutputWindow(Gtk.ApplicationWindow):
    """Edit output placement and fades without coupling presentation windows.

    Args:
        ui (:class:`~pympress.ui.UI`): Owner of the audience output controller.
    """

    def __init__(self, ui):
        """Construct an independent, nonmodal set of output controls."""
        super(OutputWindow, self).__init__(application=ui.app, title=_('Output · Pympress'))
        self.ui = ui
        self._refreshing = False
        self.set_default_size(650, 360)
        self.set_size_request(520, 310)
        self.get_style_context().add_class('pympress-file-list')
        self.connect('delete-event', self._on_delete)
        self.connect('key-press-event', self._on_key_press)
        self.connect('key-release-event', self._on_key_release)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        layout.set_border_width(18)
        self.add(layout)
        grid = Gtk.Grid(column_spacing=14, row_spacing=12)
        layout.pack_start(grid, False, False, 0)
        self.mode = Gtk.ComboBoxText()
        self.mode.append('window', _('Window'))
        self.mode.append('fullscreen', _('Fullscreen'))
        self.mode.append('overlay', _('Borderless overlay'))
        self.mode.connect('changed', self._mode_changed)
        self.monitor = Gtk.ComboBoxText()
        self.monitor.set_hexpand(True)
        for row, (label, control) in enumerate(((_('Mode'), self.mode), (_('Display'), self.monitor))):
            title = Gtk.Label(label=label)
            title.set_xalign(0)
            grid.attach(title, 0, row, 1, 1)
            grid.attach(control, 1, row, 1, 1)

        frame = Gtk.Frame(label=_('Overlay fade'))
        settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        settings.set_border_width(12)
        frame.add(settings)
        self.fade_adjustment = Gtk.Adjustment(value=0.5, lower=0, upper=5,
                                              step_increment=0.05, page_increment=0.5)
        self.fade_spin, self.fade_scale = self._slider_row(
            settings, _('Fade duration'), self.fade_adjustment, _('s'), 2)
        layout.pack_start(frame, False, False, 0)

        explanation = Gtk.Label(label=_(
            'Borderless overlay covers the selected display and fades over the windows beneath it. '
            'Hidden output leaves the background fully visible.'))
        explanation.set_line_wrap(True)
        explanation.set_xalign(0)
        layout.pack_start(explanation, False, False, 0)
        self.status = Gtk.Label()
        self.status.set_line_wrap(True)
        self.status.set_xalign(0)
        layout.pack_start(self.status, True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.apply_button = Gtk.Button(label=_('Apply and show'))
        self.apply_button.connect('clicked', self._apply)
        self.show_button = Gtk.Button(label=_('Show'))
        self.show_button.connect('clicked', self._show_clicked)
        self.hide_button = Gtk.Button(label=_('Fade out'))
        self.hide_button.connect('clicked', self._hide_clicked)
        for button in (self.apply_button, self.show_button, self.hide_button):
            buttons.pack_start(button, False, False, 0)
        layout.pack_start(buttons, False, False, 0)
        from pympress.macos_overlay import prepare_independent_window
        prepare_independent_window(self)
        layout.show_all()

    @staticmethod
    def _slider_row(parent, label, adjustment, unit, digits):
        """Add a labelled slider and numeric editor sharing one adjustment."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.pack_start(Gtk.Label(label=label), False, False, 0)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        row.pack_start(scale, True, True, 0)
        spin = Gtk.SpinButton(adjustment=adjustment, climb_rate=0.1, digits=digits)
        spin.set_numeric(True)
        row.pack_start(spin, False, False, 0)
        row.pack_start(Gtk.Label(label=unit), False, False, 0)
        parent.pack_start(row, False, False, 0)
        return spin, scale

    def refresh(self):
        """Reload the current controller settings and available display choices."""
        self._refreshing = True
        try:
            output = self.ui.output
            self.mode.set_active_id(output.mode)
            self.monitor.remove_all()
            for monitor in output.monitors():
                self.monitor.append(str(monitor['index']), monitor['label'])
            self.monitor.set_active_id(str(output.monitor_index))
            self.fade_adjustment.set_value(output.fade_seconds)
        finally:
            self._refreshing = False
        self._mode_changed()
        self.refresh_status()

    def refresh_status(self):
        """Update live output status without overwriting unapplied control edits."""
        output = self.ui.output
        self.status.set_text(output.status_text)
        self.show_button.set_sensitive(not output.visible)
        self.hide_button.set_sensitive(output.visible)
        self.hide_button.set_label(_('Fade out') if output.mode == 'overlay' else _('Hide'))

    def _mode_changed(self, *_args):
        """Enable fade controls only for borderless output."""
        overlay = self.mode.get_active_id() == 'overlay'
        for control in (self.fade_spin, self.fade_scale):
            control.set_sensitive(overlay)

    def _apply(self, *_args):
        """Validate the displayed draft before applying and showing the output."""
        try:
            self.fade_spin.update()
            mode = self.mode.get_active_id()
            monitor = self.monitor.get_active_id()
            seconds = self.fade_adjustment.get_value()
            if mode not in ('window', 'fullscreen', 'overlay'):
                raise ValueError(_('Choose an output mode.'))
            if monitor is None or not monitor.isdigit():
                raise ValueError(_('Choose an available display.'))
            if not math.isfinite(seconds) or not 0 <= seconds <= 5:
                raise ValueError(_('Fade duration must be between 0 and 5 seconds.'))
            self.ui.output.apply(mode, int(monitor), seconds, 1.0)
        except (ValueError, RuntimeError, OSError) as error:
            self.ui.file_list_error(error)
            return False
        self.refresh_status()
        return True

    def _show_clicked(self, *_args):
        """Show the currently configured output without applying its edited draft."""
        try:
            self.ui.output.show()
        except (ValueError, RuntimeError, OSError) as error:
            self.ui.file_list_error(error)
            return False
        self.refresh_status()
        return True

    def _hide_clicked(self, *_args):
        """Hide the current output, using its configured fade in overlay mode."""
        self.ui.output.hide()
        self.refresh_status()

    def _on_key_press(self, _window, event):
        """Keep settings keys local and close only this window on Escape."""
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return FileListWindow._local_key(self, event)

    def _on_key_release(self, _window, event):
        """Prevent focused buttons from activating twice on key release."""
        return (event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space) and
                isinstance(self.get_focus(), Gtk.Button))

    def present(self):
        """Refresh the output draft and present its independent controls."""
        self.refresh()
        super(OutputWindow, self).present()

    def cleanup(self):
        """Hide the management window during application shutdown."""
        self.hide()

    def _on_delete(self, *_args):
        """Hide closed controls without hiding or changing the audience output."""
        self.hide()
        return True
