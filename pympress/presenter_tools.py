"""Presenter-only controls, field visibility and direct layout editing."""

import copy
import json

import gi
# Select the GTK namespace before importing its widgets.
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk  # noqa: E402


DRAG_TYPE = 'application/x-pympress-presenter-field'
FIELDS = (
    ('current', _('Current slide'), 'p_frame_cur', 'show_current'),
    ('next', _('Next slides'), 'grid_next', 'show_next'),
    ('notes', _('Notes slide'), 'p_frame_notes', 'show_notes'),
    ('annotations', _('Annotations'), 'p_frame_annot', 'show_annotations'),
    ('page', _('Slide number'), 'frame_cur', 'show_page_number'),
    ('timer', _('Timer'), 'frame_time', 'show_timer'),
    ('estimate', _('Target duration'), 'frame_ett', 'show_estimate'),
    ('clock', _('Clock'), 'frame_clock', 'show_clock'),
)
CENTRAL_FIELDS = frozenset({'current', 'next', 'notes', 'annotations'})
INFO_FIELDS = ('page', 'timer', 'estimate', 'clock')


def swapped_layout(layout, source, destination):
    """Return a separate tree with two existing presentation leaves exchanged."""
    found = set()

    def visit(node):
        """Copy each layout node while exchanging the two requested leaves."""
        if isinstance(node, str):
            found.add(node)
            return destination if node == source else source if node == destination else node
        result = copy.deepcopy(node)
        result['children'] = [visit(child) for child in node['children']]
        return result

    result = visit(layout)
    if source == destination or not {source, destination} <= found:
        raise ValueError(_('Both fields must be part of the current layout.'))
    return result


