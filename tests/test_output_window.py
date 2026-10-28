"""Check real output-control drafts and keyboard isolation without native GTK.

Native widget construction and audience-window fades require the separate bundle
smoke tests; these exercise management callbacks with a fake output controller.
"""
try:
    from . import _support
except ImportError:
    import _support

import ast
import math
from types import SimpleNamespace
import unittest
from unittest import mock
from test_filelist_window import FileListWindow as SharedFileListWindow

SOURCE = _support.SOURCE


class BaseWindow:
    def hide(self):
        self.visible = False

    def present(self):
        self.visible = True


class Widget:
    def __init__(self, value=None):
        self.value, self.text, self.options, self.sensitive = value, '', [], True
        self.update = mock.Mock()

    def set_value(self, value):
        self.value = value

    set_active_id = set_value

    def get_value(self):
        return self.value

    get_active_id = get_value

    def set_sensitive(self, value):
        self.sensitive = value

    def set_text(self, text):
        self.text = text

    set_label = set_text

    def remove_all(self):
        self.options = []

    def append(self, key, label):
        self.options.append((key, label))


GTK = SimpleNamespace(ApplicationWindow=BaseWindow, Button=Widget)
GDK = SimpleNamespace(KEY_Escape=1, KEY_Return=2, KEY_KP_Enter=3, KEY_space=4,
                      KEY_q=ord('q'), KEY_Q=ord('Q'),
                      ModifierType=SimpleNamespace(SHIFT_MASK=1, MOD1_MASK=2, CONTROL_MASK=4,
                                                   META_MASK=8, SUPER_MASK=16))
FILE_WINDOW = SimpleNamespace(_local_key=mock.Mock(return_value=True),
                              _quit_shortcut=SharedFileListWindow._quit_shortcut)
source = SOURCE / 'pympress/output_window.py'
classes = [node for node in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(node, ast.ClassDef)]
namespace = dict(Gtk=GTK, Gdk=GDK, FileListWindow=FILE_WINDOW, math=math)
exec(compile(ast.Module(body=classes, type_ignores=[]), str(source), 'exec'), namespace)
OutputWindow = namespace['OutputWindow']


