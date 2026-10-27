#!/usr/bin/env python3
"""Exercise actual input callbacks without a display or OS file handlers.

Only the selected method definitions are extracted from UI/Pympress with AST;
GTK windows and actions are replaced by asynchronous-state test doubles.
"""

try:
    from . import _support
except ImportError:
    import _support


import ast
import enum
import importlib.util
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

SOURCE = _support.SOURCE


class WindowState(enum.IntFlag):
    FULLSCREEN = 1
    MAXIMIZED = 2


class Variant:
    def __init__(self, value):
        self.value = value

    def get_boolean(self):
        return bool(self.value)

    @classmethod
    def new_boolean(cls, value):
        return cls(bool(value))


class Action:
    def __init__(self, name, state=False):
        self.name = name
        self.state = state

    def get_name(self):
        return self.name

    def get_state(self):
        return Variant(self.state)

    def change_state(self, value):
        self.state = value.get_boolean()


class FakeApplication:
    def __init__(self, content=False, presenter=False):
        self.actions = {
            'content-fullscreen': Action('content-fullscreen', content),
            'presenter-fullscreen': Action('presenter-fullscreen', presenter),
        }

    def get_action_state(self, name):
        return self.actions[name].state

    def set_action_state(self, name, value):
        self.actions[name].state = value

    def lookup_action(self, name):
        return self.actions[name]


class FakeWindow:
    """Fullscreen transitions stay pending until a separate native event."""

    def __init__(self, state=0, realized=True):
        self.state = state
        self.realized = realized
        self.fullscreen = mock.Mock()
        self.unfullscreen = mock.Mock()
        self.fullscreen_on_monitor = mock.Mock()
        self.screen = types.SimpleNamespace(get_monitor_at_window=mock.Mock(return_value=0))

    def get_window(self):
        return self if self.realized else None

    def get_state(self):
        return self.state

    def get_screen(self):
        return self.screen


