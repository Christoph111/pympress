#!/usr/bin/env python3
"""Actual presenter-control state and layout operations, without native windows."""

try:
    from . import _support
except ImportError:
    import _support

import ast
import configparser
import copy
import json
from types import SimpleNamespace
import unittest
from unittest import mock

SOURCE = _support.SOURCE


source = SOURCE / 'pympress/presenter_tools.py'


class Context:
    def __init__(self):
        self.providers = []

    def add_provider(self, provider, priority):
        self.providers.append(provider)

    def remove_provider(self, provider):
        self.providers.remove(provider)


class Widget:
    def __init__(self):
        self.context = Context()

    def get_style_context(self):
        return self.context

    def set_no_show_all(self, value):
        self.no_show_all = value

    def set_visible(self, value):
        self.visible = value

    def set_active(self, value):
        self.active = value

    def set_value(self, value):
        self.value = value


class Provider:
    def load_from_data(self, data):
        self.data = data


class Bottom:
    def __init__(self, children):
        self.children = children

    def get_children(self):
        return list(self.children)

    def reorder_child(self, child, position):
        self.children.remove(child)
        self.children.insert(position, child)


GTK = SimpleNamespace(CssProvider=Provider, STYLE_PROVIDER_PRIORITY_APPLICATION=600, drag_finish=mock.Mock())
nodes = [node for node in ast.parse(source.read_text(encoding='utf-8')).body
         if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.Assign))]
namespace = {'Gtk': GTK, 'Gdk': SimpleNamespace(), 'copy': copy, 'json': json}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
PresenterTools = namespace['PresenterTools']
swapped_layout = namespace['swapped_layout']
FIELDS, INFO_FIELDS = namespace['FIELDS'], namespace['INFO_FIELDS']


class PresenterToolsTests(unittest.TestCase):
    def setUp(self):
        config = configparser.ConfigParser()
        config.add_section('presenter')
        layout = {'orientation': 'horizontal', 'resizeable': True, 'proportions': [0.7, 0.3],
                  'children': ['current', {'orientation': 'vertical', 'resizeable': True,
                                           'children': ['next', 'annotations']}]}
        config.get_layout = lambda name: layout
        config.update_layout_tree = mock.Mock()
        self.tools = PresenterTools.__new__(PresenterTools)
        self.tools.ui = SimpleNamespace(config=config, show_annotations=True,
                                        app=SimpleNamespace(set_action_state=mock.Mock()),
                                        p_win=SimpleNamespace(queue_resize=mock.Mock()),
                                        layout_name=lambda mode: 'plain', notes_mode=False, load_layout=mock.Mock())
        self.tools.fields = {key: Widget() for key, *_ in FIELDS}
        self.tools.checks = {key: Widget() for key in self.tools.fields}
        self.tools.spins = {key: Widget() for key in self.tools.fields}
        self.tools._scales, self.tools._providers = {}, {}
        self.tools._refreshing = False
        self.tools.editing = False
        self.tools.refresh()
        self.nav1, self.nav2 = object(), object()
        self.bottom = Bottom([self.nav1, self.tools.fields['page'], self.nav2,
                              *[self.tools.fields[key] for key in INFO_FIELDS[1:]]])
        self.tools.ui.get_object = lambda name: self.bottom

    def test_nested_leaf_swap_keeps_sizes_and_original_tree(self):
        original = self.tools.ui.config.get_layout('plain')
        snapshot = copy.deepcopy(original)
        result = swapped_layout(original, 'current', 'next')
        self.assertEqual(original, snapshot)
        self.assertEqual(result['children'][0], 'next')
        self.assertEqual(result['children'][1]['children'][0], 'current')
        self.assertEqual(result['proportions'], [0.7, 0.3])

    def test_nonexisting_or_same_leaf_is_rejected(self):
        for source, dest in [('notes', 'current'), ('current', 'current'), ('https://remote', 'current')]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                swapped_layout(self.tools.ui.config.get_layout('plain'), source, dest)

    def test_direct_layout_moves_require_edit_mode_and_keep_groups(self):
        self.assertFalse(self.tools.move_field('current', 'next'))
        self.tools.editing = True
        self.assertTrue(self.tools.move_field('current', 'next'))
        call = self.tools.ui.config.update_layout_tree.call_args.args
        self.assertEqual(call[0], 'plain')
        self.assertEqual(call[1]['children'][0], 'next')
        self.tools.ui.load_layout.assert_called_once_with(None)
        self.assertFalse(self.tools.move_field('current', 'clock'))

    def test_hidden_fields_stay_hidden_after_refresh_and_persist(self):
        self.tools.set_field_visible('current', False)
        field = self.tools.fields['current']
        self.assertTrue(field.no_show_all)
        self.assertFalse(field.visible)
        field.set_visible(True)
        self.tools.refresh()
        self.assertFalse(field.visible)
        self.assertFalse(self.tools.ui.config.getboolean('presenter', 'show_current'))
        self.tools.set_field_visible('current', True)
        self.assertTrue(field.visible)
        self.assertFalse(field.no_show_all)

    def test_annotations_follow_existing_action_and_can_all_be_restored(self):
        self.tools.set_field_visible('annotations', False)
        self.assertFalse(self.tools.ui.show_annotations)
        self.tools.ui.app.set_action_state.assert_called_with('annotations', False)
        self.tools._show_all_fields()
        self.assertTrue(all(field.visible for field in self.tools.fields.values()))
        self.tools.ui.app.set_action_state.assert_called_with('annotations', True)

    def test_field_font_scaling_is_bounded_and_providers_do_not_accumulate(self):
        self.tools.set_field_scale('clock', 900)
        self.assertEqual(self.tools.spins['clock'].value, 200)
        self.assertIn(b'20.00pt', self.tools._providers['clock'].data)
        self.tools.set_field_scale('clock', -10)
        self.assertEqual(self.tools.spins['clock'].value, 70)
        self.assertEqual(len(self.tools.fields['clock'].context.providers), 1)

    def test_info_fields_reorder_and_saved_order_restores(self):
        self.tools.editing = True
        self.assertTrue(self.tools.move_field('clock', 'page'))
        stored = self.tools.ui.config.get('presenter', 'info_field_order')
        self.assertEqual(json.loads(stored), ['clock', 'page', 'timer', 'estimate'])
        self.bottom.children = [self.nav1, self.tools.fields['page'], self.nav2,
                                *[self.tools.fields[key] for key in INFO_FIELDS[1:]]]
        self.tools._restore_info_order()
        self.assertIs(self.bottom.children[0], self.nav1)
        self.assertIs(self.bottom.children[2], self.nav2)
        fields = [key for child in self.bottom.children for key in INFO_FIELDS if self.tools.fields[key] is child]
        self.assertEqual(fields, ['clock', 'page', 'timer', 'estimate'])

    def test_malformed_info_order_does_not_move_fields(self):
        before = self.bottom.children[:]
        for value in ['{}', '["clock"]', '[[],[],[],[]]', 'bad json']:
            self.tools.ui.config.set('presenter', 'info_field_order', value)
            self.tools._restore_info_order()
            self.assertEqual(self.bottom.children, before)

    def test_private_drag_rejects_bad_data_and_finishes_without_deleting(self):
        GTK.drag_finish.reset_mock()
        self.tools.editing = True
        data = SimpleNamespace(get_length=lambda: 999, get_data=lambda: b'https://remote')
        self.tools._drag_received(None, 'context', 0, 0, data, 0, 123, 'current')
        GTK.drag_finish.assert_called_once_with('context', False, False, 123)


if __name__ == '__main__':
    unittest.main(verbosity=2)
