"""Keep reserved file arrows unambiguous across platform modifier mappings."""

try:
    from . import _support
except ImportError:
    import _support

import ast
import configparser
from copy import deepcopy
import re
from types import SimpleNamespace
import unittest


def registration_method(gtk):
    """Load the actual shortcut registration method without constructing windows."""
    path = _support.SOURCE / 'pympress' / 'ui.py'
    source = ast.parse(path.read_text(encoding='utf-8'))
    ui = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'UI')
    method = next(node for node in ui.body if isinstance(node, ast.FunctionDef) and node.name == 'register_shortcuts')
    namespace = {'Gtk': gtk}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(path), 'exec'), namespace)
    return namespace['register_shortcuts']


class Accelerators:
    """Resolve platform Primary and GTK's Control aliases at the native boundary."""

    def __init__(self, primary):
        self.primary = primary
        self.registered = {}

    def accelerator_parse(self, value):
        aliases = {'ctrl': 'control', 'primary': self.primary}
        modifiers = frozenset(aliases.get(modifier.lower(), modifier.lower())
                              for modifier in re.findall(r'<([^>]+)>', value))
        return re.sub(r'<[^>]+>', '', value).lower(), modifiers

    def set_accels_for_action(self, action, keys):
        self.registered[action] = list(keys)

    def actions(self, accelerator):
        parsed = self.accelerator_parse(accelerator)
        return [action for action, keys in self.registered.items()
                for key in keys if self.accelerator_parse(key) == parsed]


class ShortcutRegistrationTests(unittest.TestCase):
    def setUp(self):
        defaults = configparser.ConfigParser(delimiters=('=',))
        defaults.read(_support.SOURCE / 'pympress' / 'share' / 'defaults.conf', encoding='utf-8')
        self.shortcuts = {action: keys.split() for action, keys in defaults.items('shortcuts')}

    def register(self, primary):
        native = Accelerators(primary)
        config = SimpleNamespace(shortcuts=self.shortcuts)
        registration_method(native)(SimpleNamespace(config=config, app=native))
        return native

    def test_windows_primary_arrows_have_exactly_one_file_action(self):
        native = self.register('control')
        for direction, action in (('Right', 'next-file'), ('Down', 'next-file'),
                                  ('Left', 'previous-file'), ('Up', 'previous-file')):
            with self.subTest(direction=direction):
                self.assertEqual(native.actions('<Control>' + direction), ['app.' + action])
        self.assertEqual(native.actions('<Control>Page_Down'), ['app.next-label'])
        self.assertEqual(native.actions('<Control>space'), ['app.next-label'])
        self.assertEqual(native.actions('<Control>Page_Up'), ['app.prev-label'])

    def test_macos_control_arrows_remain_label_shortcuts(self):
        native = self.register('meta')
        for direction, label, file_action in (('Right', 'next-label', 'next-file'),
                                              ('Down', 'next-label', 'next-file'),
                                              ('Left', 'prev-label', 'previous-file'),
                                              ('Up', 'prev-label', 'previous-file')):
            with self.subTest(direction=direction):
                self.assertEqual(native.actions('<Control>' + direction), ['app.' + label])
                self.assertEqual(native.actions('<Meta>' + direction), ['app.' + file_action])

    def test_old_custom_bindings_keep_unreserved_keys_and_saved_configuration(self):
        self.shortcuts['next-file'] = ['F6', '<Control>Left']
        self.shortcuts.pop('previous-file')
        self.shortcuts['zoom'] = ['<ctrl>Right', 'F7']
        self.shortcuts['quit'] = ['<control>Down', '<alt>q']
        self.shortcuts['custom-action'] = ['<Meta>Up', '<Shift>x']
        before = deepcopy(self.shortcuts)
        native = self.register('control')
        self.assertEqual(self.shortcuts, before)
        self.assertEqual(native.actions('<Control>Right'), ['app.next-file'])
        self.assertEqual(native.actions('<Control>Down'), ['app.next-file'])
        self.assertEqual(native.actions('<Control>Left'), ['app.previous-file'])
        self.assertEqual(native.actions('<Meta>Up'), ['app.previous-file'])
        self.assertEqual(native.actions('F6'), ['app.next-file'])
        self.assertEqual(native.actions('F7'), ['app.zoom'])
        self.assertEqual(native.actions('<alt>q'), ['app.quit'])
        self.assertEqual(native.actions('<Shift>x'), ['app.custom-action'])
        self.assertEqual(native.actions('<Control>q'), ['app.quit'])
        self.assertNotIn('app.highlight-hold-to-erase', native.registered)

    def test_reregistration_removes_old_conflicting_native_bindings(self):
        native = Accelerators('control')
        for action, keys in self.shortcuts.items():
            native.set_accels_for_action('app.' + action, keys)
        self.assertEqual(native.actions('<Control>Right'), ['app.next-label', 'app.next-file'])
        registration_method(native)(SimpleNamespace(config=SimpleNamespace(shortcuts=self.shortcuts), app=native))
        self.assertEqual(native.actions('<Control>Right'), ['app.next-file'])


if __name__ == '__main__':
    unittest.main()
