#!/usr/bin/env python3
"""Exercise actual document ownership, original-path saves and failure cleanup.

Only native Poppler objects are replaced. Private copies and atomic filesystem
operations use actual local files; real Windows handle sharing is smoke-tested
separately with the packaged native runtime.
"""

try:
    from . import _support
except ImportError:
    import _support


from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import weakref

from pympress import pdf_source
from test_hardening import load_document


SOURCE = _support.SOURCE


class Native:
    def get_n_pages(self):
        return 1

    def get_page(self, number):
        return SimpleNamespace(get_label=lambda: '1')


class NativeReference:
    def __init__(self, document):
        self.document = document


class DocumentLifetimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='pympress-document-lifetime-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.path = self.root / 'Original ä.pdf'
        self.original = b'%PDF-inert-original'
        self.path.write_bytes(self.original)
        self.module, _ = load_document(SOURCE)
        self.cls = self.module.Document
        self.native = Native()
        self.module.Poppler.Document = SimpleNamespace(new_from_file=mock.Mock(return_value=self.native))

    def snapshot(self):
        snapshot = pdf_source.prepare(self.path, enabled=True)
        self.addCleanup(snapshot.cleanup)
        return snapshot

    def test_create_loads_private_uri_but_preserves_original_identity(self):
        snapshot = self.snapshot()
        with mock.patch.object(self.module.pdf_source, 'prepare', return_value=snapshot):
            doc = self.cls.create(None, self.path.as_uri())
        self.assertEqual(doc.uri, self.path.as_uri())
        self.assertEqual(doc.path, self.path)
        self.assertEqual(doc.page_labels, ['1'])
        self.module.Poppler.Document.new_from_file.assert_called_once_with(snapshot.uri, None)
        doc.close()
        self.assertFalse(snapshot.path.exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_platform_without_snapshot_retains_original_native_uri(self):
        with mock.patch.object(self.module.pdf_source, 'prepare', return_value=None):
            doc = self.cls.create(None, self.path.as_uri())
        self.module.Poppler.Document.new_from_file.assert_called_once_with(self.path.as_uri(), None)
        doc.close()
        doc.close()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_close_releases_native_pages_and_annotations_even_if_wrappers_remain(self):
        native = Native()
        reference = weakref.ref(native)
        doc = self.cls(None, native, self.path.as_uri())
        page = SimpleNamespace(page=NativeReference(native), annotations=[NativeReference(native)])
        annotations = page.annotations
        doc.pages_cache[0] = page
        native = None
        doc.changes = True

        def cleanup():
            self.assertIsNone(doc.doc)
            self.assertIsNone(page.page)
            self.assertEqual(page.annotations, [])
            self.assertEqual(annotations, [])
            self.assertIsNone(reference())

        owner = SimpleNamespace(cleanup=mock.Mock(side_effect=cleanup))
        doc._pdf_source = owner
        doc.close()
        doc.close()
        self.assertEqual(doc.pages_cache, {})
        self.assertTrue(doc.changes)
        owner.cleanup.assert_called_once_with()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_snapshot_cleanup_can_retry_after_failure_without_native_references(self):
        doc = self.cls(None, self.native, self.path.as_uri())
        owner = SimpleNamespace(cleanup=mock.Mock(side_effect=[OSError('locked externally'), None]))
        doc._pdf_source = owner
        with self.assertRaises(OSError):
            doc.close()
        self.assertIsNone(doc.doc)
        self.assertIs(doc._pdf_source, owner)
        doc.close()
        doc.close()
        self.assertEqual(owner.cleanup.call_count, 2)
        self.assertIsNone(doc._pdf_source)

    def test_native_open_failure_removes_private_copy_and_preserves_source(self):
        snapshot = self.snapshot()
        self.module.Poppler.Document.new_from_file.side_effect = ValueError('invalid native PDF')
        with mock.patch.object(self.module.pdf_source, 'prepare', return_value=snapshot):
            with self.assertRaises(ValueError):
                self.cls.create(None, self.path.as_uri())
        self.assertFalse(snapshot.path.exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_metadata_failure_removes_private_copy_and_preserves_source(self):
        snapshot = self.snapshot()
        self.native.get_n_pages = mock.Mock(side_effect=ValueError('invalid page metadata'))
        with mock.patch.object(self.module.pdf_source, 'prepare', return_value=snapshot):
            with self.assertRaises(ValueError):
                self.cls.create(None, self.path.as_uri())
        self.assertFalse(snapshot.path.exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_constructor_failure_removes_the_already_prepared_private_copy(self):
        snapshot = self.snapshot()
        with mock.patch.object(self.module.pdf_source, 'prepare', return_value=snapshot), \
                mock.patch.object(self.cls, '__init__', side_effect=RuntimeError('cannot initialize callbacks')):
            with self.assertRaises(RuntimeError):
                self.cls.create(None, self.path.as_uri())
        self.assertFalse(snapshot.path.exists())
        self.module.Poppler.Document.new_from_file.assert_not_called()

    def test_snapshot_document_still_saves_atomically_to_original_path(self):
        snapshot = self.snapshot()

        def save(uri):
            self.cls._local_path(uri).write_bytes(b'%PDF-saved-changes')
            return True

        self.native.save = mock.Mock(side_effect=save)
        with mock.patch.object(self.module.pdf_source, 'prepare', return_value=snapshot):
            doc = self.cls.create(None, self.path.as_uri())
        doc.changes = True
        self.assertTrue(doc.save_changes())
        self.assertEqual(self.path.read_bytes(), b'%PDF-saved-changes')
        self.assertEqual(snapshot.path.read_bytes(), self.original)
        self.assertFalse(doc.changes)
        doc.close()

    def test_failed_atomic_replace_preserves_original_bytes_and_dirty_state(self):
        snapshot = self.snapshot()

        def save(uri):
            self.cls._local_path(uri).write_bytes(b'%PDF-unsaved-changes')
            return True

        self.native.save = mock.Mock(side_effect=save)
        with mock.patch.object(self.module.pdf_source, 'prepare', return_value=snapshot):
            doc = self.cls.create(None, self.path.as_uri())
        doc.changes = True
        with mock.patch.object(Path, 'replace', side_effect=PermissionError('external lock')):
            with self.assertRaises(PermissionError):
                doc.save_changes()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertTrue(doc.changes)
        self.assertEqual(list(self.root.glob('Original ä*.pdf')), [self.path])
        doc.close()

    def test_empty_document_close_is_safe_and_idempotent(self):
        doc = self.cls.create(None, None)
        placeholder = doc.page(-1)
        doc.close()
        doc.close()
        self.assertIsNone(placeholder.page)
        self.assertEqual(doc.pages_cache, {})
        self.assertIsNone(doc.doc)
        self.module.Poppler.Document.new_from_file.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
