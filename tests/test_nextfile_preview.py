"""Check passive next-file preview ownership, failures, crops and local-only input."""

try:
    from . import _support
except ImportError:
    import _support

import pathlib
import sys
import tempfile
import types
import unittest
import weakref
from unittest import mock

from pympress import nextfile_preview
from pympress.filelist import FileEntry


SOURCE = _support.SOURCE


class NativePage:
    def __init__(self, size=(1600.0, 900.0)):
        self.size = size
        self.render = mock.Mock()
        self.get_annot_mapping = mock.Mock(side_effect=AssertionError('No annotation inspection'))
        self.get_link_mapping = mock.Mock(side_effect=AssertionError('No link inspection'))

    def get_size(self):
        return self.size


class NativeDocument:
    def __init__(self, count=2):
        self.count = count
        self.pages = [NativePage(), NativePage((800.0, 1200.0))]
        self.get_attachments = mock.Mock(side_effect=AssertionError('No attachment inspection'))
        self.get_pdf_subtype = mock.Mock(side_effect=AssertionError('No active document initialization'))

    def get_n_pages(self):
        return self.count

    def get_page(self, page):
        return self.pages[page]


class NextFilePreviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='pympress-next-preview-test-')
        self.addCleanup(directory.cleanup)
        self.directory = pathlib.Path(directory.name).resolve()
        self.document = NativeDocument()
        self.loader = mock.Mock(return_value=self.document)
        self.preparer = mock.Mock()
        self.preview = nextfile_preview.NextFilePreview(self.loader, self.preparer)
        self.addCleanup(self.preview.cleanup)

    def entry(self, name='first.pdf', contents=b'%PDF-test', identity=None):
        path = self.directory / name
        path.write_bytes(contents)
        return FileEntry(name if identity is None else identity, path)

    def test_pdf_open_is_local_and_does_not_initialize_active_document_features(self):
        entry = self.entry()
        self.assertIs(self.preview.get(entry), self.preview)
        self.assertEqual(self.preview.page_count, 2)
        self.assertIsNone(self.preview.error)
        self.loader.assert_called_once_with(entry.uri)
        self.preparer.assert_not_called()
        self.assertAlmostEqual(self.preview.aspect(0), 16 / 9)
        self.assertTrue(self.preview.draw(mock.Mock(), 0, 640, 480))
        self.document.get_attachments.assert_not_called()
        self.document.get_pdf_subtype.assert_not_called()
        for page in self.document.pages:
            page.get_annot_mapping.assert_not_called()
            page.get_link_mapping.assert_not_called()

    def test_unchanged_source_reuses_document_across_pages_and_entry_settings(self):
        entry = self.entry()
        self.preview.get(entry)
        self.preview.aspect(0)
        self.preview.get(entry.with_settings(slide_seconds=10))
        self.preview.aspect(1)
        self.preview.draw(mock.Mock(), 1, 300, 200)
        self.loader.assert_called_once_with(entry.uri)

    def test_source_change_reloads_same_entry(self):
        entry = self.entry()
        self.preview.get(entry)
        entry.path.write_bytes(b'%PDF-replaced-and-larger')
        self.preview.get(entry)
        self.assertEqual(self.loader.call_count, 2)

    def test_same_size_modification_reloads_using_nanosecond_stat_identity(self):
        entry = self.entry()
        self.preview.get(entry)
        original_key = self.preview._key
        entry.path.write_bytes(b'%PDF-edit')
        self.preview.get(entry)
        self.assertNotEqual(self.preview._key, original_key)
        self.assertEqual(self.loader.call_count, 2)

    def test_different_entry_identity_releases_before_opening_replacement(self):
        documents = []

        def load(uri):
            self.assertTrue(all(document() is None for document in documents))
            document = NativeDocument()
            documents.append(weakref.ref(document))
            return document

        preview = nextfile_preview.NextFilePreview(loader=load)
        self.addCleanup(preview.cleanup)
        entry = self.entry()
        preview.get(entry)
        preview.get(FileEntry('another-entry', entry.path))
        self.assertEqual(len(documents), 2)
        self.assertIsNone(documents[0]())
        self.assertIsNotNone(documents[1]())
        preview.cleanup()
        self.assertIsNone(documents[1]())

    def test_source_path_change_reloads_even_for_same_entry_identity(self):
        first = self.entry()
        second = self.entry('second.pdf', identity=first.id)
        self.preview.get(first)
        self.preview.get(second)
        self.assertEqual(self.loader.call_args_list, [mock.call(first.uri), mock.call(second.uri)])

    def test_failed_open_is_cached_until_source_changes(self):
        self.loader.side_effect = RuntimeError('password needed')
        entry = self.entry()
        self.preview.get(entry)
        self.preview.get(entry)
        self.assertEqual(self.loader.call_count, 1)
        self.assertEqual(self.preview.page_count, 0)
        self.assertIsNone(self.preview._document)
        self.assertTrue(self.preview.error)
        self.assertEqual(self.preview.aspect(0), 1.0)
        self.assertFalse(self.preview.draw(mock.Mock(), 0, 640, 480))
        entry.path.write_bytes(b'%PDF-repaired')
        self.loader.side_effect = None
        self.preview.get(entry)
        self.assertEqual(self.loader.call_count, 2)
        self.assertIsNone(self.preview.error)
        self.assertEqual(self.preview.page_count, 2)

    def test_missing_source_never_opens_and_reappearing_source_retries(self):
        entry = FileEntry('missing', self.directory / 'missing.pdf')
        self.preview.get(entry)
        self.preview.get(entry)
        self.loader.assert_not_called()
        self.assertTrue(self.preview.error)
        entry.path.write_bytes(b'%PDF-repaired')
        self.preview.get(entry)
        self.loader.assert_called_once_with(entry.uri)
        self.assertIsNone(self.preview.error)

    def test_removed_source_releases_existing_preview(self):
        entry = self.entry()
        self.preview.get(entry)
        entry.path.unlink()
        self.preview.get(entry)
        self.assertIsNone(self.preview._document)
        self.assertEqual(self.preview.page_count, 0)
        self.assertTrue(self.preview.error)

    def test_remote_uri_unsupported_suffix_and_directory_never_reach_native_loader(self):
        directory = self.directory / 'directory.pdf'
        directory.mkdir()
        values = ('https://example.test/deck.pdf', 'file://example.test/deck.pdf',
                  'file:///tmp/deck.pdf?command=run', self.directory / 'script.py', directory)
        for value in values:
            with self.subTest(value=value):
                self.preview.get(types.SimpleNamespace(id='unsafe', path=value))
                self.assertTrue(self.preview.error)
                self.assertEqual(self.preview.page_count, 0)
        self.loader.assert_not_called()
        self.preparer.assert_not_called()

    def test_image_uses_private_pdf_and_releases_document_before_cleanup(self):
        entry = self.entry('image.png', contents=b'original image bytes')
        owner = types.SimpleNamespace(uri='file:///private/tmp/preview/slide.pdf')
        owner.cleanup = mock.Mock(side_effect=lambda: self.assertIsNone(self.preview._document))
        self.preparer.return_value = owner
        self.preview.get(entry)
        self.preparer.assert_called_once_with(entry.path)
        self.loader.assert_called_once_with(owner.uri)
        self.assertEqual(entry.path.read_bytes(), b'original image bytes')
        self.preview.get(entry)
        self.preparer.assert_called_once_with(entry.path)
        owner.cleanup.assert_not_called()
        self.preview.get(self.entry('next.pdf'))
        owner.cleanup.assert_called_once_with()

    def test_image_owner_is_cleaned_after_native_open_failure(self):
        owner = types.SimpleNamespace(uri='file:///private/tmp/preview/slide.pdf', cleanup=mock.Mock())
        self.preparer.return_value = owner
        self.loader.side_effect = RuntimeError('cannot parse conversion')
        self.preview.get(self.entry('image.gif'))
        self.assertTrue(self.preview.error)
        self.assertIsNone(self.preview._image)
        owner.cleanup.assert_called_once_with()

    def test_image_preparation_failure_is_cached_without_native_open(self):
        entry = self.entry('image.jpg')
        self.preparer.side_effect = ValueError('bad image')
        self.preview.get(entry)
        self.preview.get(entry)
        self.preparer.assert_called_once_with(entry.path)
        self.loader.assert_not_called()
        self.assertTrue(self.preview.error)

    def test_empty_or_invalid_page_count_releases_image_owner(self):
        for count in (0, -1, None, True, 1.5):
            with self.subTest(count=count):
                self.preview.cleanup()
                owner = types.SimpleNamespace(uri='file:///private/tmp/preview/slide.pdf', cleanup=mock.Mock())
                self.preparer.return_value = owner
                self.document.count = count
                self.preview.get(self.entry('image.png'))
                self.assertEqual(self.preview.page_count, 0)
                self.assertTrue(self.preview.error)
                self.assertIsNone(self.preview._document)
                owner.cleanup.assert_called_once_with()

    def test_cleanup_is_idempotent_and_clears_cached_error(self):
        owner = types.SimpleNamespace(uri='file:///private/tmp/preview/slide.pdf', cleanup=mock.Mock())
        self.preparer.return_value = owner
        self.preview.get(self.entry('image.png'))
        self.preview.cleanup()
        self.preview.cleanup()
        owner.cleanup.assert_called_once_with()
        self.assertIsNone(self.preview._key)
        self.assertIsNone(self.preview.error)
        self.assertEqual(self.preview.page_count, 0)

    def test_none_selection_releases_document_and_allows_reopening(self):
        entry = self.entry()
        self.preview.get(entry)
        self.preview.get(None)
        self.assertIsNone(self.preview._document)
        self.assertIsNone(self.preview.error)
        self.preview.get(entry)
        self.assertEqual(self.loader.call_count, 2)

    def test_cleanup_failure_does_not_escape_or_retain_image_owner(self):
        owner = types.SimpleNamespace(uri='file:///private/tmp/preview/slide.pdf',
                                      cleanup=mock.Mock(side_effect=OSError('cannot remove temporary directory')))
        self.preparer.return_value = owner
        self.preview.get(self.entry('image.png'))
        self.preview.cleanup()
        self.assertIsNone(self.preview._document)
        self.assertIsNone(self.preview._image)
        self.assertIsNone(self.preview._key)

    def test_page_aspect_and_centered_fit_are_not_stretched(self):
        self.preview.get(self.entry())
        self.assertAlmostEqual(self.preview.aspect(0), 1600 / 900)
        self.assertAlmostEqual(self.preview.aspect(1), 800 / 1200)
        context = mock.Mock()
        self.assertTrue(self.preview.draw(context, 0, 640, 480))
        context.rectangle.assert_any_call(0, 0, 640, 480)
        context.rectangle.assert_any_call(0.0, 60.0, 640.0, 360.0)
        context.scale.assert_called_once_with(0.4, 0.4)
        self.assertEqual(context.translate.call_args_list, [mock.call(0.0, 60.0), mock.call(-0.0, -0.0)])
        context.clip.assert_called_once_with()
        context.save.assert_called_once_with()
        context.restore.assert_called_once_with()
        self.document.pages[0].render.assert_called_once_with(context)

    def test_notes_crop_aspect_clipping_and_offset(self):
        self.preview.get(self.entry())
        region = (0.5, 0.0, 0.5, 1.0)
        self.assertAlmostEqual(self.preview.aspect(0, region), 800 / 900)
        context = mock.Mock()
        self.assertTrue(self.preview.draw(context, 0, 800, 900, region))
        context.rectangle.assert_any_call(0.0, 0.0, 800.0, 900.0)
        context.scale.assert_called_once_with(1.0, 1.0)
        self.assertEqual(context.translate.call_args_list, [mock.call(0.0, 0.0), mock.call(-800.0, -0.0)])
        context.clip.assert_called_once_with()

    def test_invalid_page_indices_do_not_drop_healthy_document(self):
        self.preview.get(self.entry())
        for page in (-1, 2, None, True, 1.5):
            with self.subTest(page=page):
                self.assertEqual(self.preview.aspect(page), 1.0)
                self.assertFalse(self.preview.draw(mock.Mock(), page, 640, 480))
                self.assertIsNone(self.preview.error)
                self.assertEqual(self.preview.page_count, 2)

    def test_invalid_page_dimensions_fail_closed_without_repeated_opens(self):
        entry = self.entry()
        sizes = ((0, 900), (1600, -1), (float('inf'), 900), (1600, float('nan')),
                 (1e308, 1e-308), (1e-308, 1e308))
        for size in sizes:
            with self.subTest(size=size):
                self.preview.cleanup()
                self.document.pages[0].size = size
                self.preview.get(entry)
                self.assertEqual(self.preview.aspect(0), 1.0)
                self.assertTrue(self.preview.error)
                self.assertEqual(self.preview.page_count, 0)
                count = self.loader.call_count
                self.preview.get(entry)
                self.assertEqual(self.loader.call_count, count)

    def test_invalid_crop_fails_without_leaking_exception(self):
        entry = self.entry()
        for region in ((0, 0, 0, 1), (-1, 0, 1, 1), (0.5, 0, 1, 1),
                       (0, 0, 1, float('nan')), (0, 0, True, 1), (), 'bad'):
            with self.subTest(region=region):
                self.preview.cleanup()
                self.preview.get(entry)
                self.assertEqual(self.preview.aspect(0, region), 1.0)
                self.assertTrue(self.preview.error)

    def test_invalid_viewport_returns_false_and_leaves_document_usable(self):
        self.preview.get(self.entry())
        for width, height in ((0, 900), (1600, -1), (float('inf'), 900), (1600, float('nan'))):
            with self.subTest(width=width, height=height):
                self.assertFalse(self.preview.draw(mock.Mock(), 0, width, height))
                self.assertIsNone(self.preview.error)
                self.assertEqual(self.preview.page_count, 2)

    def test_native_render_error_restores_context_and_discards_preview(self):
        self.preview.get(self.entry())
        self.document.pages[0].render.side_effect = RuntimeError('render failed')
        context = mock.Mock()
        self.assertFalse(self.preview.draw(context, 0, 640, 480))
        context.restore.assert_called_once_with()
        self.assertTrue(self.preview.error)
        self.assertIsNone(self.preview._document)

    def test_failed_page_references_are_released_before_image_owner_cleanup(self):
        for failure in ('dimensions', 'render'):
            with self.subTest(failure=failure):
                self.preview.cleanup()
                pages = []

                def page_at(number):
                    page = NativePage((0, 900) if failure == 'dimensions' else (1600, 900))
                    page.render.side_effect = RuntimeError('cannot render page')
                    pages.append(weakref.ref(page))
                    return page

                self.document.get_page = page_at
                owner = types.SimpleNamespace(uri='file:///private/tmp/preview/slide.pdf')
                owner.cleanup = mock.Mock(side_effect=lambda: self.assertTrue(all(page() is None for page in pages)))
                self.preparer.return_value = owner
                self.preview.get(self.entry('image.png'))
                if failure == 'dimensions':
                    self.assertEqual(self.preview.aspect(0), 1.0)
                else:
                    self.assertFalse(self.preview.draw(mock.Mock(), 0, 640, 480))
                owner.cleanup.assert_called_once_with()
                self.assertTrue(all(page() is None for page in pages))

    def test_context_restore_error_cannot_escape_into_ui(self):
        self.preview.get(self.entry())
        context = mock.Mock()
        context.restore.side_effect = RuntimeError('restore failed')
        self.assertTrue(self.preview.draw(context, 0, 640, 480))

    def test_native_loader_uses_no_password_and_no_external_dispatcher(self):
        poppler = types.SimpleNamespace(Document=types.SimpleNamespace(new_from_file=mock.Mock()))
        gi = types.ModuleType('gi')
        gi.require_version = mock.Mock()
        repository = types.ModuleType('gi.repository')
        repository.Poppler = poppler
        uri = self.entry().uri
        with mock.patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository}):
            result = nextfile_preview._load_document(uri)
        gi.require_version.assert_called_once_with('Poppler', '0.18')
        poppler.Document.new_from_file.assert_called_once_with(uri, None)
        self.assertIs(result, poppler.Document.new_from_file.return_value)


if __name__ == '__main__':
    unittest.main()
