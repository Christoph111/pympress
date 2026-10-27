"""Move one file-list view between its independent window and Presenter dock."""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk  # noqa: E402


class FileListDock:
    """Keep file selection and controls while changing their visible host."""

    def __init__(self, view, ui):
        """Restore docking preferences without opening the file list."""
        self.view, self.ui, self.panel = view, ui, view.panel
        self.docked = False
        self.pane = None
        self._setting_position = False
        self._awaiting_allocation = True
        self._allocated_width = None
        try:
            self._width = ui.config.getint('presenter', 'file_list_dock_width', fallback=360)
        except ValueError:
            self._width = 360
        self._width = self._bounded_width(self._width)
        try:
            docked = ui.config.getboolean('presenter', 'file_list_docked', fallback=False)
        except ValueError:
            docked = False
        self.panel.set_no_show_all(True)
        self.panel.hide()
        if docked:
            self.set_docked(True)
        else:
            self.view.update_dock_controls(False)

    @property
    def visible(self):
        """Report the shared panel's visibility in its current host."""
        return self.panel.get_visible() and (self.docked or self.view.get_visible())

    @staticmethod
    def _bounded_width(width, available=None):
        """Keep a usable preferred width while preserving Presenter space."""
        width = max(280, min(800, width))
        if available is not None and available > 1:
            width = min(width, max(1, int(available * 0.45)))
        return width

    def _ensure_pane(self):
        """Wrap only the slide-layout region, preserving global controls."""
        if self.pane is not None:
            return
        central = self.ui.p_central
        parent = central.get_parent()
        position = parent.get_children().index(central)
        packing = parent.query_child_packing(central)
        self.pane = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        self.pane.set_wide_handle(True)
        self.pane.connect('notify::position', self._position_changed)
        self.pane.connect('size-allocate', self._size_allocated)
        parent.remove(central)
        self.pane.pack2(central, resize=True, shrink=False)
        parent.pack_start(self.pane, packing[0], packing[1], packing[2])
        parent.set_child_packing(self.pane, *packing)
        parent.reorder_child(self.pane, position)
        self.pane.show()

    def _restore_width(self):
        """Apply a bounded divider position without changing its preference."""
        if self.pane is None or not self.docked or not self.visible:
            return
        available = self.pane.get_allocated_width()
        if available <= 1:
            return
        width = self._bounded_width(self._width, available)
        if self.pane.get_position() != width:
            self._setting_position = True
            try:
                self.pane.set_position(width)
            finally:
                self._setting_position = False

    def _size_allocated(self, _pane, _allocation):
        """Restore the saved divider after mapping and Presenter resizing."""
        self._restore_width()
        if self.docked and self.visible and self.pane.get_allocated_width() > 1:
            self._allocated_width = self.pane.get_allocated_width()
            self._awaiting_allocation = False

    def _position_changed(self, pane, _property):
        """Save divider moves only while the dock has a real allocation."""
        if (self._setting_position or self._awaiting_allocation or not self.docked or not self.visible or
                pane.get_allocated_width() <= 1 or self.panel.get_allocated_width() <= 1 or
                pane.get_allocated_width() != self._allocated_width):
            return
        self._width = self._bounded_width(pane.get_position(), pane.get_allocated_width())
        self.ui.config.set('presenter', 'file_list_dock_width', str(self._width))
        self._restore_width()

    def set_docked(self, docked):
        """Switch hosts without creating a second view or changing selection."""
        docked = bool(docked)
        if docked == self.docked:
            return
        visible = self.visible
        if self.docked and self.has_focus():
            self.ui.p_win.set_focus(None)
        self.panel.hide()
        Gtk.Window.hide(self.view)
        if docked:
            self._ensure_pane()
        self.panel.get_parent().remove(self.panel)
        if docked:
            self.pane.pack1(self.panel, resize=False, shrink=False)
        else:
            self.view.add(self.panel)
        self.docked = docked
        self.view.set_compact(docked)
        self.view.update_dock_controls(docked)
        self.ui.config.set('presenter', 'file_list_docked', 'on' if docked else 'off')
        if visible:
            self.present(refresh=False)

    def present(self, refresh=True):
        """Show the selected host and focus its existing file tree."""
        if refresh:
            self.view.refresh()
        if self.docked:
            self._awaiting_allocation = True
        self.panel.show()
        if self.docked:
            self.ui.p_win.present()
            self._restore_width()
        else:
            Gtk.Window.present(self.view)
        self.view.tree.grab_focus()
        self.ui.config.set('presenter', 'file_list_visible', 'on')

    def hide(self):
        """Hide the current host and release dock focus before hiding it."""
        if self.docked and self.has_focus():
            self.ui.p_win.set_focus(None)
        self.panel.hide()
        Gtk.Window.hide(self.view)
        self.ui.config.set('presenter', 'file_list_visible', 'off')

    def cleanup(self):
        """Close native surfaces while preserving the next launch's preferences."""
        if self.docked and self.has_focus():
            self.ui.p_win.set_focus(None)
        self.panel.hide()
        Gtk.Window.hide(self.view)

    def toggle(self):
        """Toggle the file list in its currently selected host."""
        if self.visible:
            self.hide()
        else:
            self.present()

    def has_focus(self):
        """Report whether the current host's focused control is in the panel."""
        host = self.ui.p_win if self.docked else self.view
        focused = host.get_focus()
        return bool(self.visible and focused is not None and
                    (focused is self.panel or focused.is_ancestor(self.panel)))

    def handle_key(self, event):
        """Dispatch one local key phase through the visible host."""
        if not self.has_focus():
            return False
        host = self.ui.p_win if self.docked else self.view
        if event.type == Gdk.EventType.KEY_PRESS:
            return self.view._on_key_press(host, event)
        if event.type == Gdk.EventType.KEY_RELEASE:
            return self.view._on_key_release(host, event)
        return False
