#!/usr/bin/env python3
"""Exercise real list-window callbacks without opening a native GUI.

The real FileList model and FileListWindow methods run together. Only GTK's
widgets and selections are replaced; native widget construction is verified by
the bundle/UI smoke test rather than claimed by this test.
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


from pympress.filelist import FileList

SOURCE = _support.SOURCE


class TreePath:
    def __init__(self, indices):
        self.indices = indices

    def get_indices(self):
        return self.indices

    @classmethod
    def new_from_indices(cls, indices):
        return cls(indices)


GTK = SimpleNamespace(ApplicationWindow=object, TreePath=TreePath, drag_finish=lambda *args: None,
                      DirectionType=SimpleNamespace(TAB_BACKWARD='backward', TAB_FORWARD='forward'),
                      FileChooserAction=SimpleNamespace(SAVE='save'),
                      ResponseType=SimpleNamespace(ACCEPT='accept', CANCEL='cancel'))
GDK = SimpleNamespace(KEY_Tab=1, KEY_ISO_Left_Tab=2, KEY_Escape=3, KEY_Return=4, KEY_KP_Enter=5,
                      KEY_Delete=6, KEY_BackSpace=7, KEY_Up=8, KEY_Down=9,
                      KEY_v=ord('v'), KEY_V=ord('V'), KEY_space=ord(' '),
                      KEY_l=ord('l'), KEY_L=ord('L'), KEY_Left=10, KEY_Right=11,
                      ModifierType=SimpleNamespace(SHIFT_MASK=1, MOD1_MASK=2, CONTROL_MASK=4,
                                                   META_MASK=8, SUPER_MASK=16))
source = SOURCE / 'pympress/filelist_window.py'
class_nodes = [node for node in ast.parse(source.read_text(encoding='utf-8')).body
               if isinstance(node, ast.ClassDef) and node.name in {'FileListWindow', '_ClipboardText'}]
namespace = {'Gtk': GTK, 'Gdk': GDK}
exec(compile(ast.Module(body=class_nodes, type_ignores=[]), str(source), 'exec'), namespace)
FileListWindow = namespace['FileListWindow']


class Widget:
    def set_sensitive(self, value):
        self.sensitive = value

    def set_visible(self, value):
        self.visible = value

    def set_text(self, value):
        self.text = value

    def set_tooltip_text(self, value):
        self.tooltip = value


class Button:
    def __init__(self, sensitive=True, visible=True):
        self.sensitive = sensitive
        self.visible = visible
        self.clicks = 0

    def is_sensitive(self):
        return self.sensitive

    def get_visible(self):
        return self.visible

    def clicked(self):
        self.clicks += 1


GTK.Button = Button
GTK.Entry = type('Entry', (), {})
GTK.TextView = type('TextView', (), {})


class Store(list):
    def clear(self):
        super(Store, self).clear()
        self.selection.selected.clear()


class Selection:
    def __init__(self, store):
        self.store = store
        self.selected = set()

    def get_selected_rows(self):
        return self.store, [TreePath([index]) for index in sorted(self.selected)]

    def select_path(self, path):
        self.selected.add(path.get_indices()[0])


class FileListWindowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pympress-window-')
        self.directory = Path(self.temporary.name)
        files = [self.directory / name for name in ['A.pdf', 'Bild ä.png', 'C.pdf', 'D.pdf', 'E.pdf']]
        for path in files:
            path.write_bytes(b'fixture')
        model = FileList()
        model.append(files)
        self.ui = SimpleNamespace(file_list=model, file_list_status={})
        self.ui.file_list_move = lambda index, delta: model.move(index, index + delta)
        self.ui.file_list_activate = lambda index: model.set_current(index)
        self.ui.file_list_remove = lambda indices: [model.remove(index) for index in reversed(indices)]
        self.window = FileListWindow.__new__(FileListWindow)
        self.window.ui = self.ui
        self.window.get_focus = lambda: None
        self.window.propagate_key_event = lambda event: True
        self.window.store = Store()
        selection = Selection(self.window.store)
        self.window.store.selection = selection
        self.window.tree = SimpleNamespace(get_selection=lambda: selection, has_focus=lambda: True)
        for name in ('empty_label', 'summary', 'open_button', 'remove_button', 'up_button', 'down_button',
                     'clear_button', 'save_button', 'previous_button', 'next_button'):
            setattr(self.window, name, Widget())
        self.window.refresh()

    def tearDown(self):
        self.temporary.cleanup()

    def select(self, *indices):
        self.window.tree.get_selection().selected = set(indices)
        self.window._update_buttons()

    def selected_ids(self):
        return [self.window.store[index][self.window.ID] for index in self.window._selected_indices()]

    def test_block_moves_keep_order_selection_and_current_identity(self):
        self.ui.file_list.set_current(2)
        active = self.ui.file_list.current.id
        self.window.refresh()
        self.select(1, 2)
        selected = self.selected_ids()
        self.window._move_selected(-1)
        self.assertEqual([entry.name for entry in self.ui.file_list.entries],
                         ['Bild ä.png', 'C.pdf', 'A.pdf', 'D.pdf', 'E.pdf'])
        self.assertEqual(self.selected_ids(), selected)
        self.assertEqual(self.ui.file_list.current.id, active)
        self.assertEqual(self.ui.file_list.current_index, 1)
        self.window._move_selected(1)
        self.assertEqual([entry.name for entry in self.ui.file_list.entries],
                         ['A.pdf', 'Bild ä.png', 'C.pdf', 'D.pdf', 'E.pdf'])
        self.assertEqual(self.selected_ids(), selected)

    def test_nonadjacent_move_and_boundary_do_not_scramble_files(self):
        self.select(1, 3)
        self.window._move_selected(-1)
        expected = ['Bild ä.png', 'A.pdf', 'D.pdf', 'C.pdf', 'E.pdf']
        self.assertEqual([entry.name for entry in self.ui.file_list.entries], expected)
        self.window._move_selected(-1)
        self.assertEqual([entry.name for entry in self.ui.file_list.entries], expected)
        self.assertFalse(self.window.up_button.sensitive)

    def test_missing_and_failed_entries_remain_visible(self):
        missing = self.ui.file_list.entries[1]
        missing.path.unlink()
        failed = self.ui.file_list.entries[2]
        self.ui.file_list_status[failed.id] = 'Could not open the PDF'
        self.window.refresh()
        self.assertEqual(self.window.store[1][self.window.STATUS], 'Missing file')
        self.assertEqual(self.window.store[2][self.window.STATUS], 'Could not open the PDF')
        self.assertEqual(len(self.window.store), 5)
        self.assertEqual(self.window.store[1][self.window.KIND], 'Image')

    def test_failed_open_keeps_current_marker_and_selection(self):
        self.ui.file_list.set_current(0)
        self.window.refresh()
        self.select(3)
        self.ui.file_list_activate = lambda index: False
        self.window._open_selected()
        self.assertEqual(self.ui.file_list.current_index, 0)
        self.assertEqual([row[self.window.ACTIVE] for row in self.window.store], ['▶', '', '', '', ''])
        self.assertEqual(self.window._selected_indices(), [3])

    def test_multi_remove_only_changes_references(self):
        paths = [entry.path for entry in self.ui.file_list.entries]
        self.select(1, 3)
        self.window._remove_selected()
        self.assertEqual([entry.name for entry in self.ui.file_list.entries], ['A.pdf', 'C.pdf', 'E.pdf'])
        self.assertTrue(all(path.exists() for path in paths))

    def test_empty_list_and_playback_buttons(self):
        self.assertFalse(self.window.previous_button.sensitive)
        self.assertTrue(self.window.next_button.sensitive)
        self.ui.file_list.set_current(4)
        self.window.refresh()
        self.assertFalse(self.window.next_button.sensitive)
        self.assertTrue(self.window.previous_button.sensitive)
        self.assertIn('Active: 5/5', self.window.summary.text)
        self.ui.file_list.clear()
        self.window.refresh()
        self.assertTrue(self.window.empty_label.visible)
        self.assertFalse(self.window.open_button.sensitive)
        self.assertFalse(self.window.save_button.sensitive)
        self.assertIn('0 files', self.window.summary.text)

    def test_drop_appends_without_opening_and_finishes_exactly_once(self):
        calls = []
        self.ui.file_list_drop = lambda data, activate: calls.append((data, activate)) or True
        GTK.drag_finish = lambda *args: calls.append(args)
        self.window._on_drop(None, 'context', 0, 0, 'selection-data', 0, 123)
        self.assertEqual(calls, [('selection-data', False), ('context', True, False, 123)])

    def test_unexpected_drop_failure_still_releases_drag_context(self):
        calls = []

        def fail(data, activate):
            raise RuntimeError('fixture error')
        self.ui.file_list_drop = fail
        GTK.drag_finish = lambda *args: calls.append(args)
        with self.assertRaises(RuntimeError):
            self.window._on_drop(None, 'context', 0, 0, 'selection-data', 0, 456)
        self.assertEqual(calls, [('context', False, False, 456)])

    def test_text_input_never_reaches_presentation_accelerators(self):
        calls = []
        window = SimpleNamespace(propagate_key_event=lambda event: calls.append('text') or True,
                                 get_focus=lambda: None)
        event = SimpleNamespace(keyval=ord('q'), state=0)
        self.assertTrue(FileListWindow._local_key(window, event))
        self.assertEqual(calls, ['text'])

    def test_dialog_does_not_forward_a_key_twice(self):
        calls = []
        dialog = SimpleNamespace(propagate_key_event=lambda event: calls.append(event) or False,
                                 get_focus=lambda: None)
        event = SimpleNamespace(keyval=ord('x'), state=0)
        self.assertTrue(self.window._on_dialog_key_press(dialog, event))
        self.assertEqual(calls, [event])

    def test_focused_button_return_and_space_activate_once_without_presenter_keys(self):
        for key in (GDK.KEY_Return, GDK.KEY_KP_Enter, GDK.KEY_space):
            with self.subTest(key=key):
                button = Button()
                propagated = []
                window = SimpleNamespace(get_focus=lambda: button,
                                         propagate_key_event=lambda event: propagated.append(event))
                event = SimpleNamespace(keyval=key, state=0)
                self.assertTrue(FileListWindow._local_key(window, event))
                self.assertTrue(self.window._on_key_release(window, event))
                self.assertEqual(button.clicks, 1)
                self.assertEqual(propagated, [])

    def test_disabled_or_hidden_buttons_do_not_activate(self):
        for button in (Button(sensitive=False), Button(visible=False)):
            with self.subTest(button=button):
                window = SimpleNamespace(get_focus=lambda: button)
                event = SimpleNamespace(keyval=GDK.KEY_space, state=0)
                self.assertTrue(FileListWindow._local_key(window, event))
                self.assertEqual(button.clicks, 0)

    def test_modified_button_keys_do_not_activate(self):
        button = Button()
        window = SimpleNamespace(get_focus=lambda: button, propagate_key_event=lambda event: False)
        for modifier in (GDK.ModifierType.CONTROL_MASK, GDK.ModifierType.MOD1_MASK,
                         GDK.ModifierType.META_MASK, GDK.ModifierType.SUPER_MASK):
            event = SimpleNamespace(keyval=GDK.KEY_Return, state=modifier)
            self.assertTrue(FileListWindow._local_key(window, event))
        self.assertEqual(button.clicks, 0)

    def test_dialog_focused_cancel_button_activates_instead_of_default_accept(self):
        button = Button()
        responses = []
        dialog = SimpleNamespace(get_focus=lambda: button, response=responses.append,
                                 propagate_key_event=lambda event: False, get_filename=lambda: '/tmp/file.pdf')
        event = SimpleNamespace(keyval=GDK.KEY_Return, state=0)
        self.assertTrue(self.window._on_dialog_key_press(dialog, event))
        self.assertTrue(self.window._on_key_release(dialog, event))
        self.assertEqual(button.clicks, 1)
        self.assertEqual(responses, [])

    def test_clipboard_native_uris_use_the_same_validator_without_activation(self):
        calls = []
        data = SimpleNamespace(get_length=lambda: 24, get_uris=lambda: ['file:///tmp/A.pdf'])
        clipboard = SimpleNamespace(request_text=lambda callback: calls.append('unexpected fallback'))
        self.ui.file_list_drop = lambda selection, activate: calls.append((selection, activate)) or True
        self.window._on_clipboard_uris(clipboard, data)
        self.assertEqual(calls, [(data, False)])

    def test_clipboard_falls_back_to_decoded_text_with_shared_validation(self):
        calls = []
        text = '/tmp/Bild ä.png\n/tmp/A.pdf'
        data = SimpleNamespace(get_length=lambda: -1, get_uris=lambda: None)
        clipboard = SimpleNamespace(request_text=lambda callback: callback(None, text))

        def validate(selection, activate):
            calls.append((selection.get_uris(), selection.get_text(), selection.get_length(), activate))
            return True
        self.ui.file_list_drop = validate
        self.window._on_clipboard_uris(clipboard, data)
        self.assertEqual(calls, [(None, text, len(text.encode('utf-8')), False)])

    def test_empty_clipboard_does_not_modify_or_activate_a_file(self):
        calls = []
        self.ui.file_list_drop = lambda *args, **kwargs: calls.append('unexpected validation')
        original = self.ui.file_list.entries
        self.window._on_clipboard_text(None, None)
        self.assertEqual(calls, [])
        self.assertEqual(self.ui.file_list.entries, original)
        self.assertIn('no file paths', self.window.summary.text)

    def test_summary_translates_complete_templates_before_interpolating(self):
        messages = []

        def translate(message):
            messages.append(message)
            return 'TRANSLATED: ' + message

        def translate_plural(singular, plural, count):
            return translate(singular if count == 1 else plural)
        self.ui.file_list.set_current(2)
        with mock.patch.dict(namespace, {'_': translate, 'ngettext': translate_plural}):
            self.window.refresh()
        self.assertIn('{count} files · Active: {position}/{total} – {name}', messages)
        self.assertIn('TRANSLATED: 5 files · Active: 3/5 – C.pdf', self.window.summary.text)
        self.ui.file_list.clear()
        self.ui.file_list.append(self.directory / 'A.pdf')
        with mock.patch.dict(namespace, {'_': translate, 'ngettext': translate_plural}):
            self.window.refresh()
        self.assertIn('{count} file · No file from the list is active', messages)
        self.assertEqual(self.window.summary.text, 'TRANSLATED: 1 file · No file from the list is active')

    def test_command_and_control_v_consume_keys_before_presenter_accelerators(self):
        calls = []
        self.window._paste_clipboard = lambda: calls.append('paste')
        self.window.propagate_key_event = lambda event: calls.append('unexpected propagation')
        for modifier in (GDK.ModifierType.CONTROL_MASK, GDK.ModifierType.META_MASK,
                         GDK.ModifierType.SUPER_MASK):
            event = SimpleNamespace(keyval=GDK.KEY_v, state=modifier)
            self.assertTrue(self.window._on_key_press(None, event))
        self.assertEqual(calls, ['paste', 'paste', 'paste'])

    def test_shift_l_hides_l_toggles_laser_and_command_arrows_change_files(self):
        calls = []
        self.window.hide = lambda: calls.append('hide')
        self.ui.app = SimpleNamespace(activate_action=lambda *args: calls.append(args))
        self.ui.file_list_previous = lambda: calls.append('previous')
        self.ui.file_list_next = lambda: calls.append('next')
        for key, modifiers in [(GDK.KEY_L, GDK.ModifierType.SHIFT_MASK), (GDK.KEY_l, 0),
                               (GDK.KEY_Left, GDK.ModifierType.META_MASK),
                               (GDK.KEY_Right, GDK.ModifierType.META_MASK)]:
            self.assertTrue(self.window._on_key_press(None, SimpleNamespace(keyval=key, state=modifiers)))
        self.assertEqual(calls, ['hide', ('pointer-mode', 'toggle'), 'previous', 'next'])

    def test_list_shortcuts_leave_text_input_untouched(self):
        self.window.get_focus = lambda: GTK.Entry()
        self.ui.app = SimpleNamespace(activate_action=lambda *args: self.fail('Laser while typing'))
        self.ui.file_list_next = lambda: self.fail('Next file while editing text')
        for key, modifiers in [(GDK.KEY_L, GDK.ModifierType.SHIFT_MASK), (GDK.KEY_l, 0),
                               (GDK.KEY_Right, GDK.ModifierType.META_MASK)]:
            self.assertTrue(self.window._on_key_press(None, SimpleNamespace(keyval=key, state=modifiers)))

    def test_primary_arrows_change_current_file_in_all_four_directions(self):
        model = self.ui.file_list
        self.ui.file_list_previous = lambda: model.set_current(model.current_index - 1)
        self.ui.file_list_next = lambda: model.set_current(model.current_index + 1)
        self.window.propagate_key_event = lambda event: self.fail('File shortcut reached local editing')
        for modifier in (GDK.ModifierType.CONTROL_MASK, GDK.ModifierType.META_MASK, GDK.ModifierType.SUPER_MASK):
            for key, expected in ((GDK.KEY_Left, 1), (GDK.KEY_Up, 1), (GDK.KEY_Right, 3), (GDK.KEY_Down, 3)):
                with self.subTest(modifier=modifier, key=key):
                    model.set_current(2)
                    self.assertTrue(self.window._on_key_press(None, SimpleNamespace(keyval=key, state=modifier)))
                    self.assertEqual(model.current_index, expected)
                    self.assertEqual(self.window.store[expected][self.window.ACTIVE], '▶')

    def test_primary_arrows_preserve_entry_and_text_view_editing(self):
        self.ui.file_list_previous = lambda: self.fail('Previous file while editing text')
        self.ui.file_list_next = lambda: self.fail('Next file while editing text')
        forwarded = []
        self.window.propagate_key_event = lambda event: forwarded.append(event) or True
        self.window.tree.has_focus = lambda: False
        for input_type in (GTK.Entry, GTK.TextView):
            self.window.get_focus = input_type
            for modifier in (GDK.ModifierType.CONTROL_MASK, GDK.ModifierType.META_MASK, GDK.ModifierType.SUPER_MASK):
                for key in (GDK.KEY_Left, GDK.KEY_Up, GDK.KEY_Right, GDK.KEY_Down):
                    with self.subTest(input_type=input_type, modifier=modifier, key=key):
                        event = SimpleNamespace(keyval=key, state=modifier)
                        self.assertTrue(self.window._on_key_press(None, event))
                        self.assertIs(forwarded[-1], event)
        self.assertEqual(len(forwarded), 24)

    def test_general_presentation_letters_remain_local(self):
        self.ui.app = SimpleNamespace(activate_action=lambda *args: self.fail('Global presentation shortcut'))
        self.window.propagate_key_event = lambda event: False
        before = self.ui.file_list.current_index
        for letter in 'pqfr':
            self.assertTrue(self.window._on_key_press(None, SimpleNamespace(keyval=ord(letter), state=0)))
        self.assertEqual(self.ui.file_list.current_index, before)

    def test_save_keeps_path_covered_by_native_overwrite_confirmation(self):
        calls = []
        dialog = SimpleNamespace(set_do_overwrite_confirmation=lambda value: calls.append(('confirm', value)),
                                 set_current_name=lambda value: None, run=lambda: GTK.ResponseType.ACCEPT,
                                 get_filename=lambda: '/tmp/user-chosen-name',
                                 destroy=lambda: calls.append(('destroy',)))
        self.window._chooser = lambda *args: dialog
        self.window._list_filter = lambda dialog: None
        self.ui.file_list_save = lambda path: calls.append(('save', path))
        self.window._save_list()
        self.assertEqual(calls, [('confirm', True), ('save', '/tmp/user-chosen-name'), ('destroy',)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