class PresenterTools:
    """Expose ``widget`` for the UI's top bar and ``refresh()`` after layout changes."""

    def __init__(self, ui):
        """Build Presenter controls and field handles using the existing UI widgets."""
        self.ui = ui
        self.editing = False
        self._refreshing = False
        self._handles = []
        self._providers = {}
        self._scales = {}
        self.fields = {key: ui.get_object(name) for key, _label, name, _option in FIELDS}
        self.checks = {}
        self.spins = {}
        self._atom = Gdk.Atom.intern(DRAG_TYPE, False)
        ui.p_win.get_style_context().add_class('pympress-console')

        self.widget = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.widget.set_border_width(10)
        self.widget.get_style_context().add_class('presenter-controls')
        brand = Gtk.Label(label='PYMPRESS / {}'.format(_('Presenter').upper()))
        brand.get_style_context().add_class('console-brand')
        brand.set_margin_end(16)
        self.widget.pack_start(brand, False, False, 0)
        self._button(_('File list'), 'show-file-list', tooltip=_('Show or hide the file list (Shift+L)'))
        self._button(_('Playback'), 'show-playback', tooltip=_('Configure playback and transitions'))
        self._button(_('Output'), 'show-output', tooltip=_('Choose output mode, display, and overlay fade'))
        self._button(_('Show / hide'), 'output-toggle', tooltip=_('Show or hide audience output (F10)'))
        self._button(_('Laser'), 'pointer-mode', 'toggle', _('Toggle the laser pointer (L)'))

        fields_button = Gtk.MenuButton(label=_('Fields'))
        self.widget.pack_end(fields_button, False, False, 0)
        popover = Gtk.Popover.new(fields_button)
        fields_button.set_popover(popover)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        body.set_border_width(14)
        popover.add(body)
        heading = Gtk.Label(label=_('PRESENTER FIELDS'))
        heading.set_xalign(0)
        body.pack_start(heading, False, False, 3)
        for key, label, _name, _option in FIELDS:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            check = Gtk.CheckButton(label=label)
            check.connect('toggled', self._field_toggled, key)
            row.pack_start(check, True, True, 0)
            spin = Gtk.SpinButton.new_with_range(70, 200, 10)
            spin.set_numeric(True)
            spin.set_width_chars(3)
            spin.set_tooltip_text(_('Font size in percent; resize slides using the dividers'))
            spin.connect('value-changed', self._scale_changed, key)
            row.pack_end(spin, False, False, 0)
            body.pack_start(row, False, False, 0)
            self.checks[key], self.spins[key] = check, spin
        note = Gtk.Label(label=_('Size: drag the dividers.\n'
                                 'Position: enable Edit layout, then drag field titles.\n'
                                 'Notes slides appear for PDFs with notes.'))
        note.set_xalign(0)
        note.set_line_wrap(True)
        body.pack_start(note, False, False, 5)
        reset = Gtk.Button(label=_('Show all fields'))
        reset.connect('clicked', self._show_all_fields)
        body.pack_start(reset, False, False, 0)
        body.show_all()

        self.edit_button = Gtk.ToggleButton(label=_('Edit layout'))
        self.edit_button.connect('toggled', lambda button: self.set_editing(button.get_active()))
        self.widget.pack_end(self.edit_button, False, False, 0)
        self._button(_('Advanced layout…'), 'edit-layout', tooltip=_('Edit groups, orientation, and size distribution'))
        for key in ('current', 'notes', 'annotations') + INFO_FIELDS:
            self._add_drag_handle(key, self.fields[key])
        for frame in ui.p_frames_next:
            self._add_drag_handle('next', frame)
        self._restore_info_order()
        self.widget.show_all()
        self.refresh()

    def _button(self, label, action, target=None, tooltip=None):
        """Create a toolbar button bound to an application action and optional target."""
        button = Gtk.Button(label=label)
        button.set_tooltip_text(tooltip or label)
        button.connect('clicked', lambda *_: self.ui.app.activate_action(action, target))
        self.widget.pack_start(button, False, False, 0)
        return button

    def show_layout_editor(self, *_args):
        """Open the existing advanced layout editor through its application action."""
        self.ui.app.activate_action('edit-layout', None)

    def _option(self, key):
        """Return the persistent visibility option for a Presenter field."""
        return next(option for field, _label, _name, option in FIELDS if field == key)

    def _visible(self, key):
        """Read field visibility while respecting the existing annotations action."""
        if key == 'annotations':
            return self.ui.show_annotations
        return self.ui.config.getboolean('presenter', self._option(key), fallback=True)

    def set_field_visible(self, key, visible):
        """Persist and apply visibility for one Presenter field."""
        if key not in self.fields:
            raise ValueError(_('Unknown presenter field'))
        self.ui.config.set('presenter', self._option(key), 'on' if visible else 'off')
        if key == 'annotations':
            self.ui.show_annotations = bool(visible)
            self.ui.app.set_action_state('annotations', bool(visible))
        self.refresh()
        self.ui.p_win.queue_resize()

    def _field_toggled(self, check, key):
        """Apply a visibility change unless controls are being refreshed."""
        if not self._refreshing:
            self.set_field_visible(key, check.get_active())

    def set_field_scale(self, key, scale):
        """Persist a clamped font-size percentage for one Presenter field."""
        if key not in self.fields:
            raise ValueError(_('Unknown presenter field'))
        scale = max(70, min(200, int(scale)))
        self.ui.config.set('presenter', 'field_{}_scale'.format(key), str(scale))
        self.refresh()

    def _scale_changed(self, spin, key):
        """Apply a font-size change unless controls are being refreshed."""
        if not self._refreshing:
            self.set_field_scale(key, spin.get_value_as_int())

    def _show_all_fields(self, *_args):
        """Restore every field and synchronize the existing annotations action."""
        for key in self.fields:
            self.ui.config.set('presenter', self._option(key), 'on')
        self.ui.show_annotations = True
        self.ui.app.set_action_state('annotations', True)
        self.refresh()

    def refresh(self):
        """Restore field visibility, font sizes, and controls from persistent settings."""
        self._refreshing = True
        try:
            for key, widget in self.fields.items():
                visible = self._visible(key)
                widget.set_no_show_all(not visible)
                widget.set_visible(visible)
                self.checks[key].set_active(visible)
                try:
                    scale = self.ui.config.getint('presenter', 'field_{}_scale'.format(key), fallback=100)
                except ValueError:
                    scale = 100
                scale = max(70, min(200, scale))
                self.spins[key].set_value(scale)
                if self._scales.get(key) != scale:
                    context = widget.get_style_context()
                    if key in self._providers:
                        context.remove_provider(self._providers[key])
                    provider = Gtk.CssProvider()
                    provider.load_from_data(('* { font-size: %.2fpt; }' % (10 * scale / 100)).encode())
                    context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
                    self._providers[key], self._scales[key] = provider, scale
        finally:
            self._refreshing = False

    def _add_drag_handle(self, key, frame):
        """Wrap a frame title with a handle reserved for internal layout dragging."""
        label = frame.get_label_widget()
        if label is None:
            return
        frame.set_label_widget(None)
        handle = Gtk.EventBox()
        handle.get_style_context().add_class('presenter-field-handle')
        handle.set_visible_window(False)
        handle.add(label)
        frame.set_label_widget(handle)
        handle.show_all()
        handle.connect('drag-data-get', self._drag_data_get, key)
        handle.connect('drag-drop', self._drag_request)
        handle.connect('drag-data-received', self._drag_received, key)
        self._handles.append(handle)
        handle.set_tooltip_text(_('Enable “Edit layout” to move fields'))

    def set_editing(self, enabled):
        """Enable or disable dragging of the Presenter field title handles."""
        self.editing = bool(enabled)
        context = self.ui.p_win.get_style_context()
        (context.add_class if self.editing else context.remove_class)('layout-editing')
        for handle in self._handles:
            if self.editing:
                targets = [Gtk.TargetEntry.new(DRAG_TYPE, Gtk.TargetFlags.SAME_APP, 1)]
                handle.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, targets, Gdk.DragAction.MOVE)
                handle.drag_dest_set(Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT,
                                     targets, Gdk.DragAction.MOVE)
            else:
                handle.drag_source_unset()
                handle.drag_dest_unset()
        if self.edit_button.get_active() != self.editing:
            self.edit_button.set_active(self.editing)

    def _drag_data_get(self, _widget, _context, data, _info, _time, key):
        """Publish only the internal field identifier while layout editing is active."""
        if self.editing:
            data.set(self._atom, 8, key.encode('ascii'))

    def _drag_request(self, widget, context, _x, _y, time):
        """Request private field data only from a compatible active layout drag."""
        if not self.editing or widget.drag_dest_find_target(context, None) != self._atom:
            return False
        widget.drag_get_data(context, self._atom, time)
        return True

    def _drag_received(self, _widget, context, _x, _y, data, _info, time, destination):
        """Apply a validated field move and finish the internal drag exactly once."""
        success = False
        try:
            if not self.editing or data.get_length() > 32:
                return
            source = bytes(data.get_data()).decode('ascii')
            success = self.move_field(source, destination)
        except (ValueError, UnicodeError, TypeError):
            pass
        finally:
            Gtk.drag_finish(context, success, False, time)

    def move_field(self, source, destination):
        """Reorder information fields or exchange central fields while editing."""
        if not self.editing or source == destination:
            return False
        if source in CENTRAL_FIELDS and destination in CENTRAL_FIELDS:
            name = self.ui.layout_name(self.ui.notes_mode)
            layout = swapped_layout(self.ui.config.get_layout(name), source, destination)
            self.ui.config.update_layout_tree(name, layout)
            self.ui.load_layout(None)
            self.refresh()
            return True
        if source in INFO_FIELDS and destination in INFO_FIELDS:
            bottom = self.ui.get_object('bottom')
            position = bottom.get_children().index(self.fields[destination])
            bottom.reorder_child(self.fields[source], position)
            order = [key for child in bottom.get_children() for key in INFO_FIELDS if self.fields[key] == child]
            self.ui.config.set('presenter', 'info_field_order', json.dumps(order))
            return True
        return False

    def _restore_info_order(self):
        """Restore a valid saved information-field order without moving other controls."""
        try:
            order = json.loads(self.ui.config.get('presenter', 'info_field_order', fallback='[]'))
            if not isinstance(order, list) or len(order) != len(INFO_FIELDS) or set(order) != set(INFO_FIELDS):
                return
            bottom = self.ui.get_object('bottom')
            children = bottom.get_children()
            positions = [index for index, child in enumerate(children) if child in self.fields.values()]
            for position, key in zip(positions, order):
                children[position] = self.fields[key]
            for position, child in enumerate(children):
                bottom.reorder_child(child, position)
        except (ValueError, TypeError):
            pass
