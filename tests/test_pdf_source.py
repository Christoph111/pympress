#!/usr/bin/env python3
"""Exercise private PDF snapshot IO and failure cleanup without native decoders."""

try:
    from . import _support
except ImportError:
    import _support


import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from pympress import filelist, pdf_source


SOURCE = _support.SOURCE


class Reader:
    def __init__(self, wrapped, reads, change=None):
        self.wrapped, self.reads, self.change = wrapped, reads, change

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.wrapped.close()

    def fileno(self):
        return self.wrapped.fileno()

    def read(self, count):
        self.reads.append(count)
        value = self.wrapped.read(count)
        if self.change is not None:
            change, self.change = self.change, None
            change()
        return value


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='pympress-snapshot-tests-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.source = self.root / 'Original ä 100%.pdf'
        self.payload = b'%PDF-inert-fixture\n' + b'x' * 4096
        self.source.write_bytes(self.payload)
        self.created = []
        original = tempfile.TemporaryDirectory

        def make_directory(**options):
            owner = original(dir=str(self.root), **options)
            self.created.append(Path(owner.name))
            return owner

        patch = mock.patch.object(pdf_source.tempfile, 'TemporaryDirectory', side_effect=make_directory)
        patch.start()
        self.addCleanup(patch.stop)

    def wrap_source(self, reads, change=None):
        original = Path.open

        def open_path(path, mode='r', *args, **kwargs):
            handle = original(path, mode, *args, **kwargs)
            return Reader(handle, reads, change) if path == self.source and mode == 'rb' else handle

        return mock.patch.object(Path, 'open', autospec=True, side_effect=open_path)

    def test_private_closed_copy_preserves_original_bytes_and_filename_privacy(self):
        snapshot = pdf_source.prepare(self.source.as_uri(), enabled=True)
        self.addCleanup(snapshot.cleanup)
        self.assertEqual(snapshot.path.read_bytes(), self.payload)
        self.assertNotEqual(snapshot.path, self.source)
        self.assertNotIn(self.source.name, str(snapshot.path))
        self.assertEqual(filelist.local_path(snapshot.uri), snapshot.path)
        self.assertEqual(self.source.read_bytes(), self.payload)
        # Replacing the private file proves that no copy-stage handle remains.
        replacement = snapshot.path.with_suffix('.replacement')
        replacement.write_bytes(self.payload)
        replacement.replace(snapshot.path)
        private = snapshot.path.parent
        snapshot.cleanup()
        snapshot.cleanup()
        self.assertFalse(private.exists())
        self.assertEqual(self.source.read_bytes(), self.payload)

    @unittest.skipIf(os.name == 'nt', 'POSIX mode bits do not express Windows temporary-directory ACLs.')
    def test_temporary_directory_is_private_on_posix(self):
        snapshot = pdf_source.prepare(self.source, enabled=True)
        self.addCleanup(snapshot.cleanup)
        self.assertEqual(snapshot.path.parent.stat().st_mode & 0o077, 0)

    def test_platform_selection_is_explicit_and_disabled_path_does_not_touch_source(self):
        with mock.patch.object(pdf_source, 'local_path', side_effect=AssertionError('must remain untouched')):
            self.assertIsNone(pdf_source.prepare('https://example.test/talk.pdf', enabled=False))
            with mock.patch.object(pdf_source, 'os', SimpleNamespace(name='posix')):
                self.assertIsNone(pdf_source.prepare('https://example.test/talk.pdf'))
        with mock.patch.object(pdf_source, 'os', SimpleNamespace(name='nt', fstat=os.fstat)):
            snapshot = pdf_source.prepare(self.source)
        self.addCleanup(snapshot.cleanup)
        self.assertEqual(snapshot.path.read_bytes(), self.payload)

    def test_invalid_references_and_switches_fail_without_a_snapshot(self):
        image = self.root / 'image.png'
        image.write_bytes(b'not a PDF')
        for value in ['https://example.test/talk.pdf', 'file://server/share/talk.pdf',
                      self.root / 'missing.pdf', image, str(self.source) + '\x00']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                pdf_source.prepare(value, enabled=True)
        for enabled in (0, 1, 'true', [], {}):
            with self.subTest(enabled=enabled), self.assertRaises(ValueError):
                pdf_source.prepare(self.source, enabled=enabled)
        self.assertEqual(self.created, [])

    def test_copy_reads_are_bounded_and_do_not_load_the_whole_file_at_once(self):
        reads = []
        with mock.patch.object(pdf_source, 'COPY_CHUNK_BYTES', 1024), self.wrap_source(reads):
            snapshot = pdf_source.prepare(self.source, enabled=True)
        self.addCleanup(snapshot.cleanup)
        self.assertGreater(len(reads), 4)
        self.assertTrue(all(0 < count <= 1024 for count in reads))
        self.assertEqual(snapshot.path.read_bytes(), self.payload)

    def test_source_growth_is_bounded_and_removes_the_partial_copy(self):
        reads = []

        def grow():
            with self.source.open('ab') as output:
                output.write(b'appended while copying')

        with mock.patch.object(pdf_source, 'COPY_CHUNK_BYTES', 1024), self.wrap_source(reads, grow):
            with self.assertRaisesRegex(ValueError, 'changed'):
                pdf_source.prepare(self.source, enabled=True)
        self.assertLessEqual(sum(reads), len(self.payload) + 1)
        self.assertTrue(self.created)
        self.assertTrue(all(not path.exists() for path in self.created))

    def test_source_truncation_is_reported_and_removes_the_partial_copy(self):
        def shrink():
            with self.source.open('wb') as output:
                output.write(b'%PDF-shortened')

        with mock.patch.object(pdf_source, 'COPY_CHUNK_BYTES', 1024), self.wrap_source([], shrink):
            with self.assertRaisesRegex(ValueError, 'changed'):
                pdf_source.prepare(self.source, enabled=True)
        self.assertTrue(all(not path.exists() for path in self.created))

    def test_same_length_source_change_is_detected_by_open_file_metadata(self):
        def change_timestamp():
            current = self.source.stat()
            os.utime(str(self.source), ns=(current.st_atime_ns, current.st_mtime_ns + 2000000000))

        with self.wrap_source([], change_timestamp):
            with self.assertRaisesRegex(ValueError, 'changed'):
                pdf_source.prepare(self.source, enabled=True)
        self.assertTrue(all(not path.exists() for path in self.created))

    def test_copy_failure_and_interruption_remove_partial_files(self):
        for error in (OSError('source read failed'), KeyboardInterrupt()):
            with self.subTest(error=error):
                with self.wrap_source([], lambda: (_ for _ in ()).throw(error)):
                    with self.assertRaises(type(error)):
                        pdf_source.prepare(self.source, enabled=True)
                self.assertTrue(all(not path.exists() for path in self.created))
        self.assertEqual(self.source.read_bytes(), self.payload)


if __name__ == '__main__':
    unittest.main(verbosity=2)
