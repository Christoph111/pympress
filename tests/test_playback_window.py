#!/usr/bin/env python3
"""Run actual playback-window callbacks with real timing models and fake GTK.

These check editing, atomic application and event-source lifetime. Actual GTK
widget construction remains a separate native bundle/GUI check.
"""

try:
    from . import _support
except ImportError:
    import _support


import ast
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from pympress.filelist import FileList, FileListError, MAX_PAGE_OVERRIDES, validate_slide_seconds
from pympress.playback import PlaybackController
from test_filelist_window import FileListWindow as SharedFileListWindow

SOURCE = _support.SOURCE


class BaseWindow:
    def hide(self):
        self.visible = False

    def present(self):
        self.visible = True

    def get_visible(self):
        return self.visible


class Widget:
    def __init__(self, value=None):
        self.value = value
        self.text = ''

    def set_sensitive(self, value):
        self.sensitive = value

    def set_text(self, value):
        self.text = value

    set_label = set_text

    def set_tooltip_text(self, value):
        self.tooltip = value

    def set_value(self, value):
        self.value = value

    set_active_id = set_value
    set_active = set_value

    def get_value(self):
        return self.value

    get_active_id = get_value
    get_active = get_value

    def update(self):
        pass


class Store(list):
    def __getitem__(self, key):
        return super().__getitem__(int(key) if isinstance(key, str) else key)


class Selection:
    def __init__(self, model):
        self.model = model
        self.selected = None

    def select_path(self, path):
        self.selected = path[0]

    def get_selected(self):
        return self.model, self.selected


GTK = SimpleNamespace(ApplicationWindow=BaseWindow, Button=Widget,
                      TreePath=SimpleNamespace(new_from_indices=lambda indices: indices))
GDK = SimpleNamespace(KEY_Escape=1, KEY_Return=2, KEY_KP_Enter=3, KEY_space=4,
                      KEY_q=ord('q'), KEY_Q=ord('Q'),
                      ModifierType=SimpleNamespace(SHIFT_MASK=1, MOD1_MASK=2, CONTROL_MASK=4,
                                                   META_MASK=8, SUPER_MASK=16))
GLIB = SimpleNamespace(source_remove=mock.Mock(), timeout_add=mock.Mock(return_value=99))
FILE_WINDOW = SimpleNamespace(_local_key=mock.Mock(return_value=True),
                              _quit_shortcut=SharedFileListWindow._quit_shortcut)
source = SOURCE / 'pympress/playback_window.py'
nodes = [node for node in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(node, ast.ClassDef)]
namespace = dict(Gtk=GTK, Gdk=GDK, GLib=GLIB, FileListWindow=FILE_WINDOW,
                 FileList=FileList, FileListError=FileListError, MAX_PAGE_OVERRIDES=MAX_PAGE_OVERRIDES,
                 validate_slide_seconds=validate_slide_seconds)
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
PlaybackWindow = namespace['PlaybackWindow']


class PlaybackWindowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='playback-window-')
        self.pdf = Path(self.directory.name).resolve() / 'Slides.pdf'
        self.pdf.write_bytes(b'PDF')
        files = FileList()
        files.append(self.pdf)
        files.set_current(0)
        self.player = PlaybackController(mock.Mock(return_value=7), mock.Mock(),
                                         mock.Mock(return_value=5), clock=lambda: 100)
        self.ui = SimpleNamespace(file_list=files, playback=self.player, current_page=1,
                                  doc=SimpleNamespace(pages_number=lambda: 3),
                                  file_list_error=mock.Mock(), playback_settings_changed=mock.Mock(),
                                  playback_toggle=mock.Mock(), playback_stop=mock.Mock(),
                                  app=SimpleNamespace(quit=mock.Mock(),
                                                      handle_quit_shortcut=mock.Mock(return_value=False)))
        self.window = PlaybackWindow.__new__(PlaybackWindow)
        self.window.ui = self.ui
        self.window._refreshing = False
        self.window._dirty = False
        self.window._entry_id = None
        self.window._entry_name = None
        self.window._draft_page_seconds = {}
        self.window._status_source = None
        self.window._initialized = False
        self.window.visible = False
        for name in ['mode', 'loop', 'transition_style', 'transition_adjustment', 'filename', 'file_frame',
                     'default_seconds', 'transition_enabled', 'page_hint', 'reset_page_button', 'draft_status',
                     'transition_scale', 'transition_spin', 'play_button', 'stop_button', 'status']:
            setattr(self.window, name, Widget())
        self.window.page_store = Store()
        selection = Selection(self.window.page_store)
        self.window.page_tree = Widget()
        self.window.page_tree.get_selection = lambda: selection
        self.window.get_focus = lambda: None
        GLIB.source_remove.reset_mock()
        GLIB.timeout_add.reset_mock()
        FILE_WINDOW._local_key.reset_mock()
        self.window.refresh()

    def tearDown(self):
        self.window.cleanup()
        self.directory.cleanup()

    def test_primary_q_requests_shared_quit_before_local_draft_editing(self):
        self.ui.app.handle_quit_shortcut.return_value = True
        self.window.default_seconds.set_value(17)
        self.window._mark_dirty()
        self.window.get_focus = lambda: self.window.default_seconds
        for key in (GDK.KEY_q, GDK.KEY_Q):
            for modifier in (GDK.ModifierType.CONTROL_MASK, GDK.ModifierType.META_MASK,
                             GDK.ModifierType.SUPER_MASK):
                with self.subTest(key=key, modifier=modifier):
                    self.ui.app.handle_quit_shortcut.reset_mock()
                    event = SimpleNamespace(keyval=key, state=modifier)
                    self.assertTrue(self.window._on_key_press(self.window, event))
                    self.ui.app.handle_quit_shortcut.assert_called_once_with(event)
        FILE_WINDOW._local_key.assert_not_called()
        self.ui.app.quit.assert_not_called()
        self.assertEqual(self.window.default_seconds.get_value(), 17)
        self.assertTrue(self.window._dirty)

    def test_plain_q_and_modified_variants_remain_local(self):
        for state in (0, GDK.ModifierType.SHIFT_MASK, GDK.ModifierType.MOD1_MASK,
                      GDK.ModifierType.META_MASK | GDK.ModifierType.SHIFT_MASK,
                      GDK.ModifierType.META_MASK | GDK.ModifierType.MOD1_MASK):
            event = SimpleNamespace(keyval=GDK.KEY_q, state=state)
            self.assertTrue(self.window._on_key_press(self.window, event))
            FILE_WINDOW._local_key.assert_called_with(self.window, event)
        self.ui.app.quit.assert_not_called()

    def test_refresh_displays_defaults_and_existing_page_overrides(self):
        self.assertEqual(self.window.filename.text, 'Slides.pdf')
        self.assertEqual(self.window.page_store, [[0, 1, '5', 'File default'], [
                         1, 2, '5', 'File default'], [2, 3, '5', 'File default']])
        self.ui.file_list.update_settings(0, page_seconds={1: 12})
        self.window.refresh()
        self.assertEqual(self.window.page_store[1], [1, 2, '12', 'Custom'])
        self.assertEqual(self.window.page_tree.get_selection().selected, 1)

    def test_refresh_preserves_unapplied_edits_for_same_entry(self):
        self.window.default_seconds.set_value(17)
        self.window._mark_dirty()
        with mock.patch.object(self.window.page_store, 'clear') as clear:
            self.window.refresh()
        clear.assert_not_called()
        self.assertEqual(self.window.default_seconds.get_value(), 17)

    def test_edit_duration_accepts_decimal_comma_and_blank_restores_default(self):
        self.window._duration_edited(None, '1', '2,75')
        self.assertEqual(self.window._draft_page_seconds, {1: 2.75})
        self.assertEqual(self.window.page_store[1][2:], ['2.75', 'Custom'])
        self.window._duration_edited(None, '1', '')
        self.assertEqual(self.window._draft_page_seconds, {})
        self.assertEqual(self.window.page_store[1][2:], ['5', 'File default'])

    def test_invalid_duration_does_not_change_the_previous_draft(self):
        self.window._duration_edited(None, '0', '2')
        for value in ['NaN', 'Infinity', '0', '-1', 'text', '86401']:
            self.window._duration_edited(None, '0', value)
            self.assertEqual(self.window._draft_page_seconds, {0: 2})
            self.assertEqual(self.window.page_store[0][2], '2')
        self.assertEqual(self.ui.file_list_error.call_count, 6)

    def test_changing_default_keeps_individual_rows(self):
        self.window._duration_edited(None, '1', '12')
        self.window.default_seconds.set_value(8)
        self.window._default_changed()
        self.assertEqual([row[2] for row in self.window.page_store], ['8', '12', '8'])

    def test_reset_selected_page_removes_only_that_override(self):
        self.window._duration_edited(None, '0', '10')
        self.window._duration_edited(None, '1', '12')
        self.window._reset_selected_page()
        self.assertEqual(self.window._draft_page_seconds, {0: 10})
        self.assertEqual(self.window.page_store[1][2:], ['5', 'File default'])

    def test_apply_commits_global_and_file_settings_once(self):
        entry_id = self.ui.file_list.current.id
        self.window.mode.set_value('list')
        self.window.loop.set_value(True)
        self.window.transition_style.set_value('fade')
        self.window.transition_adjustment.set_value(1.75)
        self.window.default_seconds.set_value(8)
        self.window.transition_enabled.set_value(False)
        self.window._duration_edited(None, '0', '2.5')
        self.assertTrue(self.window._apply())
        model = self.ui.file_list
        self.assertEqual((model.playback_mode, model.playback_loop), ('list', True))
        self.assertEqual((model.transition_style, model.transition_seconds), ('fade', 1.75))
        self.assertEqual(model.current.id, entry_id)
        self.assertEqual((model.current.slide_seconds, dict(model.current.page_seconds),
                         model.current.transition_enabled), (8, {0: 2.5}, False))
        self.ui.playback_settings_changed.assert_called_once_with()
        self.assertFalse(self.window._dirty)

    def test_invalid_draft_is_atomic_including_global_settings(self):
        original = self.ui.file_list.current
        self.window.transition_style.set_value('fade')
        self.window.transition_adjustment.set_value(2)
        self.window._draft_page_seconds = {0: float('nan')}
        self.assertFalse(self.window._apply())
        self.assertIs(self.ui.file_list.current, original)
        self.assertEqual(self.ui.file_list.transition_style, 'cut')
        self.ui.playback_settings_changed.assert_not_called()

    def test_file_change_preserves_draft_and_applies_to_original_entry(self):
        original_id = self.ui.file_list.current.id
        self.window.default_seconds.set_value(99)
        self.window.transition_style.set_value('fade')
        self.window.transition_adjustment.set_value(1.5)
        self.window._duration_edited(None, '0', '2')
        self.ui.file_list.append(self.pdf)
        self.ui.file_list.set_current(1)
        self.window.refresh()
        self.assertIn('The current file changed', self.window.draft_status.text)
        self.assertEqual(self.window._entry_id, original_id)
        self.assertEqual(self.window.default_seconds.get_value(), 99)
        self.assertTrue(self.window._apply())
        self.assertEqual(self.ui.file_list.entries[0].slide_seconds, 99)
        self.assertEqual(self.ui.file_list.entries[0].page_seconds, {0: 2})
        self.assertEqual(self.ui.file_list.transition_style, 'fade')
        self.assertEqual(self.ui.file_list.current.slide_seconds, 5)
        self.assertEqual(self.window._entry_id, self.ui.file_list.current.id)
        self.ui.file_list_error.assert_not_called()

    def test_draft_follows_stable_identity_after_file_change_and_reorder(self):
        original_id = self.ui.file_list.current.id
        self.window.default_seconds.set_value(12)
        self.window._mark_dirty()
        self.ui.file_list.append(self.pdf)
        self.ui.file_list.move(0, 1)
        self.ui.file_list.set_current(0)
        self.window.refresh()
        self.assertTrue(self.window._apply())
        self.assertEqual(self.ui.file_list.entries[1].id, original_id)
        self.assertEqual(self.ui.file_list.entries[1].slide_seconds, 12)
        self.assertEqual(self.ui.file_list.entries[0].slide_seconds, 5)

    def test_removed_entry_keeps_draft_and_cannot_change_globals_until_discarded(self):
        self.window.default_seconds.set_value(99)
        self.window.transition_style.set_value('fade')
        self.window.transition_adjustment.set_value(1.5)
        self.window._mark_dirty()
        self.ui.file_list.append(self.pdf)
        self.ui.file_list.remove(0)
        self.ui.file_list.set_current(0)
        self.window.refresh()
        self.assertFalse(self.window._apply())
        self.assertEqual(self.ui.file_list.transition_style, 'cut')
        self.assertEqual(self.ui.file_list.current.slide_seconds, 5)
        self.assertEqual(self.window.default_seconds.get_value(), 99)
        self.assertTrue(self.window._dirty)
        self.window._discard()
        self.assertFalse(self.window._dirty)
        self.assertEqual(self.window._entry_id, self.ui.file_list.current.id)
        self.assertEqual(self.window.default_seconds.get_value(), 5)
        self.assertEqual(self.window.draft_status.text, '')

    def test_global_only_draft_does_not_change_newly_opened_file(self):
        self.ui.file_list.set_current(-1)
        self.window.refresh()
        self.window.mode.set_value('list')
        self.window.loop.set_value(True)
        self.window._mark_dirty()
        self.ui.file_list.set_current(0)
        self.window.refresh()
        self.assertIsNone(self.window._entry_id)
        self.assertTrue(self.window._apply())
        self.assertEqual((self.ui.file_list.playback_mode, self.ui.file_list.playback_loop), ('list', True))
        self.assertEqual(self.ui.file_list.current.slide_seconds, 5)

    def test_start_applies_draft_but_pause_and_resume_do_not(self):
        with mock.patch.object(self.window, '_apply', return_value=True) as apply:
            self.window._play_clicked()
            apply.assert_called_once_with()
            self.ui.playback_toggle.assert_called_once_with()
            apply.reset_mock()
            self.player.start(5)
            self.window._play_clicked()
            self.player.pause()
            self.window._play_clicked()
            apply.assert_not_called()

    def test_invalid_draft_prevents_start(self):
        with mock.patch.object(self.window, '_apply', return_value=False):
            self.window._play_clicked()
        self.ui.playback_toggle.assert_not_called()

    def test_status_refresh_does_not_rebuild_or_change_drafts(self):
        self.window.default_seconds.set_value(42)
        self.window._mark_dirty()
        self.player.start(5)
        with mock.patch.object(self.window, 'refresh') as refresh:
            self.window.refresh_status()
        refresh.assert_not_called()
        self.assertEqual(self.window.default_seconds.get_value(), 42)
        self.assertEqual(self.window.play_button.text, 'Pause')
        self.assertIn('5.0', self.window.status.text)

    def test_present_registers_one_status_source_and_hide_cancels_it(self):
        self.window.present()
        self.window.present()
        GLIB.timeout_add.assert_called_once_with(250, self.window._status_tick)
        self.assertEqual(self.window._status_source, 99)
        self.window.hide()
        self.window.hide()
        GLIB.source_remove.assert_called_once_with(99)
        self.assertIsNone(self.window._status_source)
        self.assertFalse(self.window.get_visible())

    def test_status_tick_only_refreshes_status_and_stops_when_hidden(self):
        self.window.visible = True
        with mock.patch.object(self.window, 'refresh_status') as status, \
                mock.patch.object(self.window, 'refresh') as full:
            self.assertTrue(self.window._status_tick())
            status.assert_called_once_with()
            full.assert_not_called()
        self.window.visible = False
        self.window._status_source = 99
        self.assertFalse(self.window._status_tick())
        self.assertIsNone(self.window._status_source)

    def test_escape_hides_and_other_keys_use_native_control_shield(self):
        self.window.visible = True
        self.assertTrue(self.window._on_key_press(None, SimpleNamespace(keyval=GDK.KEY_Escape)))
        self.assertFalse(self.window.visible)
        event = SimpleNamespace(keyval=777)
        self.assertTrue(self.window._on_key_press(None, event))
        FILE_WINDOW._local_key.assert_called_once_with(self.window, event)

    def test_image_entry_uses_file_duration_and_does_not_make_pdf_rows(self):
        image = self.pdf.parent / 'Image.png'
        image.write_bytes(b'PNG')
        self.ui.file_list.append(image)
        self.ui.file_list.set_current(1)
        self.window.refresh()
        self.assertEqual(self.window.page_store, [])
        self.assertIn('An image', self.window.page_hint.text)
        self.assertFalse(self.window.page_tree.sensitive)

    def test_page_rows_are_bounded_without_reading_pages_or_rendering(self):
        self.ui.doc.pages_number = lambda: 20000
        self.window.refresh()
        self.assertEqual(len(self.window.page_store), MAX_PAGE_OVERRIDES)
        self.assertIn('10,000', self.window.page_hint.text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
