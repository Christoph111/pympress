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
import logging
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

    def get_window(self):
        return self if self.realized else None

    def get_state(self):
        return self.state


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
    gtk = types.SimpleNamespace(drag_finish=mock.Mock())
    globals_ = {'Gtk': gtk, 'Gdk': types.SimpleNamespace(WindowState=WindowState),
                'GLib': types.SimpleNamespace(Variant=Variant),
                'logger': logging.getLogger('fullscreen_inputs'), '_': lambda text: text}
    ui = extracted_class(source / 'pympress' / 'ui.py', 'UI',
                         {'cancel_current_input', 'exit_fullscreen', 'switch_fullscreen'}, globals_)
    return ui, None, gtk


class FullscreenInputTests(unittest.TestCase):
    ui_class = None

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






def setUpModule():
    ui, app, gtk = load_callbacks(SOURCE)
    FullscreenInputTests.ui_class = ui


if __name__ == '__main__':
    unittest.main(verbosity=2)
