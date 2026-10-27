"""Keep docked file-list key handling separate from presentation shortcuts."""
try:
    from . import _support
except ImportError:
    import _support

import configparser
from types import SimpleNamespace
import unittest
from unittest import mock

from test_presentation_inputs import extracted_class

GDK = SimpleNamespace(KEY_Escape=27,
                      EventType=SimpleNamespace(KEY_PRESS=1, KEY_RELEASE=2, BUTTON_PRESS=3, BUTTON_RELEASE=4))
UI = extracted_class(_support.SOURCE / 'pympress/ui.py', 'UI',
                     {'on_key_input', '_restore_file_list_window', 'track_clicks'}, {'Gdk': GDK})


class DockedInputTests(unittest.TestCase):
    def setUp(self):
        self.ui = UI()
        self.ui.p_win, self.ui.c_win = object(), object()
        self.dock = SimpleNamespace(docked=True, has_focus=mock.Mock(return_value=True),
                                    handle_key=mock.Mock(return_value=True))
        self.ui.file_list_window = SimpleNamespace(_dock=self.dock)
        self.ui.cancel_current_input = mock.Mock(return_value=True)
        self.ui.annotations = SimpleNamespace(key_event=mock.Mock(return_value=False))
        self.ui.scribbler = SimpleNamespace(key_event=mock.Mock(return_value=False))

    def event(self, key=32, phase=1):
        return SimpleNamespace(keyval=key, type=phase)

    def test_floating_list_focus_does_not_capture_presenter_keys(self):
        self.dock.docked = False
        self.assertFalse(self.ui.on_key_input(self.ui.p_win, self.event()))
        self.dock.has_focus.assert_not_called()
        self.dock.handle_key.assert_not_called()

    def test_docked_escape_hides_list_before_global_presentation_cancellation(self):
        event = self.event(27)
        self.assertTrue(self.ui.on_key_input(self.ui.p_win, event))
        self.dock.handle_key.assert_called_once_with(event)
        self.ui.cancel_current_input.assert_not_called()
        self.ui.annotations.key_event.assert_not_called()

    def test_key_release_is_dispatched_once_to_the_docked_view(self):
        event = self.event(13, GDK.EventType.KEY_RELEASE)
        self.assertTrue(self.ui.on_key_input(self.ui.p_win, event))
        self.dock.handle_key.assert_called_once_with(event)
        self.ui.cancel_current_input.assert_not_called()

    def test_focus_outside_the_dock_keeps_global_escape_behavior(self):
        self.dock.has_focus.return_value = False
        self.assertTrue(self.ui.on_key_input(self.ui.p_win, self.event(27)))
        self.ui.cancel_current_input.assert_called_once_with(None)
        self.dock.handle_key.assert_not_called()

    def test_audience_keys_bypass_remembered_presenter_dock_focus(self):
        for phase in (GDK.EventType.KEY_PRESS, GDK.EventType.KEY_RELEASE):
            with self.subTest(phase=phase):
                self.assertFalse(self.ui.on_key_input(self.ui.c_win, self.event(32, phase)))
        self.dock.has_focus.assert_not_called()
        self.dock.handle_key.assert_not_called()

    def test_audience_escape_cancels_output_instead_of_hiding_docked_list(self):
        self.assertTrue(self.ui.on_key_input(self.ui.c_win, self.event(27)))
        self.ui.cancel_current_input.assert_called_once_with(None)
        self.dock.has_focus.assert_not_called()
        self.dock.handle_key.assert_not_called()


class DockPreviewFocusTests(unittest.TestCase):
    def setUp(self):
        self.ui = UI()
        self.ui.p_win = SimpleNamespace(set_focus=mock.Mock())
        self.ui.c_win = object()
        dock = SimpleNamespace(docked=True, has_focus=mock.Mock(return_value=True))
        self.ui.file_list_window = SimpleNamespace(_dock=dock)
        self.ui.laser = SimpleNamespace(toggle_pointer=mock.Mock(return_value=False))
        self.ui.zoom = SimpleNamespace(toggle_zoom_target=mock.Mock(return_value=False))
        self.ui.scribbler = SimpleNamespace(toggle_scribble=mock.Mock(return_value=False))
        self.ui.click_link = mock.Mock(return_value=False)

    def test_clicking_presenter_preview_releases_dock_keyboard_focus(self):
        widget = SimpleNamespace(get_toplevel=lambda: self.ui.p_win)
        self.ui.track_clicks(widget, SimpleNamespace(type=GDK.EventType.BUTTON_PRESS))
        self.ui.p_win.set_focus.assert_called_once_with(None)

    def test_audience_click_does_not_change_presenter_focus(self):
        widget = SimpleNamespace(get_toplevel=lambda: self.ui.c_win)
        self.ui.track_clicks(widget, SimpleNamespace(type=GDK.EventType.BUTTON_PRESS))
        self.ui.p_win.set_focus.assert_not_called()

    def test_release_does_not_clear_new_focus(self):
        widget = SimpleNamespace(get_toplevel=lambda: self.ui.p_win)
        self.ui.track_clicks(widget, SimpleNamespace(type=GDK.EventType.BUTTON_RELEASE))
        self.ui.p_win.set_focus.assert_not_called()


class FileListStartupTests(unittest.TestCase):
    def setUp(self):
        self.ui = UI()
        self.ui.file_list_window = None
        self.ui.config = configparser.ConfigParser()
        self.ui.config.add_section('presenter')
        self.view = SimpleNamespace(_ensure_dock=mock.Mock(), present=mock.Mock())
        self.factory = mock.Mock(return_value=self.view)

    def restore(self, mode=None, visible=None):
        if mode is not None:
            self.ui.config.set('presenter', 'file_list_docked', mode)
        if visible is not None:
            self.ui.config.set('presenter', 'file_list_visible', visible)
        module = SimpleNamespace(FileListWindow=self.factory)
        with mock.patch.dict('sys.modules', {'pympress.filelist_window': module}):
            self.ui._restore_file_list_window()

    def test_visible_independent_window_is_restored(self):
        self.restore('off', 'on')
        self.factory.assert_called_once_with(self.ui)
        self.view._ensure_dock.assert_called_once_with()
        self.view.present.assert_called_once_with()
        self.assertIs(self.ui.file_list_window, self.view)

    def test_visible_dock_is_restored(self):
        self.restore('on', 'on')
        self.view._ensure_dock.assert_called_once_with()
        self.view.present.assert_called_once_with()

    def test_hidden_dock_restores_mode_without_opening(self):
        self.restore('on', 'off')
        self.view._ensure_dock.assert_called_once_with()
        self.view.present.assert_not_called()

    def test_hidden_independent_window_is_not_created(self):
        self.restore('off', 'off')
        self.factory.assert_not_called()
        self.assertIsNone(self.ui.file_list_window)

    def test_missing_preferences_do_not_open_an_unrequested_window(self):
        self.restore()
        self.factory.assert_not_called()

    def test_invalid_preferences_do_not_break_startup_or_open_a_window(self):
        self.restore('unknown', 'on')
        self.factory.assert_not_called()