class OutputWindowTests(unittest.TestCase):
    def setUp(self):
        self.output = SimpleNamespace(
            mode='overlay', monitor_index=1, fade_seconds=0.75, target_opacity=1.0,
            visible=False, status_text='Output is hidden.',
            monitors=mock.Mock(return_value=[dict(index=0, label='Built-in display'),
                                             dict(index=1, label='Projector')]),
            apply=mock.Mock(), show=mock.Mock(), hide=mock.Mock(), toggle=mock.Mock())
        self.ui = SimpleNamespace(output=self.output, file_list_error=mock.Mock(),
                                  app=SimpleNamespace(quit=mock.Mock(),
                                                      handle_quit_shortcut=mock.Mock(return_value=False)))
        self.window = OutputWindow.__new__(OutputWindow)
        self.window.ui, self.window._refreshing, self.window.visible = self.ui, False, False
        for name in ('mode', 'monitor', 'fade_adjustment', 'fade_spin', 'fade_scale',
                     'status', 'apply_button', 'show_button', 'hide_button'):
            setattr(self.window, name, Widget())
        self.window.get_focus = lambda: None
        FILE_WINDOW._local_key.reset_mock()
        self.window.refresh()

    def test_primary_q_requests_shared_quit_before_local_output_editing(self):
        self.ui.app.handle_quit_shortcut.return_value = True
        self.window.fade_adjustment.value = 2.5
        self.window.get_focus = lambda: self.window.fade_spin
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
        self.assertEqual(self.window.fade_adjustment.value, 2.5)
        self.output.hide.assert_not_called()
        self.output.apply.assert_not_called()

    def test_plain_q_and_modified_variants_remain_local(self):
        for state in (0, GDK.ModifierType.SHIFT_MASK, GDK.ModifierType.MOD1_MASK,
                      GDK.ModifierType.META_MASK | GDK.ModifierType.SHIFT_MASK,
                      GDK.ModifierType.META_MASK | GDK.ModifierType.MOD1_MASK):
            event = SimpleNamespace(keyval=GDK.KEY_q, state=state)
            self.assertTrue(self.window._on_key_press(self.window, event))
            FILE_WINDOW._local_key.assert_called_with(self.window, event)
        self.ui.app.quit.assert_not_called()

    def test_refresh_loads_stable_display_ids_and_settings(self):
        self.assertEqual(self.window.monitor.options, [('0', 'Built-in display'), ('1', 'Projector')])
        self.assertEqual(self.window.monitor.value, '1')
        self.assertEqual(self.window.mode.value, 'overlay')
        self.assertEqual(self.window.fade_adjustment.value, 0.75)
        self.assertFalse(self.window._refreshing)

    def test_apply_updates_numeric_editor_and_accepts_both_duration_boundaries(self):
        for seconds in (0, 5):
            with self.subTest(seconds=seconds):
                self.output.apply.reset_mock()
                self.window.fade_adjustment.value = seconds
                self.assertTrue(self.window._apply())
                self.output.apply.assert_called_once_with('overlay', 1, seconds, 1.0)
        self.assertEqual(self.window.fade_spin.update.call_count, 2)
        self.ui.file_list_error.assert_not_called()

    def test_invalid_draft_never_reaches_controller(self):
        for attribute, values in (
                ('mode', [None, '', 'invalid']),
                ('monitor', [None, '', '-1', 'not a display']),
                ('fade_adjustment', [-0.01, 5.01, float('nan'), float('inf')])):
            for value in values:
                with self.subTest(attribute=attribute, value=value):
                    self.window.refresh()
                    self.output.apply.reset_mock()
                    self.ui.file_list_error.reset_mock()
                    getattr(self.window, attribute).value = value
                    self.assertFalse(self.window._apply())
                    self.output.apply.assert_not_called()
                    self.ui.file_list_error.assert_called_once()

    def test_disconnected_display_error_retains_draft(self):
        self.output.apply.side_effect = ValueError('Display disconnected.')
        self.window.fade_adjustment.value = 1.25
        self.assertFalse(self.window._apply())
        self.assertEqual(self.window.fade_adjustment.value, 1.25)
        self.ui.file_list_error.assert_called_once_with(self.output.apply.side_effect)

    def test_status_refresh_preserves_unapplied_edits(self):
        self.window.mode.value, self.window.monitor.value = 'window', '0'
        self.window.fade_adjustment.value = 1.5
        self.output.visible, self.output.status_text = True, 'Output is visible.'
        self.window.refresh_status()
        self.assertEqual(self.window.status.text, 'Output is visible.')
        self.assertFalse(self.window.show_button.sensitive)
        self.assertTrue(self.window.hide_button.sensitive)
        self.assertEqual(self.window.hide_button.text, 'Fade out')
        self.assertEqual(self.window.mode.value, 'window')
        self.assertEqual(self.window.monitor.value, '0')
        self.assertEqual(self.window.fade_adjustment.value, 1.5)

    def test_fade_controls_enabled_only_in_overlay(self):
        for mode, enabled in [('window', False), ('fullscreen', False), ('overlay', True)]:
            with self.subTest(mode=mode):
                self.window.mode.value = mode
                self.window._mode_changed()
                self.assertEqual(self.window.fade_spin.sensitive, enabled)
                self.assertEqual(self.window.fade_scale.sensitive, enabled)

    def test_show_uses_current_output_without_applying_draft(self):
        self.window.mode.value, self.window.fade_adjustment.value = 'window', 4.5
        self.assertTrue(self.window._show_clicked())
        self.output.show.assert_called_once_with()
        self.output.apply.assert_not_called()
        self.assertEqual(self.window.fade_adjustment.value, 4.5)

    def test_show_error_is_reported(self):
        self.output.show.side_effect = RuntimeError('Output unavailable.')
        self.assertFalse(self.window._show_clicked())
        self.ui.file_list_error.assert_called_once_with(self.output.show.side_effect)
        self.output.apply.assert_not_called()

    def test_hide_current_output_and_label_for_plain_window(self):
        self.output.mode, self.output.visible = 'window', True
        self.window._hide_clicked()
        self.output.hide.assert_called_once_with()
        self.assertEqual(self.window.hide_button.text, 'Hide')
        self.output.apply.assert_not_called()

    def test_escape_hides_only_settings(self):
        self.window.visible = True
        self.assertTrue(self.window._on_key_press(None, SimpleNamespace(keyval=GDK.KEY_Escape)))
        self.assertFalse(self.window.visible)
        self.output.hide.assert_not_called()
        self.output.apply.assert_not_called()
        FILE_WINDOW._local_key.assert_not_called()

    def test_other_keys_use_shared_local_widget_handler(self):
        event = SimpleNamespace(keyval=ord('l'))
        self.assertTrue(self.window._on_key_press(None, event))
        FILE_WINDOW._local_key.assert_called_once_with(self.window, event)
        self.output.toggle.assert_not_called()

    def test_button_key_release_is_consumed_without_second_activation(self):
        self.window.get_focus = lambda: self.window.apply_button
        for key in (GDK.KEY_Return, GDK.KEY_KP_Enter, GDK.KEY_space):
            with self.subTest(key=key):
                self.assertTrue(self.window._on_key_release(None, SimpleNamespace(keyval=key)))
        self.output.apply.assert_not_called()
        self.window.get_focus = lambda: None
        self.assertFalse(self.window._on_key_release(None, SimpleNamespace(keyval=GDK.KEY_Return)))

    def test_present_reloads_draft_and_close_preserves_audience_output(self):
        self.window.mode.value = 'window'
        self.window.present()
        self.assertTrue(self.window.visible)
        self.assertEqual(self.window.mode.value, 'overlay')
        self.assertTrue(self.window._on_delete())
        self.assertFalse(self.window.visible)
        self.output.hide.assert_not_called()
        self.window.cleanup()
        self.output.hide.assert_not_called()


if __name__ == '__main__':
    unittest.main()
