#!/usr/bin/env python3
"""Focused integration regressions for list cancellation and image export.

Execute the actual UI callback bodies and actual FileList parser/model, replacing
GTK dialogs and the already-tested native PDF writer only.
"""

try:
    from . import _support
except ImportError:
    import _support


import ast
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import urlsplit
from urllib.request import url2pathname

from pympress import filelist

SOURCE = _support.SOURCE


def extract(name, path, methods, globals_):
    source = ast.parse(path.read_text(encoding='utf-8'))
    original = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == name)
    selected = [node for node in original.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    if {node.name for node in selected} != set(methods):
        raise AssertionError('Missing tested method')
    cls = ast.ClassDef(name=name, bases=[], keywords=[], body=selected, decorator_list=[])
    namespace = dict(globals_)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(path), 'exec'), namespace)
    return namespace[name]


Document = extract('Document', SOURCE / 'pympress/document.py', {'_local_path'},
                   {'pathlib': __import__('pathlib'), 'os': __import__('os'),
                    'urlsplit': urlsplit, 'url2pathname': url2pathname})
dialog = mock.Mock()
gtk = SimpleNamespace(MessageDialog=mock.Mock(return_value=dialog), STOCK_CLOSE='close',
                      DialogFlags=SimpleNamespace(MODAL=1), MessageType=SimpleNamespace(ERROR=1),
                      ResponseType=SimpleNamespace(CLOSE=1), WindowPosition=SimpleNamespace(CENTER=1))
UI = extract('UI', SOURCE / 'pympress/ui.py',
             {'_save_document', 'file_list_add_paths', 'file_list_activate', 'file_list_remove',
              'file_list_clear', 'file_list_load'},
             {'filelist': filelist, 'document': SimpleNamespace(Document=Document),
              'GLib': SimpleNamespace(Error=type('FakeGLibError', (Exception,), {})), 'Gtk': gtk,
              'logger': mock.Mock(spec=logging.Logger), '_': lambda value: value})


class Integration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pympress-integration-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.pdf = self.root / 'Talk.pdf'
        self.pdf.write_bytes(b'%PDF-inert-fixture')
        self.image = self.root / 'Portrait.jpg'
        self.image.write_bytes(b'inert-image-fixture')
        self.ui = UI()
        self.ui.p_win = object()
        self.ui.doc = SimpleNamespace(save_changes=mock.Mock(return_value=True))
        self.ui.file_list = filelist.FileList()
        self.ui.file_list.append(self.pdf)
        self.ui.file_list.set_current(0)
        self.ui.file_list_status = {'existing': 'status'}
        self.ui.file_list_error = mock.Mock()
        self.ui._refresh_file_list = mock.Mock()
        self.ui.swap_document = mock.Mock(return_value=True)
        self.ui.image_document = SimpleNamespace(source_path=self.image)

    def test_image_plain_save_uses_save_as_and_respects_cancel(self):
        self.ui.save_file_as = mock.Mock(return_value=False)
        self.assertFalse(self.ui._save_document())
        self.ui.save_file_as.assert_called_once()
        self.ui.doc.save_changes.assert_not_called()

    def test_image_rejects_source_nonlocal_and_non_pdf_exports(self):
        original = self.image.read_bytes()
        for uri in [self.image.as_uri(), (self.root / 'other.png').as_uri(),
                    'https://example.test/export.pdf', 'file://remote/tmp/export.pdf']:
            with self.subTest(uri=uri):
                self.assertFalse(self.ui._save_document(uri))
        self.ui.doc.save_changes.assert_not_called()
        self.assertEqual(self.image.read_bytes(), original)

    def test_image_rejects_a_source_symlink_alias(self):
        alias = self.root / 'alias.pdf'
        _support.symlink_or_skip(self, alias, self.image)
        original = self.image.read_bytes()
        self.assertFalse(self.ui._save_document(alias.as_uri()))
        self.ui.doc.save_changes.assert_not_called()
        self.assertEqual(self.image.read_bytes(), original)

    def test_image_allows_an_explicit_new_local_pdf(self):
        target = (self.root / 'export.pdf').as_uri()
        self.assertTrue(self.ui._save_document(target))
        self.ui.doc.save_changes.assert_called_once_with(target)

    def test_cancelled_list_replace_preserves_model_active_entry_and_status(self):
        replacement = filelist.FileList()
        replacement.append(self.image)
        target = self.root / 'replacement.json'
        replacement.save(target)
        existing = self.ui.file_list
        current = existing.current
        self.ui.swap_document.return_value = False
        self.assertFalse(self.ui.file_list_load(target))
        self.assertIs(self.ui.file_list, existing)
        self.assertIs(self.ui.file_list.current, current)
        self.assertEqual(self.ui.file_list_status, {'existing': 'status'})

    def test_missing_list_entries_remain_visible_without_opening(self):
        replacement = filelist.FileList()
        replacement.append(self.image)
        replacement.set_current(0)
        target = self.root / 'replacement.json'
        replacement.save(target)
        self.image.unlink()
        self.ui.file_list_activate = mock.Mock()
        self.assertTrue(self.ui.file_list_load(target))
        self.assertEqual(len(self.ui.file_list), 1)
        self.assertFalse(self.ui.file_list.entries[0].exists)
        self.assertEqual(self.ui.file_list.current_index, -1)
        self.ui.file_list_activate.assert_not_called()

    def test_malformed_list_does_not_close_the_current_document(self):
        target = self.root / 'bad.json'
        target.write_text('{"format": "wrong"}')
        existing = self.ui.file_list
        self.assertFalse(self.ui.file_list_load(target))
        self.assertIs(self.ui.file_list, existing)
        self.ui.swap_document.assert_not_called()

    def test_cancelled_remove_and_clear_keep_list_and_actual_files(self):
        self.ui.swap_document.return_value = False
        old_entries = self.ui.file_list.entries
        self.assertFalse(self.ui.file_list_remove([0]))
        self.assertEqual(self.ui.file_list.entries, old_entries)
        self.assertFalse(self.ui.file_list_clear())
        self.assertEqual(self.ui.file_list.entries, old_entries)
        self.assertTrue(self.pdf.exists())

    def test_cancelled_activation_preserves_current_without_false_error_status(self):
        entry = self.ui.file_list.append(self.image)[0]

        def cancel(*args):
            self.ui._open_cancelled = True
            return False
        self.ui.swap_document.side_effect = cancel
        self.assertFalse(self.ui.file_list_activate(1))
        self.assertEqual(self.ui.file_list.current_index, 0)
        self.assertNotIn(entry.id, self.ui.file_list_status)

    def test_failed_activation_preserves_current_and_marks_the_failed_entry(self):
        entry = self.ui.file_list.append(self.image)[0]
        self.ui.swap_document.return_value = False
        self.assertFalse(self.ui.file_list_activate(1))
        self.assertEqual(self.ui.file_list.current_index, 0)
        self.assertEqual(self.ui.file_list_status[entry.id], 'Could not open file')

    def test_native_colon_filename_round_trips_through_saved_relative_list(self):
        path = self.root / 'Talk: Intro.pdf'
        path.write_bytes(b'%PDF-inert-fixture')
        files = filelist.FileList()
        files.append(path)
        target = self.root / 'show.json'
        files.save(target)
        loaded = filelist.FileList.load(target)
        self.assertEqual(loaded.entries[0].path, path)
        self.assertTrue(loaded.entries[0].exists)


if __name__ == '__main__':
    unittest.main(verbosity=2)