def extracted_class(path, class_name, method_names, globals_):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    original = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [node for node in original.body
               if isinstance(node, ast.FunctionDef) and node.name in method_names]
    missing = set(method_names) - {node.name for node in methods}
    if missing:
        raise ValueError('{} is missing methods {}'.format(path, ', '.join(sorted(missing))))
    node = ast.ClassDef(name=class_name, bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.Module(body=[node], type_ignores=[])
    namespace = dict(globals_)
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    return namespace[class_name]


def load_callbacks(source):
    filelist_path = source / 'pympress' / 'filelist.py'
    spec = importlib.util.spec_from_file_location('input_filelist_under_test', filelist_path)
    filelist = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = filelist
    spec.loader.exec_module(filelist)
    gtk = types.SimpleNamespace(drag_finish=mock.Mock())
    globals_ = {'Gtk': gtk, 'Gdk': types.SimpleNamespace(WindowState=WindowState),
                'GLib': types.SimpleNamespace(Variant=Variant, get_current_time=lambda: 12345,
                                              get_monotonic_time=mock.Mock(return_value=0)),
                'logger': logging.getLogger('input_callbacks_under_test'),
                'filelist': filelist, '_': lambda text: text}
    ui = extracted_class(source / 'pympress' / 'ui.py', 'UI',
                         {'cancel_current_input', 'exit_fullscreen', 'switch_fullscreen',
                          '_request_fullscreen', '_fullscreen_pending_target', 'on_window_state_event',
                          'file_list_drop', 'on_drag_drop', 'on_drop_request'}, globals_)
    app = extracted_class(source / 'pympress' / 'app.py', 'Pympress', {'do_open'}, globals_)
    return ui, app, gtk


class FullscreenInputTests(unittest.TestCase):
    ui_class = None

    def setUp(self):
        self.clock = self.ui_class._request_fullscreen.__globals__['GLib'].get_monotonic_time
        self.clock.reset_mock(return_value=True, side_effect=True)
        self.clock.return_value = 0

    def assert_screensaver_released(self, ui):
        self.assertTrue(ui.set_screensaver.called)
        call = ui.set_screensaver.call_args
        self.assertIs(call.kwargs.get('disabled', call.args[0] if call.args else None), False)

    def ui(self, c_native=0, p_native=0, c_action=False, p_action=False,
           c_realized=True, p_realized=True):
        ui = self.ui_class()
        ui.c_win = FakeWindow(c_native, c_realized)
        ui.p_win = FakeWindow(p_native, p_realized)
        ui.app = FakeApplication(c_action, p_action)
        ui.set_screensaver = mock.Mock()
        ui.transition = types.SimpleNamespace(cancel=mock.Mock())
        for name in ('page_number', 'est_time', 'zoom', 'scribbler', 'deck', 'annotations'):
            setattr(ui, name, types.SimpleNamespace(try_cancel=mock.Mock(return_value=False)))
        ui.autoplay = types.SimpleNamespace(stop_looping=mock.Mock(return_value=False))
        return ui

    def test_escape_exits_content_native_fullscreen_with_stale_false_action(self):
        ui = self.ui(c_native=WindowState.FULLSCREEN)
        self.assertTrue(ui.cancel_current_input(None))
        ui.c_win.unfullscreen.assert_called_once()
        ui.p_win.unfullscreen.assert_not_called()
        self.assertFalse(ui.app.get_action_state('content-fullscreen'))
        self.assert_screensaver_released(ui)

    def test_escape_exits_presenter_native_fullscreen_with_stale_false_action(self):
        ui = self.ui(p_native=WindowState.FULLSCREEN)
        self.assertTrue(ui.cancel_current_input(None))
        ui.p_win.unfullscreen.assert_called_once()
        ui.c_win.unfullscreen.assert_not_called()
        self.assertFalse(ui.app.get_action_state('presenter-fullscreen'))

    def test_escape_exits_both_windows(self):
        ui = self.ui(c_native=WindowState.FULLSCREEN, p_native=WindowState.FULLSCREEN,
                     c_action=True, p_action=True)
        self.assertTrue(ui.cancel_current_input(None))
        ui.c_win.unfullscreen.assert_called_once()
        ui.p_win.unfullscreen.assert_called_once()
        self.assertFalse(ui.app.get_action_state('content-fullscreen'))
        self.assertFalse(ui.app.get_action_state('presenter-fullscreen'))

    def test_escape_cancels_editor_or_mode_before_leaving_fullscreen(self):
        for name in ('page_number', 'est_time', 'zoom', 'scribbler', 'deck', 'annotations'):
            with self.subTest(input=name):
                ui = self.ui(c_native=WindowState.FULLSCREEN)
                getattr(ui, name).try_cancel.return_value = True
                self.assertTrue(ui.cancel_current_input(None))
                ui.c_win.unfullscreen.assert_not_called()
                ui.p_win.unfullscreen.assert_not_called()
                ui.set_screensaver.assert_not_called()

    def test_escape_stops_autoplay_before_leaving_fullscreen(self):
        ui = self.ui(c_native=WindowState.FULLSCREEN)
        ui.autoplay.stop_looping.return_value = True
        self.assertTrue(ui.cancel_current_input(None))
        ui.c_win.unfullscreen.assert_not_called()

    def test_escape_handles_pending_action_without_realized_native_window(self):
        ui = self.ui(c_action=True, p_action=True, c_realized=False, p_realized=False)
        self.assertTrue(ui.exit_fullscreen())
        ui.c_win.unfullscreen.assert_called_once()
        ui.p_win.unfullscreen.assert_called_once()
        self.assertFalse(ui.app.get_action_state('content-fullscreen'))
        self.assertFalse(ui.app.get_action_state('presenter-fullscreen'))

    def test_escape_does_not_unmaximize_normal_windows(self):
        ui = self.ui(c_native=WindowState.MAXIMIZED, p_native=WindowState.MAXIMIZED)
        self.assertFalse(ui.cancel_current_input(None))
        ui.c_win.unfullscreen.assert_not_called()
        ui.p_win.unfullscreen.assert_not_called()

    def test_escape_cancels_pending_fullscreen_request_before_native_event(self):
        ui = self.ui(c_action=True)
        self.assertTrue(ui.exit_fullscreen())
        ui.c_win.unfullscreen.assert_called_once()
        self.assertFalse(ui.app.get_action_state('content-fullscreen'))

    def test_toggle_native_fullscreen_off_synchronizes_false_action(self):
        ui = self.ui(c_native=WindowState.FULLSCREEN, c_action=False)
        action = ui.app.lookup_action('content-fullscreen')
        self.assertTrue(ui.switch_fullscreen(action, None))
        ui.c_win.unfullscreen.assert_called_once()
        ui.c_win.fullscreen.assert_not_called()
        self.assertFalse(action.state)
        self.assert_screensaver_released(ui)

    def test_toggle_normal_native_window_on_repairs_stale_true_action(self):
        ui = self.ui(p_action=True)
        action = ui.app.lookup_action('presenter-fullscreen')
        self.assertTrue(ui.switch_fullscreen(action, None))
        ui.p_win.fullscreen.assert_called_once()
        ui.p_win.unfullscreen.assert_not_called()
        self.assertTrue(action.state)

    def test_unrealized_normal_window_does_not_create_phantom_exit_request(self):
        ui = self.ui(c_realized=False)
        self.assertFalse(ui._request_fullscreen(ui.c_win, False))
        ui.c_win.unfullscreen.assert_not_called()
        self.assertIsNone(ui._fullscreen_pending_target(ui.c_win))

    def test_repeated_toggle_before_native_event_sends_one_entry_request(self):
        ui = self.ui()
        action = ui.app.lookup_action('content-fullscreen')
        ui.switch_fullscreen(action, None)
        ui.switch_fullscreen(action, None)
        ui.c_win.fullscreen.assert_called_once()
        ui.c_win.unfullscreen.assert_not_called()
        self.assertIs(ui._fullscreen_pending_target(ui.c_win), True)
        self.assertTrue(action.state)

    def test_failed_native_acknowledgement_allows_retry_at_monotonic_deadline(self):
        ui = self.ui()
        self.assertTrue(ui._request_fullscreen(ui.c_win, True))
        self.clock.return_value = 4999999
        self.assertFalse(ui._request_fullscreen(ui.c_win, True))
        self.clock.return_value = 5000000
        self.assertTrue(ui._request_fullscreen(ui.c_win, True))
        self.assertEqual(ui.c_win.fullscreen.call_count, 2)

    def test_matching_native_event_clears_only_that_windows_pending_request(self):
        ui = self.ui()
        ui._request_fullscreen(ui.c_win, True)
        ui._request_fullscreen(ui.p_win, True)
        ui.c_win.state = WindowState.FULLSCREEN
        event = types.SimpleNamespace(changed_mask=WindowState.FULLSCREEN,
                                      new_window_state=WindowState.FULLSCREEN)
        self.assertFalse(ui.on_window_state_event(ui.c_win, event))
        self.assertIsNone(ui._fullscreen_pending_target(ui.c_win))
        self.assertIs(ui._fullscreen_pending_target(ui.p_win), True)
        self.assertTrue(ui.app.get_action_state('content-fullscreen'))

    def test_normal_native_event_without_pending_request_is_supported(self):
        ui = self.ui()
        event = types.SimpleNamespace(changed_mask=WindowState.FULLSCREEN,
                                      new_window_state=WindowState.FULLSCREEN)
        self.assertFalse(ui.on_window_state_event(ui.c_win, event))
        self.assertTrue(ui.app.get_action_state('content-fullscreen'))
        ui.set_screensaver.assert_called_once_with(disabled=True)

    def test_settled_native_mode_and_monitor_do_not_restart_fullscreen(self):
        ui = self.ui(c_native=WindowState.FULLSCREEN)
        self.assertFalse(ui._request_fullscreen(ui.c_win, True, monitor=0))
        ui.c_win.fullscreen.assert_not_called()
        ui.c_win.fullscreen_on_monitor.assert_not_called()
        ui.transition.cancel.assert_not_called()

    def test_different_monitor_is_requested_once_while_pending(self):
        ui = self.ui(c_native=WindowState.FULLSCREEN)
        self.assertTrue(ui._request_fullscreen(ui.c_win, True, monitor=1))
        self.assertFalse(ui._request_fullscreen(ui.c_win, True, monitor=1))
        ui.c_win.fullscreen_on_monitor.assert_called_once_with(ui.c_win.screen, 1)

    def test_settled_windowed_state_does_not_issue_native_request(self):
        ui = self.ui()
        self.assertFalse(ui._request_fullscreen(ui.c_win, False))
        ui.c_win.unfullscreen.assert_not_called()
        self.assertIsNone(ui._fullscreen_pending_target(ui.c_win))

    def test_escape_cancels_pending_entry_before_native_fullscreen_exists(self):
        ui = self.ui()
        ui._request_fullscreen(ui.c_win, True)
        self.assertTrue(ui.exit_fullscreen())
        ui.c_win.unfullscreen.assert_called_once()
        self.assertIs(ui._fullscreen_pending_target(ui.c_win), False)
        self.assertFalse(ui.app.get_action_state('content-fullscreen'))

    def test_late_entry_event_retries_superseding_exit_instead_of_reentering(self):
        ui = self.ui()
        ui._request_fullscreen(ui.c_win, True)
        ui._request_fullscreen(ui.c_win, False)
        ui.c_win.state = WindowState.FULLSCREEN
        event = types.SimpleNamespace(changed_mask=WindowState.FULLSCREEN,
                                      new_window_state=WindowState.FULLSCREEN)
        ui.on_window_state_event(ui.c_win, event)
        self.assertEqual(ui.c_win.unfullscreen.call_count, 2)
        ui.c_win.fullscreen.assert_called_once()
        self.assertIs(ui._fullscreen_pending_target(ui.c_win), False)
        ui.c_win.state = 0
        event.new_window_state = 0
        ui.on_window_state_event(ui.c_win, event)
        self.assertIsNone(ui._fullscreen_pending_target(ui.c_win))
        self.assertFalse(ui.app.get_action_state('content-fullscreen'))

    def test_repeated_exit_request_is_ignored_until_acknowledged(self):
        ui = self.ui(c_native=WindowState.FULLSCREEN)
        self.assertTrue(ui._request_fullscreen(ui.c_win, False))
        self.assertFalse(ui._request_fullscreen(ui.c_win, False))
        ui.c_win.unfullscreen.assert_called_once()

    def test_native_failure_releases_pending_guard_for_immediate_retry(self):
        ui = self.ui()
        ui.c_win.fullscreen.side_effect = RuntimeError('native request rejected')
        with self.assertRaisesRegex(RuntimeError, 'native request rejected'):
            ui._request_fullscreen(ui.c_win, True)
        self.assertIsNone(ui._fullscreen_pending_target(ui.c_win))
        ui.c_win.fullscreen.side_effect = None
        self.assertTrue(ui._request_fullscreen(ui.c_win, True))
        self.assertEqual(ui.c_win.fullscreen.call_count, 2)

    def test_slide_fade_is_cancelled_before_one_native_mode_change(self):
        ui = self.ui()
        calls = []
        ui.transition.cancel.side_effect = lambda: calls.append('cancel slide fade')
        ui.c_win.fullscreen.side_effect = lambda: calls.append('enter native fullscreen')
        ui._request_fullscreen(ui.c_win, True)
        ui._request_fullscreen(ui.c_win, True)
        self.assertEqual(calls, ['cancel slide fade', 'enter native fullscreen'])


class DropInputTests(unittest.TestCase):
    ui_class = None
    gtk = None

    def setUp(self):
        self.gtk.drag_finish.reset_mock()
        self.temp = tempfile.TemporaryDirectory(prefix='pympress-drop-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.first = self.directory / 'Bühne mit Leerzeichen.pdf'
        self.second = self.directory / 'Next image.png'
        self.first.write_bytes(b'%PDF-1.4 inert fixture')
        self.second.write_bytes(b'inert image path fixture')

    def ui(self):
        ui = self.ui_class()
        ui.file_list_add_paths = mock.Mock(return_value=True)
        ui.file_list_error = mock.Mock()
        ui._file_list_error = mock.Mock()
        ui.show_file_list_error = mock.Mock()
        return ui

    def selection(self, uris=(), text=None):
        return types.SimpleNamespace(get_uris=mock.Mock(return_value=list(uris)),
                                     get_text=mock.Mock(return_value=text),
                                     get_length=mock.Mock(return_value=20))

    def test_drop_prefers_native_uri_list_and_preserves_all_file_order(self):
        ui = self.ui()
        data = self.selection([self.first.as_uri(), self.second.as_uri()], '/bad/fallback.pdf')
        self.assertTrue(ui.file_list_drop(data))
        paths = ui.file_list_add_paths.call_args.args[0]
        self.assertEqual([Path(path) for path in paths], [self.first, self.second])
        self.assertTrue(ui.file_list_add_paths.call_args.kwargs['activate'])

    def test_plain_text_fallback_accepts_multiple_local_paths(self):
        ui = self.ui()
        data = self.selection(text=str(self.first) + '\n' + str(self.second))
        self.assertTrue(ui.file_list_drop(data, activate=False))
        paths = ui.file_list_add_paths.call_args.args[0]
        self.assertEqual([Path(path) for path in paths], [self.first, self.second])
        self.assertFalse(ui.file_list_add_paths.call_args.kwargs['activate'])

    def test_empty_selection_does_not_append_or_activate(self):
        ui = self.ui()
        self.assertFalse(ui.file_list_drop(self.selection()))
        ui.file_list_add_paths.assert_not_called()

    def test_data_received_finishes_accepted_copy_once_without_deleting_source(self):
        ui = self.ui()
        ui.file_list_drop = mock.Mock(return_value=True)
        context, data = object(), self.selection([self.first.as_uri()])
        self.assertTrue(ui.on_drag_drop(object(), context, 0, 0, data, 0, 54321))
        self.gtk.drag_finish.assert_called_once_with(context, True, False, 54321)

    def test_data_received_finishes_rejected_drop_once_without_deleting_source(self):
        ui = self.ui()
        ui.file_list_drop = mock.Mock(return_value=False)
        context = object()
        self.assertFalse(ui.on_drag_drop(object(), context, 0, 0, self.selection(), 0, 54321))
        self.gtk.drag_finish.assert_called_once_with(context, False, False, 54321)

    def test_drop_request_fetches_supported_selection_before_finishing(self):
        ui = self.ui()
        target, context = object(), object()
        widget = types.SimpleNamespace(drag_dest_find_target=mock.Mock(return_value=target),
                                       drag_get_data=mock.Mock())
        self.assertTrue(ui.on_drop_request(widget, context, 0, 0, 54321))
        widget.drag_get_data.assert_called_once_with(context, target, 54321)
        self.gtk.drag_finish.assert_not_called()

    def test_drop_request_without_matching_target_does_not_request_data(self):
        ui = self.ui()
        widget = types.SimpleNamespace(drag_dest_find_target=mock.Mock(return_value=None),
                                       drag_get_data=mock.Mock())
        self.assertFalse(ui.on_drop_request(widget, object(), 0, 0, 54321))
        widget.drag_get_data.assert_not_called()


class MacOSOpenTests(unittest.TestCase):
    app_class = None

    def app(self):
        app = self.app_class()
        app.do_activate = mock.Mock()
        app.gui = types.SimpleNamespace(file_list_add_paths=mock.Mock())
        return app

    def test_open_retains_all_files_in_finder_order(self):
        app = self.app()
        uris = ['file:///tmp/first.pdf', 'file:///tmp/second.png', 'file:///tmp/third.pdf']
        files = [types.SimpleNamespace(get_uri=lambda uri=uri: uri) for uri in uris]
        app.do_open(files, len(files), '')
        app.do_activate.assert_called_once()
        app.gui.file_list_add_paths.assert_called_once_with(uris, activate=True)

    def test_open_honors_native_file_count(self):
        app = self.app()
        files = [types.SimpleNamespace(get_uri=lambda: 'file:///tmp/first.pdf'),
                 types.SimpleNamespace(get_uri=lambda: 'file:///tmp/unused.pdf')]
        app.do_open(files, 1, '')
        app.gui.file_list_add_paths.assert_called_once_with(['file:///tmp/first.pdf'], activate=True)

    def test_open_without_files_does_not_create_or_activate_ui(self):
        app = self.app()
        app.do_open([], 0, '')
        app.do_activate.assert_not_called()
        app.gui.file_list_add_paths.assert_not_called()


def setUpModule():
    ui, app, gtk = load_callbacks(SOURCE)
    FullscreenInputTests.ui_class = ui
    DropInputTests.ui_class, DropInputTests.gtk = ui, gtk
    MacOSOpenTests.app_class = app


if __name__ == '__main__':
    unittest.main(verbosity=2)
