"""Independent native slideshow controls and per-file/per-page timing editor."""

import gi
# Select the GTK namespace before importing its widgets and dependent windows.
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

from pympress.filelist import FileList, FileListError, MAX_PAGE_OVERRIDES, validate_slide_seconds  # noqa: E402
from pympress.filelist_window import FileListWindow  # noqa: E402


class PlaybackWindow(Gtk.ApplicationWindow):
    """Edit playback policy and timing drafts independently of the presenter.

    Args:
        ui (:class:`~pympress.ui.UI`): Owner of the file list and playback controller.
    """

    PAGE, NUMBER, SECONDS, SOURCE = range(4)

    def __init__(self, ui):
        """Build playback controls and initialize a draft for the current file."""
        super(PlaybackWindow, self).__init__(application=ui.app, title=_('Playback · Pympress'))
        self.ui = ui
        self._refreshing = False
        self._dirty = False
        self._entry_id = None
        self._entry_name = None
        self._draft_page_seconds = {}
        self._status_source = None
        self._initialized = False
        self.set_default_size(720, 730)
        self.set_size_request(590, 540)
        self.get_style_context().add_class('pympress-file-list')
        self.connect('delete-event', self._on_delete)
        self.connect('hide', lambda *_: self._stop_status_source())
        self.connect('destroy', lambda *_: self._stop_status_source())
        self.connect('key-press-event', self._on_key_press)
        self.connect('key-release-event', self._on_key_release)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        layout.set_border_width(16)
        self.add(layout)
        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mode_row.pack_start(Gtk.Label(label=_('Play:')), False, False, 0)
        self.mode = Gtk.ComboBoxText()
        self.mode.append('current', _('Current file'))
        self.mode.append('list', _('Entire file list'))
        mode_row.pack_start(self.mode, True, True, 0)
        self.loop = Gtk.CheckButton(label=_('Loop'))
        mode_row.pack_start(self.loop, False, False, 0)
        layout.pack_start(mode_row, False, False, 0)

        playback = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.play_button = Gtk.Button(label=_('Start'))
        self.play_button.connect('clicked', self._play_clicked)
        playback.pack_start(self.play_button, False, False, 0)
        self.stop_button = Gtk.Button(label=_('Stop'))
        self.stop_button.connect('clicked', self._stop_clicked)
        playback.pack_start(self.stop_button, False, False, 0)
        self.status = Gtk.Label()
        self.status.set_xalign(0)
        self.status.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        playback.pack_start(self.status, True, True, 4)
        layout.pack_start(playback, False, False, 0)

        transition_frame = Gtk.Frame(label=_('Transition'))
        transition_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        transition_box.set_border_width(10)
        transition_frame.add(transition_box)
        transition_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.transition_style = Gtk.ComboBoxText()
        self.transition_style.append('cut', _('Cut'))
        self.transition_style.append('fade', _('Crossfade'))
        transition_row.pack_start(self.transition_style, False, False, 0)
        self.transition_adjustment = Gtk.Adjustment(value=0.5, lower=0, upper=5,
                                                    step_increment=0.05, page_increment=0.5)
        self.transition_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                          adjustment=self.transition_adjustment)
        self.transition_scale.set_draw_value(False)
        self.transition_scale.set_hexpand(True)
        transition_row.pack_start(self.transition_scale, True, True, 0)
        self.transition_spin = Gtk.SpinButton(adjustment=self.transition_adjustment,
                                              climb_rate=0.1, digits=2)
        transition_row.pack_start(self.transition_spin, False, False, 0)
        transition_row.pack_start(Gtk.Label(label=_('s')), False, False, 0)
        transition_box.pack_start(transition_row, False, False, 0)
        explanation = Gtk.Label(label=_(
            'Slide timing starts after the crossfade. At file boundaries, '
            'the next file determines whether a transition is used.'))
        explanation.set_line_wrap(True)
        explanation.set_xalign(0)
        transition_box.pack_start(explanation, False, False, 0)
        layout.pack_start(transition_frame, False, False, 0)

        self.file_frame = Gtk.Frame(label=_('File settings'))
        file_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        file_box.set_border_width(10)
        self.file_frame.add(file_box)
        self.filename = Gtk.Label()
        self.filename.set_xalign(0)
        self.filename.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        file_box.pack_start(self.filename, False, False, 0)
        defaults = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        defaults.pack_start(Gtk.Label(label=_('Time per slide:')), False, False, 0)
        self.default_seconds = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=5, lower=0.1, upper=86400, step_increment=0.1, page_increment=1),
            climb_rate=0.1, digits=2)
        defaults.pack_start(self.default_seconds, False, False, 0)
        defaults.pack_start(Gtk.Label(label=_('s')), False, False, 0)
        self.transition_enabled = Gtk.CheckButton(label=_('Use transitions for this file'))
        defaults.pack_end(self.transition_enabled, False, False, 0)
        file_box.pack_start(defaults, False, False, 0)

        self.page_hint = Gtk.Label()
        self.page_hint.set_line_wrap(True)
        self.page_hint.set_xalign(0)
        file_box.pack_start(self.page_hint, False, False, 0)
        self.page_store = Gtk.ListStore(int, int, str, str)
        self.page_tree = Gtk.TreeView(model=self.page_store)
        self.page_tree.set_headers_visible(True)
        self.page_tree.append_column(Gtk.TreeViewColumn(_('Slide'), Gtk.CellRendererText(), text=self.NUMBER))
        duration = Gtk.CellRendererText(editable=True)
        duration.connect('edited', self._duration_edited)
        self.page_tree.append_column(Gtk.TreeViewColumn(_('Display time (s)'), duration, text=self.SECONDS))
        source_column = Gtk.TreeViewColumn(_('Source'), Gtk.CellRendererText(), text=self.SOURCE)
        source_column.set_expand(True)
        self.page_tree.append_column(source_column)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(140)
        scrolled.add(self.page_tree)
        file_box.pack_start(scrolled, True, True, 0)
        self.reset_page_button = Gtk.Button(label=_('Reset selected slide to file default'))
        self.reset_page_button.connect('clicked', self._reset_selected_page)
        file_box.pack_start(self.reset_page_button, False, False, 0)
        layout.pack_start(self.file_frame, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        files_button = Gtk.Button(label=_('File list…'))
        files_button.connect('clicked', lambda *_: self.ui.show_file_list())
        footer.pack_start(files_button, False, False, 0)
        self.draft_status = Gtk.Label()
        self.draft_status.set_xalign(0)
        self.draft_status.set_line_wrap(True)
        footer.pack_start(self.draft_status, True, True, 0)
        self.reload_button = Gtk.Button(label=_('Discard'))
        self.reload_button.set_tooltip_text(_('Discard unapplied changes and reload settings for the current file'))
        self.reload_button.connect('clicked', self._discard)
        footer.pack_start(self.reload_button, False, False, 0)
        self.apply_button = Gtk.Button(label=_('Apply'))
        self.apply_button.connect('clicked', self._apply)
        footer.pack_end(self.apply_button, False, False, 0)
        layout.pack_start(footer, False, False, 0)

        self.mode.connect('changed', self._mark_dirty)
        self.loop.connect('toggled', self._mark_dirty)
        self.transition_style.connect('changed', self._style_changed)
        self.transition_adjustment.connect('value-changed', self._mark_dirty)
        self.default_seconds.connect('value-changed', self._default_changed)
        self.transition_enabled.connect('toggled', self._mark_dirty)
        layout.show_all()
        self.refresh()

    @staticmethod
    def _seconds_text(value):
        """Format seconds without redundant trailing fractional zeros."""
        return '{:.2f}'.format(value).rstrip('0').rstrip('.')

    def _mark_dirty(self, *_args):
        """Mark the draft as changed unless controls are being refreshed."""
        if not self._refreshing:
            self._dirty = True
            self.draft_status.set_text(self._draft_message())

    def _draft_message(self):
        """Describe whether the draft still refers to the current file."""
        current = self.ui.file_list.current
        if (current.id if current is not None else None) != self._entry_id:
            if self._entry_id is not None:
                return _('The current file changed · draft still applies to “{}”').format(self._entry_name)
            return _('The current file changed · global changes are not yet applied')
        return _('Changes not yet applied')

    def _style_changed(self, *_args):
        """Enable crossfade controls and mark the draft as changed."""
        fade = self.transition_style.get_active_id() == 'fade'
        self.transition_scale.set_sensitive(fade)
        self.transition_spin.set_sensitive(fade)
        self._mark_dirty()

    def _default_changed(self, *_args):
        """Update draft rows that inherit the file's default duration."""
        if self._refreshing:
            return
        self._mark_dirty()
        value = self._seconds_text(self.default_seconds.get_value())
        for row in self.page_store:
            if row[self.PAGE] not in self._draft_page_seconds:
                row[self.SECONDS] = value

    def refresh(self):
        """Update document settings, preserving unfinished edits for this file."""
        entry = self.ui.file_list.current
        entry_id = entry.id if entry is not None else None
        if self._initialized and self._dirty:
            self.draft_status.set_text(self._draft_message())
            self.refresh_status()
            return
        self._refreshing = True
        try:
            self._entry_id = entry_id
            self._entry_name = entry.name if entry is not None else None
            self.mode.set_active_id(self.ui.file_list.playback_mode)
            self.loop.set_active(self.ui.file_list.playback_loop)
            self.transition_style.set_active_id(self.ui.file_list.transition_style)
            self.transition_adjustment.set_value(self.ui.file_list.transition_seconds)
            self.filename.set_text(entry.name if entry is not None else _('No file from the list is active'))
            self.filename.set_tooltip_text(str(entry.path) if entry is not None else None)
            self.file_frame.set_sensitive(entry is not None)
            self.default_seconds.set_value(entry.slide_seconds if entry is not None else 5)
            self.transition_enabled.set_active(entry.transition_enabled if entry is not None else True)
            self._draft_page_seconds = dict(entry.page_seconds) if entry is not None else {}
            count = self.ui.doc.pages_number() if entry is not None and not entry.is_image else 0
            count = max(0, min(count, MAX_PAGE_OVERRIDES))
            self.page_store.clear()
            for page in range(count):
                self.page_store.append([page, page + 1, self._seconds_text(entry.duration_for_page(page)),
                                        _('Custom') if page in self._draft_page_seconds else _('File default')])
            self.page_tree.set_sensitive(count > 0)
            self.reset_page_button.set_sensitive(count > 0)
            if entry is not None and entry.is_image:
                hint = _('An image is one slide and uses the file’s display time.')
            elif count:
                hint = _('Double-click a time to edit it. Clear the field to use the file default.')
                if self.ui.doc.pages_number() > MAX_PAGE_OVERRIDES:
                    hint = _('Double-click a time to edit it. Clear the field to use the file default. '
                             'Only the first 10,000 slides can be edited individually here.')
                page = max(0, min(self.ui.current_page, count - 1))
                self.page_tree.get_selection().select_path(Gtk.TreePath.new_from_indices([page]))
            else:
                hint = _('Open a PDF from the file list to set timings for individual slides.')
            self.page_hint.set_text(hint)
            self._dirty = False
            self._initialized = True
            self.draft_status.set_text('')
            self._style_changed()
        finally:
            self._refreshing = False
        self.refresh_status()

    def refresh_status(self):
        """Update playback status without rebuilding editable timing rows."""
        player = self.ui.playback
        self.play_button.set_label(_('Pause') if player.running else _('Resume') if player.paused else _('Start'))
        self.stop_button.set_sensitive(player.is_active)
        self.play_button.set_sensitive(player.is_active or self.ui.doc.pages_number() > 0)
        if player.running:
            text = _('Playing · next slide in {:.1f} s').format(player.remaining)
        elif player.paused:
            text = _('Paused · {:.1f} s remaining').format(player.remaining)
        else:
            text = _('Stopped')
        self.status.set_text(text)

    def _duration_edited(self, _renderer, path, text):
        """Validate and store a draft duration for the edited slide."""
        row = self.page_store[path]
        page = row[self.PAGE]
        try:
            stripped = text.strip()
            seconds = validate_slide_seconds(float(stripped.replace(',', '.'))) if stripped else None
        except (ValueError, OverflowError) as error:
            self.ui.file_list_error(_('Invalid display time: {}').format(error))
            return
        if seconds is None:
            self._draft_page_seconds.pop(page, None)
            seconds = self.default_seconds.get_value()
            row[self.SOURCE] = _('File default')
        else:
            self._draft_page_seconds[page] = seconds
            row[self.SOURCE] = _('Custom')
        row[self.SECONDS] = self._seconds_text(seconds)
        self._mark_dirty()

    def _reset_selected_page(self, *_args):
        """Remove the selected slide's custom duration from the draft."""
        _model, selected = self.page_tree.get_selection().get_selected()
        if selected is None:
            return
        row = self.page_store[selected]
        self._draft_page_seconds.pop(row[self.PAGE], None)
        row[self.SECONDS] = self._seconds_text(self.default_seconds.get_value())
        row[self.SOURCE] = _('File default')
        self._mark_dirty()

    def _apply(self, *_args):
        """Apply validated global and per-file draft settings atomically.

        Returns:
            bool: Whether the draft was applied successfully.
        """
        index, entry = None, None
        if self._entry_id is not None:
            index = next((index for index, item in enumerate(self.ui.file_list.entries)
                          if item.id == self._entry_id), None)
            if index is None:
                self.ui.file_list_error(
                    _('The edited file was removed from the list. Your draft has been kept. '
                      'Choose “Discard” to reload the current file.'))
                return False
            entry = self.ui.file_list.entries[index]
        try:
            # Validate all draft values before changing any persistent setting.
            self.default_seconds.update()
            self.transition_spin.update()
            style, seconds = self.transition_style.get_active_id(), self.transition_adjustment.get_value()
            mode, loop = self.mode.get_active_id(), self.loop.get_active()
            validator = FileList()
            validator.set_transition(style, seconds)
            validator.set_playback(mode, loop)
            settings = dict(slide_seconds=self.default_seconds.get_value(),
                            page_seconds=self._draft_page_seconds,
                            transition_enabled=self.transition_enabled.get_active())
            if entry is not None:
                entry.with_settings(**settings)
            self.ui.file_list.set_transition(style, seconds)
            self.ui.file_list.set_playback(mode, loop)
            if entry is not None:
                self.ui.file_list.update_settings(index, **settings)
        except (FileListError, ValueError) as error:
            self.ui.file_list_error(error)
            return False
        self._dirty = False
        self.ui.playback_settings_changed()
        self.refresh()
        return True

    def _discard(self, *_args):
        """Discard unapplied edits and reload settings for the current file."""
        self._dirty = False
        self.refresh()

    def _play_clicked(self, *_args):
        """Apply a new session's draft before toggling playback."""
        if not self.ui.playback.is_active and not self._apply():
            return
        self.ui.playback_toggle()
        self.refresh_status()

    def _stop_clicked(self, *_args):
        """Stop playback and refresh its displayed status."""
        self.ui.playback_stop()
        self.refresh_status()

    def _on_key_press(self, _window, event):
        """Keep playback-window keys local and hide the window on Escape."""
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return FileListWindow._local_key(self, event)

    def _on_key_release(self, _window, event):
        """Consume activation-key releases while a button has focus."""
        return (event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space) and
                isinstance(self.get_focus(), Gtk.Button))

    def _status_tick(self):
        """Refresh status while visible and stop the timeout when hidden."""
        if not self.get_visible():
            self._status_source = None
            return False
        self.refresh_status()
        return True

    def _stop_status_source(self):
        """Cancel the window's status timeout if one is registered."""
        source, self._status_source = self._status_source, None
        if source is not None:
            GLib.source_remove(source)

    def present(self):
        """Show the controls and start at most one status timeout."""
        self.refresh()
        super(PlaybackWindow, self).present()
        if self._status_source is None:
            self._status_source = GLib.timeout_add(250, self._status_tick)

    def hide(self):
        """Hide the controls and release their status timeout."""
        self._stop_status_source()
        super(PlaybackWindow, self).hide()

    def cleanup(self):
        """Release status updates and hide the controls during shutdown."""
        self.hide()

    def _on_delete(self, *_args):
        """Hide a closed window while preserving its unapplied draft."""
        self.hide()
        return True
