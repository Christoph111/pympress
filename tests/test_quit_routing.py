"""Keep the quit shortcut ahead of local Presenter input handlers."""

try:
    from . import _support
except ImportError:
    import _support

import ast
from types import SimpleNamespace
import unittest
from unittest import mock


class QuitRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = _support.SOURCE / 'pympress' / 'ui.py'
        source = ast.parse(path.read_text(encoding='utf-8'))
        ui = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'UI')
        method = next(node for node in ui.body if isinstance(node, ast.FunctionDef) and node.name == 'on_key_input')
        module = ast.Module(body=[method], type_ignores=[])
        namespace = {'Gdk': SimpleNamespace(KEY_Escape=27)}
        exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
        cls.on_key_input = namespace['on_key_input']

    def setUp(self):
        self.event = SimpleNamespace(keyval=113)
        self.window = object()
        self.handler = mock.Mock(return_value=True)
        self.dock = SimpleNamespace(docked=True, has_focus=mock.Mock(return_value=True),
                                    handle_key=mock.Mock(return_value=True))
        self.ui = SimpleNamespace(app=SimpleNamespace(handle_quit_shortcut=self.handler),
                                  p_win=self.window, file_list_window=SimpleNamespace(_dock=self.dock),
                                  annotations=SimpleNamespace(key_event=mock.Mock(return_value=False)),
                                  scribbler=SimpleNamespace(key_event=mock.Mock(return_value=False)))

    def test_quit_runs_before_docked_text_input(self):
        self.assertTrue(type(self).on_key_input(self.ui, self.window, self.event))
        self.handler.assert_called_once_with(self.event)
        self.dock.has_focus.assert_not_called()
        self.dock.handle_key.assert_not_called()
        self.ui.annotations.key_event.assert_not_called()

    def test_quit_runs_before_annotation_editor(self):
        self.ui.file_list_window = None
        self.assertTrue(type(self).on_key_input(self.ui, self.window, self.event))
        self.ui.annotations.key_event.assert_not_called()
        self.ui.scribbler.key_event.assert_not_called()

    def test_other_keys_remain_local_to_dock(self):
        self.handler.return_value = False
        self.assertTrue(type(self).on_key_input(self.ui, self.window, self.event))
        self.dock.handle_key.assert_called_once_with(self.event)

    def test_other_keys_can_reach_annotation_editor(self):
        self.handler.return_value = False
        self.ui.file_list_window = None
        self.ui.annotations.key_event.return_value = True
        self.assertTrue(type(self).on_key_input(self.ui, self.window, self.event))
        self.ui.annotations.key_event.assert_called_once_with(self.window, self.event)
        self.ui.scribbler.key_event.assert_not_called()


if __name__ == '__main__':
    unittest.main()
