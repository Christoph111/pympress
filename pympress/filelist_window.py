# -*- coding: utf-8 -*-
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later version.
"""Native, separate window for ordering presentation files without altering them."""

from pympress.filelist import SUPPORTED_SUFFIXES

import gi
# Select the GTK namespace before importing its widgets.
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, Pango  # noqa: E402


class _ClipboardText:
    """Present GTK's decoded clipboard text to the shared drop validator."""

    def __init__(self, text):
        """Store clipboard text for the shared drop-validation interface."""
        self.text = text

    def get_length(self):
        """Return the UTF-8 byte length used by the shared file validator."""
        return len(self.text.encode('utf-8'))

    def get_uris(self):
        """Return no URI references so validation uses the clipboard text."""
        return None

    def get_text(self):
        """Return the decoded clipboard text without changing its contents."""
        return self.text


class FileListWindow(Gtk.ApplicationWindow):
    """Manage ``ui.file_list`` through the owning UI's ``file_list_*`` callbacks.

    Entries keep their stable IDs in the tree model so that refreshing after a
    move, failed open, or document change preserves a user's selection.
    """

    ID, ACTIVE, NUMBER, NAME, KIND, FOLDER, STATUS = range(7)

    def __init__(self, ui):
        """Build an independent file-list window and connect its local controls."""
        super(FileListWindow, self).__init__(application=ui.app, title=_('File list · Pympress'))
        self.ui = ui
        self.set_default_size(930, 480)
        self.set_size_request(620, 320)
        self.get_style_context().add_class('pympress-file-list')
        self.connect('delete-event', self._on_delete)
        self.connect('key-press-event', self._on_key_press)
        self.connect('key-release-event', self._on_key_release)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        layout.set_border_width(12)
        self.add(layout)

        files = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        files.get_style_context().add_class('console-toolbar')
        layout.pack_start(files, False, False, 0)
        self._button(files, _('Add files…'), self._add_files,
                     _('Add multiple PDFs and images to the file list'))
        self._button(files, _('Paste'), self._paste_clipboard,
                     _('Add files or file paths from the clipboard (⌘V / Ctrl+V)'))
        self._button(files, _('Load list…'), self._load_list,
                     _('Open a saved file list'))
        self.save_button = self._button(files, _('Save list…'), self._save_list,
                                        _('Save the file order as a file list'))

        hint = Gtk.Label(label=_('Drop PDFs and images here. Double-click to open the selected file.'))
        hint.set_xalign(0)
        hint.set_line_wrap(True)
        layout.pack_start(hint, False, False, 0)

        self.store = Gtk.ListStore(str, str, int, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.get_style_context().add_class('presentation-files')
        self.tree.set_headers_visible(True)
        self.tree.set_enable_search(True)
        self.tree.set_search_column(self.NAME)
        self.tree.set_tooltip_column(self.FOLDER)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.tree.get_selection().connect('changed', self._update_buttons)
        self.tree.connect('row-activated', self._row_activated)
        self._column('', self.ACTIVE)
        self._column('#', self.NUMBER)
        self._column(_('File'), self.NAME, expand=True, width=250)
        self._column(_('Type'), self.KIND)
        self._column(_('Folder'), self.FOLDER, expand=True, width=230)
        self._column(_('Status'), self.STATUS, width=150)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.add(self.tree)
        overlay = Gtk.Overlay()
        overlay.add(scrolled)
        self.empty_label = Gtk.Label(label=_('No files yet.\nAdd PDFs or images, or drop them here.'))
        self.empty_label.set_justify(Gtk.Justification.CENTER)
        self.empty_label.set_halign(Gtk.Align.CENTER)
        self.empty_label.set_valign(Gtk.Align.CENTER)
        overlay.add_overlay(self.empty_label)
        overlay.set_overlay_pass_through(self.empty_label, True)
        layout.pack_start(overlay, True, True, 0)

        edit = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        layout.pack_start(edit, False, False, 0)
        self.open_button = self._button(edit, _('Open'), self._open_selected,
                                        _('Show the selected file (Enter)'))
        self.up_button = self._button(edit, _('↑ Move up'), lambda *_: self._move_selected(-1),
                                      _('Move the selection up in the list (Alt+↑)'))
        self.down_button = self._button(edit, _('↓ Move down'), lambda *_: self._move_selected(1),
                                        _('Move the selection down in the list (Alt+↓)'))
        self.remove_button = self._button(edit, _('Remove'), self._remove_selected,
                                          _('Remove from the list; the files remain unchanged'))
        self.clear_button = self._button(edit, _('Clear'), self._clear,
                                         _('Clear the file list without deleting any files'))

        playback = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        layout.pack_start(playback, False, False, 0)
        self.previous_button = self._button(playback, _('← Previous file'), self._previous)
        self.next_button = self._button(playback, _('Next file →'), self._next)
        self.summary = Gtk.Label()
        self.summary.set_xalign(0)
        self.summary.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        playback.pack_start(self.summary, True, True, 6)

        # Keep the whole window usable as a file drop area, including its empty state.
        for widget in (self, self.tree):
            flags = Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT
            widget.drag_dest_set(flags, [], Gdk.DragAction.COPY)
            widget.drag_dest_add_uri_targets()
            widget.drag_dest_add_text_targets()
            widget.connect('drag-drop', ui.on_drop_request)
            widget.connect('drag-data-received', self._on_drop)

        from pympress.macos_overlay import prepare_independent_window
        prepare_independent_window(self)
        layout.show_all()
        self.refresh()

    @staticmethod
    def _button(box, label, callback, tooltip=None):
        """Create a labelled button, connect its callback, and add it to the row."""
        button = Gtk.Button(label=label)
        if tooltip:
            button.set_tooltip_text(tooltip)
        button.connect('clicked', callback)
        box.pack_start(button, False, False, 0)
        return button

    def _column(self, title, index, expand=False, width=None):
        """Append a resizable text column with optional truncation and width."""
        renderer = Gtk.CellRendererText()
        if width:
            renderer.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE)
        column = Gtk.TreeViewColumn(title, renderer, text=index)
        column.set_resizable(True)
        column.set_expand(expand)
        if width:
            column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            column.set_fixed_width(width)
        self.tree.append_column(column)

    def _selected_indices(self):
        """Return selected file positions in ascending list order."""
        _model, rows = self.tree.get_selection().get_selected_rows()
        return sorted(path.get_indices()[0] for path in rows)

    def refresh(self):
        """Refresh file metadata and controls while preserving selected entry IDs."""
        selected = {self.store[index][self.ID] for index in self._selected_indices()}
        entries = self.ui.file_list.entries
        current = self.ui.file_list.current_index
        statuses = getattr(self.ui, 'file_list_status', {})
        self.store.clear()
        selection = self.tree.get_selection()
        for index, entry in enumerate(entries):
            missing = not entry.exists
            status = _('Missing file') if missing else statuses.get(entry.id, '')
            if index == current and not status:
                status = _('Now showing')
            self.store.append([entry.id, '▶' if index == current else '', index + 1, entry.name,
                               _('Image') if entry.is_image else _('PDF'), str(entry.path.parent), str(status)])
            if entry.id in selected:
                selection.select_path(Gtk.TreePath.new_from_indices([index]))

        if entries and not self._selected_indices():
            selection.select_path(Gtk.TreePath.new_from_indices([current if current >= 0 else 0]))
        self.empty_label.set_visible(not entries)
        if 0 <= current < len(entries):
            message = ngettext('{count} file · Active: {position}/{total} – {name}',
                               '{count} files · Active: {position}/{total} – {name}', len(entries))
            summary = message.format(count=len(entries), position=current + 1,
                                     total=len(entries), name=entries[current].name)
        else:
            message = ngettext('{count} file · No file from the list is active',
                               '{count} files · No file from the list is active', len(entries))
            summary = message.format(count=len(entries))
        self.summary.set_text(summary)
        self.summary.set_tooltip_text(summary)
        self._update_buttons()

    def present(self):
        """Refresh the file list, show its window, and focus the file tree."""
        self.refresh()
        super(FileListWindow, self).present()
        self.tree.grab_focus()

    def _update_buttons(self, *_args):
        """Update editing and navigation controls for the current selection."""
        # Selection changes also fire while refresh() rebuilds the model.
        if not hasattr(self, 'open_button'):
            return
        selected = self._selected_indices()
        count = len(self.ui.file_list.entries)
        current = self.ui.file_list.current_index
        self.open_button.set_sensitive(len(selected) == 1)
        self.remove_button.set_sensitive(bool(selected))
        self.up_button.set_sensitive(bool(selected) and selected[0] > 0)
        self.down_button.set_sensitive(bool(selected) and selected[-1] < count - 1)
        self.clear_button.set_sensitive(count > 0)
        self.save_button.set_sensitive(count > 0)
        self.previous_button.set_sensitive(current > 0)
        self.next_button.set_sensitive(count > 0 and current < count - 1)

    def _on_delete(self, *_args):
        """Hide the independent management window instead of destroying it."""
        self.hide()
        return True

    @staticmethod
    def _activate_focused_button(window, event):
        """Restore GTK's usual focused-button activation before shielding app keys."""
        if event.keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            return False
        modifiers = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK |
                     Gdk.ModifierType.META_MASK | Gdk.ModifierType.SUPER_MASK)
        if event.state & modifiers:
            return False
        focused = window.get_focus()
        if not isinstance(focused, Gtk.Button):
            return False
        if focused.is_sensitive() and focused.get_visible():
            focused.clicked()
        return True

    def _on_key_release(self, window, event):
        """Consume button key releases after explicit key-press activation."""
        # The corresponding key press already clicked the button. Do not let a
        # native release handler activate it again, including in modal choosers.
        return (event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space) and
                isinstance(window.get_focus(), Gtk.Button))

    @staticmethod
    def _local_key(window, event, propagate=True):
        """Let focused GTK controls consume keys without presenter app accelerators."""
        if FileListWindow._activate_focused_button(window, event):
            return True
        if propagate and window.propagate_key_event(event):
            return True
        if event.keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            backwards = event.keyval == Gdk.KEY_ISO_Left_Tab or event.state & Gdk.ModifierType.SHIFT_MASK
            window.child_focus(Gtk.DirectionType.TAB_BACKWARD if backwards else Gtk.DirectionType.TAB_FORWARD)
        return True

    def _on_key_press(self, _window, event):
        """Handle list shortcuts without triggering unrelated presentation actions."""
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        focused = self.get_focus()
        typing = isinstance(focused, (Gtk.Entry, Gtk.TextView))
        command = event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK |
                                 Gdk.ModifierType.SUPER_MASK)
        if not typing and command and event.keyval in (Gdk.KEY_Left, Gdk.KEY_Up, Gdk.KEY_Right, Gdk.KEY_Down):
            previous = event.keyval in (Gdk.KEY_Left, Gdk.KEY_Up)
            (self.ui.file_list_previous if previous else self.ui.file_list_next)()
            self.refresh()
            return True
        if not typing and not command and event.keyval in (Gdk.KEY_l, Gdk.KEY_L):
            if event.state & Gdk.ModifierType.SHIFT_MASK:
                self.hide()
            else:
                self.ui.app.activate_action('pointer-mode', 'toggle')
            return True
        paste_modifiers = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK |
                           Gdk.ModifierType.SUPER_MASK)
        if event.keyval in (Gdk.KEY_v, Gdk.KEY_V) and event.state & paste_modifiers:
            self._paste_clipboard()
            return True
        if self.tree.has_focus():
            if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self._open_selected()
                return True
            if event.keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
                self._remove_selected()
                return True
            if event.state & Gdk.ModifierType.MOD1_MASK and event.keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
                self._move_selected(-1 if event.keyval == Gdk.KEY_Up else 1)
                return True
        return self._local_key(self, event)

    def _on_dialog_key_press(self, dialog, event):
        """Preserve local chooser editing, button activation, and cancellation."""
        if event.keyval == Gdk.KEY_Escape:
            dialog.response(Gtk.ResponseType.CANCEL)
            return True
        if self._activate_focused_button(dialog, event):
            return True
        if dialog.propagate_key_event(event):
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if dialog.get_filename():
                dialog.response(Gtk.ResponseType.ACCEPT)
            return True
        return self._local_key(dialog, event, propagate=False)

    def _chooser(self, title, action):
        """Create a local-only file chooser owned by the file-list window."""
        dialog = Gtk.FileChooserDialog(title=title, transient_for=self, modal=True, action=action)
        dialog.add_buttons(_('Cancel'), Gtk.ResponseType.CANCEL,
                           _('Save') if action == Gtk.FileChooserAction.SAVE else _('Open'),
                           Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        dialog.set_local_only(True)
        dialog.connect('key-press-event', self._on_dialog_key_press)
        dialog.connect('key-release-event', self._on_key_release)
        return dialog

    def _add_files(self, *_args):
        """Append files selected in a multi-file PDF and image chooser."""
        dialog = self._chooser(_('Add PDFs and images'), Gtk.FileChooserAction.OPEN)
        dialog.set_select_multiple(True)
        supported = Gtk.FileFilter()
        supported.set_name(_('PDFs and images'))
        for suffix in sorted(SUPPORTED_SUFFIXES):
            supported.add_pattern('*' + suffix)
            supported.add_pattern('*' + suffix.upper())
        dialog.add_filter(supported)
        try:
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                self.ui.file_list_add_paths(dialog.get_filenames(), activate=False)
                self.refresh()
        finally:
            dialog.destroy()

    def _paste_clipboard(self, *_args):
        """Request native clipboard file references before falling back to text."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        target = Gdk.Atom.intern('text/uri-list', False)
        clipboard.request_contents(target, self._on_clipboard_uris)

    def _on_clipboard_uris(self, clipboard, data, *_args):
        """Validate native clipboard references or request decoded text instead."""
        # Prefer native file references, preserving URI decoding and validation.
        if data.get_length() > 0 and data.get_uris():
            self.ui.file_list_drop(data, activate=False)
            self.refresh()
        else:
            # request_text negotiates GTK's supported text targets (including
            # UTF8_STRING and legacy STRING) instead of assuming one encoding.
            clipboard.request_text(self._on_clipboard_text)

    def _on_clipboard_text(self, _clipboard, text, *_args):
        """Validate pasted paths and report an empty clipboard without opening files."""
        if not text:
            message = _('The clipboard contains no file paths.')
            self.summary.set_text(message)
            self.summary.set_tooltip_text(message)
            return
        self.ui.file_list_drop(_ClipboardText(text), activate=False)
        self.refresh()

    def _list_filter(self, dialog):
        """Restrict the list chooser to JSON file-list documents."""
        lists = Gtk.FileFilter()
        lists.set_name(_('Pympress file list (*.json)'))
        lists.add_pattern('*.json')
        lists.add_pattern('*.JSON')
        dialog.add_filter(lists)

    def _load_list(self, *_args):
        """Ask the owning UI to restore an explicitly chosen file list."""
        dialog = self._chooser(_('Load file list'), Gtk.FileChooserAction.OPEN)
        self._list_filter(dialog)
        try:
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                self.ui.file_list_load(dialog.get_filename())
                self.refresh()
        finally:
            dialog.destroy()

    def _save_list(self, *_args):
        """Save a list at the exact path covered by overwrite confirmation."""
        dialog = self._chooser(_('Save file list'), Gtk.FileChooserAction.SAVE)
        self._list_filter(dialog)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(_('Presentation.pympress-list.json'))
        try:
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                # Keep precisely the path for which GTK confirmed overwriting.
                self.ui.file_list_save(dialog.get_filename())
        finally:
            dialog.destroy()

    def _open_selected(self, *_args):
        """Open the selected entry only when exactly one file is selected."""
        selected = self._selected_indices()
        if len(selected) == 1:
            self.ui.file_list_activate(selected[0])
            self.refresh()

    def _row_activated(self, _tree, path, _column):
        """Open the activated tree row through the owning UI."""
        self.ui.file_list_activate(path.get_indices()[0])
        self.refresh()

    def _remove_selected(self, *_args):
        """Remove selected list references without deleting their source files."""
        selected = self._selected_indices()
        if selected:
            self.ui.file_list_remove(selected)
            self.refresh()

    def _move_selected(self, delta):
        """Shift selected entries as a group while preserving their relative order."""
        selected = self._selected_indices()
        count = len(self.ui.file_list.entries)
        if not selected or selected[0] + delta < 0 or selected[-1] + delta >= count:
            return
        # Move toward the free edge first, so a selected block retains its order.
        for index in sorted(selected, reverse=delta > 0):
            if self.ui.file_list_move(index, delta) is False:
                break
        self.refresh()

    def _clear(self, *_args):
        """Ask the owning UI to clear the list with its unsaved-change safeguards."""
        self.ui.file_list_clear()
        self.refresh()

    def _previous(self, *_args):
        """Open the previous available file through the owning UI."""
        self.ui.file_list_previous()
        self.refresh()

    def _next(self, *_args):
        """Open the next available file through the owning UI."""
        self.ui.file_list_next()
        self.refresh()

    def _on_drop(self, _widget, context, _x, _y, data, _info, time):
        """Append validated drop data and finish the copy operation exactly once."""
        success = False
        try:
            success = bool(self.ui.file_list_drop(data, activate=False))
        finally:
            Gtk.drag_finish(context, success, False, time)
        self.refresh()
