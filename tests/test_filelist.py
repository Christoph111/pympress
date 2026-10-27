#!/usr/bin/env python3
"""File-list trust boundaries and image conversion lifecycle, without a GUI.

Run with the existing system Python; GI/Cairo calls are isolated with mocks.
The built application's native self-test additionally exercises real decoders.
"""

try:
    from . import _support
except ImportError:
    import _support


import json
import pathlib
import os
import tempfile
import types
import unittest
from unittest import mock

from pympress import filelist, image_document

SOURCE = _support.SOURCE
symlink_or_skip = _support.symlink_or_skip


class Fixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='pympress-list-tests-')
        self.root = pathlib.Path(self.directory.name).resolve()
        self.pdf = self.root / 'Slides 100% ä.pdf'
        self.image = self.root / 'Bild.JPG'
        self.pdf.write_bytes(b'%PDF-test')
        self.image.write_bytes(b'image-test')

    def tearDown(self):
        self.directory.cleanup()


class Files(Fixture):
    def test_json_depth_failures_are_reported_on_modern_and_legacy_python(self):
        target = self.root / 'nested.json'
        target.write_text('[' * 2000 + '0' + ']' * 2000, encoding='utf-8')
        with self.assertRaises(filelist.FileListError):
            filelist.FileList.load(target)
        with mock.patch.object(filelist.json, 'loads', side_effect=RuntimeError('maximum recursion depth exceeded')):
            with self.assertRaisesRegex(filelist.FileListError, 'Could not read the file list'):
                filelist.FileList.load(target)

    def test_native_windows_drive_parsing_is_portable_and_rejects_unc(self):
        native = filelist._native_absolute_path(r'C:\Users\Presenter\Slides ä.pdf', pathlib.PureWindowsPath)
        self.assertEqual(native.drive, 'C:')
        self.assertTrue(native.is_absolute())
        self.assertEqual(filelist._native_absolute_path('C:/Slides/talk.pdf', pathlib.PureWindowsPath),
                         pathlib.PureWindowsPath('C:/Slides/talk.pdf'))
        self.assertIsNone(filelist._native_absolute_path('file:///C:/Slides/talk.pdf', pathlib.PureWindowsPath))
        for value in [r'\\server\share\talk.pdf', '//server/share/talk.pdf', r'\\?\C:\talk.pdf']:
            with self.subTest(value=value), self.assertRaises(filelist.FileListError):
                filelist._native_absolute_path(value, pathlib.PureWindowsPath)

    def test_remote_paths_are_rejected_before_resolving_or_touching_filesystem(self):
        remote = [r'\\server\share\talk.pdf', '//server/share/talk.pdf',
                  'file://server/share/talk.pdf', 'file:////server/share/talk.pdf',
                  'file:///%2Fserver/share/talk.pdf']
        error = AssertionError('must not resolve remote path')
        with mock.patch.object(filelist.os.path, 'realpath', side_effect=error), \
                mock.patch.object(pathlib.Path, 'is_file', side_effect=AssertionError('must not stat remote path')):
            for value in remote:
                with self.subTest(value=value), self.assertRaises(filelist.FileListError):
                    filelist.local_path(value)
            for value in remote[:2]:
                for operation in (filelist.FileList().save, filelist.FileList.load):
                    with self.subTest(value=value, operation=operation), self.assertRaises(filelist.FileListError):
                        operation(value)

    @unittest.skipUnless(os.name == 'nt', 'Requires the Windows local filesystem.')
    def test_windows_absolute_filenames_and_file_uris_round_trip(self):
        self.assertTrue(self.pdf.drive)
        self.assertEqual(filelist.local_path(str(self.pdf)), self.pdf)
        self.assertEqual(filelist.local_path(self.pdf), self.pdf)
        self.assertEqual(filelist.local_path(self.pdf.as_uri()), self.pdf)
        files = filelist.FileList()
        files.append([str(self.pdf), self.image.as_uri()])
        target = self.root / 'Windows-list.json'
        files.save(target)
        saved = json.loads(target.read_text(encoding='utf-8'))
        self.assertTrue(all('\\' not in entry['path'] for entry in saved['entries']))
        self.assertEqual(filelist.FileList.load(target).entries, files.entries)

    def test_cross_drive_storage_uses_a_local_file_uri(self):
        files = filelist.FileList()
        files.append(self.pdf)
        target = self.root / 'cross-drive.json'
        with mock.patch.object(filelist.os.path, 'relpath', side_effect=ValueError('another drive')):
            files.save(target)
        saved = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(saved['entries'][0]['path'], self.pdf.as_uri())
        self.assertEqual(filelist.FileList.load(target).entries, files.entries)

    def test_entry_tuple_replacements_remain_validated_and_immutable(self):
        entry = filelist.FileEntry('a' * 32, self.pdf)
        self.assertEqual(entry.slide_seconds, 5)
        changed = entry.with_settings(slide_seconds=2, page_seconds={1: 3})
        self.assertEqual(changed.id, entry.id)
        self.assertEqual(changed.page_seconds, {1: 3})
        self.assertEqual(entry.slide_seconds, 5)
        self.assertIsInstance(hash(changed), int)
        with self.assertRaises(AttributeError):
            changed.slide_seconds = 8
        with self.assertRaises(filelist.FileListError):
            changed._replace(slide_seconds=float('nan'))
        with self.assertRaises(filelist.FileListError):
            filelist.FileEntry._make([entry.id, entry.path, -1, {}, True])

    def test_error_templates_are_translated_before_filename_formatting(self):
        def translator(message):
            return 'Translated type: {}' if message == 'Unsupported file type: {}' else message

        with mock.patch('builtins._', translator):
            with self.assertRaisesRegex(filelist.FileListError, 'Translated type: wrong.svg'):
                filelist.local_path(self.root / 'wrong.svg')

    def test_timing_settings_are_immutable_and_keep_entry_identity(self):
        files = filelist.FileList()
        entry, duplicate = files.append([self.pdf, self.pdf])
        files.set_current(0)
        settings = {0: 1.5, 3: 8}
        updated = files.update_settings(0, slide_seconds=12, page_seconds=settings, transition_enabled=False)
        settings[0] = 99
        self.assertEqual(updated.id, entry.id)
        self.assertEqual(updated.path, entry.path)
        self.assertIs(files.current, updated)
        self.assertEqual(updated.duration_for_page(0), 1.5)
        self.assertEqual(updated.duration_for_page(1), 12)
        self.assertEqual(duplicate.slide_seconds, 5)
        self.assertEqual(duplicate.page_seconds, {})
        self.assertTrue(duplicate.transition_enabled)
        with self.assertRaises(TypeError):
            updated.page_seconds[0] = 20
        files.move(0, 1)
        self.assertEqual(files.current.id, entry.id)
        self.assertEqual(files.current.page_seconds, {0: 1.5, 3: 8})

    def test_invalid_settings_do_not_partially_update_an_entry(self):
        files = filelist.FileList()
        entry = files.append(self.pdf)[0]
        for settings in [dict(slide_seconds=value)
                         for value in [True, '5', 0, float('nan'),
                                       float('inf'),
                                       86401, 10 ** 1000]] + [dict(page_seconds={True: 1}),
                                                              dict(page_seconds={-1: 1}),
                                                              dict(page_seconds={0: float('nan')}),
                                                              dict(page_seconds={filelist.MAX_PAGE_INDEX + 1: 1}),
                                                              dict(page_seconds=[]),
                                                              dict(transition_enabled=1)]:
            with self.subTest(settings=repr(settings)[:80]), self.assertRaises(filelist.FileListError):
                files.update_settings(0, **settings)
            self.assertIs(files.entries[0], entry)
        with mock.patch.object(filelist, 'MAX_PAGE_OVERRIDES', 1), self.assertRaises(filelist.FileListError):
            files.update_settings(0, page_seconds={0: 1, 1: 2})

    def test_v3_round_trip_all_timing_and_transition_settings(self):
        files = filelist.FileList()
        files.append([self.pdf, self.image])
        files.update_settings(0, slide_seconds=7.5, page_seconds={0: 0.1, 5: 86400}, transition_enabled=False)
        files.set_transition('fade', 2.75)
        files.set_playback('list', True)
        target = self.root / 'settings.json'
        files.save(target)
        saved = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(saved['version'], 3)
        self.assertEqual(saved['entries'][0]['page_seconds'], {'0': 0.1, '5': 86400})
        loaded = filelist.FileList.load(target)
        self.assertEqual(loaded.entries, files.entries)
        self.assertEqual((loaded.transition_style, loaded.transition_seconds), ('fade', 2.75))
        self.assertEqual((loaded.playback_mode, loaded.playback_loop), ('list', True))

    def test_v1_lists_load_with_safe_timing_defaults(self):
        target = self.root / 'old.json'
        target.write_text(json.dumps({'format': filelist.LIST_FORMAT, 'version': 1, 'current': 0,
                                     'entries': [{'id': 'a' * 32, 'path': self.pdf.name}]}), encoding='utf-8')
        loaded = filelist.FileList.load(target)
        self.assertEqual(loaded.current.slide_seconds, 5)
        self.assertEqual(loaded.current.page_seconds, {})
        self.assertTrue(loaded.current.transition_enabled)
        self.assertEqual((loaded.transition_style, loaded.transition_seconds), ('cut', 0.5))
        self.assertEqual((loaded.playback_mode, loaded.playback_loop), ('current', False))
        self.assertTrue(loaded.continue_by_default)
        self.assertIsNone(loaded.current.continue_after)

    def test_v3_rejects_nonfinite_or_malformed_timing_and_duplicate_json_keys(self):
        files = filelist.FileList()
        files.append(self.pdf)
        target = self.root / 'settings.json'
        files.save(target)
        good = json.loads(target.read_text(encoding='utf-8'))
        bad_entries = [dict(good['entries'][0], **settings) for settings in [
            dict(slide_seconds=float('nan')), dict(slide_seconds=float('inf')), dict(slide_seconds=10 ** 1000),
            dict(slide_seconds=False), dict(page_seconds={'01': 1}), dict(page_seconds={'-1': 1}),
            dict(page_seconds={'0': float('nan')}), dict(page_seconds={'1000001': 1}),
            dict(page_seconds={'0.0': 1}), dict(page_seconds={'٠': 1}), dict(page_seconds=[]),
            dict(transition_enabled=1)]]
        variants = [dict(good, entries=[entry]) for entry in bad_entries] + [
            dict(good, transition_style='other'), dict(good, transition_seconds=float('nan')),
            dict(good, transition_seconds=-0.1), dict(good, transition_seconds=5.1),
            dict(good, transition_seconds=10 ** 1000), dict(good, playback_loop=1), dict(good, playback_mode='other')]
        for value in variants:
            target.write_text(json.dumps(value), encoding='utf-8')
            with self.subTest(value=repr(value)[:100]), self.assertRaises(filelist.FileListError):
                filelist.FileList.load(target)
        target.write_text('{"format":"a","format":"b"}', encoding='utf-8')
        with self.assertRaises(filelist.FileListError):
            filelist.FileList.load(target)

    def test_append_duplicates_are_independent_and_atomic(self):
        files = filelist.FileList()
        entries = files.append([self.pdf, self.pdf, self.image])
        self.assertEqual(len({entry.id for entry in entries}), 3)
        self.assertEqual(entries[0].uri, self.pdf.as_uri())
        self.assertEqual(entries[0].name, self.pdf.name)
        self.assertTrue(entries[2].is_image)
        self.assertFalse(entries[0].is_image)
        self.assertEqual(files.current_index, -1)
        with self.assertRaises(filelist.FileListError):
            files.append([self.pdf, self.root / 'missing.pdf'])
        self.assertEqual(files.entries, tuple(entries))

    def test_reordering_preserves_current_identity(self):
        files = filelist.FileList()
        entries = files.append([self.pdf, self.image, self.pdf])
        files.set_current(1)
        files.move(0, 2)
        self.assertIs(files.current, entries[1])
        self.assertEqual(files.current_index, 0)
        files.remove(2)
        self.assertIs(files.current, entries[1])
        files.remove(0)
        self.assertIsNone(files.current)
        self.assertEqual(files.current_index, -1)
        files.clear()
        self.assertEqual(files.entries, ())

    def test_remove_before_current_adjusts_index(self):
        files = filelist.FileList()
        entries = files.append([self.pdf, self.image])
        files.set_current(1)
        files.remove(0)
        self.assertIs(files.current, entries[1])
        self.assertEqual(files.current_index, 0)

    def test_invalid_indices_and_count_limit(self):
        files = filelist.FileList()
        files.append(self.pdf)
        for value in [-2, 2, True, 0.0, -1.0, '0']:
            with self.subTest(value=value), self.assertRaises(filelist.FileListError):
                files.set_current(value)
        with mock.patch.object(filelist, 'MAX_ENTRIES', 1), self.assertRaises(filelist.FileListError):
            files.append(self.image)
        self.assertEqual(len(files), 1)

    def test_local_paths_preserve_literal_percent_and_resolve_uri(self):
        self.assertEqual(filelist.local_path(self.pdf), self.pdf)
        self.assertEqual(filelist.local_path(self.pdf.as_uri()), self.pdf)
        self.assertEqual(filelist.local_path(self.pdf.as_uri().replace('file:///', 'file://localhost/')), self.pdf)
        alias = self.root / 'alias.pdf'
        symlink_or_skip(self, alias, self.pdf)
        self.assertEqual(filelist.local_path(alias), self.pdf)

    def test_reject_remote_handlers_controls_directories(self):
        directory = self.root / 'directory.pdf'
        directory.mkdir()
        for value in ['https://example.test/a.pdf', 'file://server/tmp/a.pdf',
                      'file://user@localhost/tmp/a.pdf', 'file://localhost:80/tmp/a.pdf',
                      self.pdf.as_uri() + '?active=1', self.pdf.as_uri() + '#fragment',
                      'file:relative.pdf', self.pdf.as_uri().replace('Slides', '%00Slides'),
                      'javascript:alert(1)', str(self.pdf) + '\x00', directory,
                      self.root / 'source.svg', self.root / 'missing.pdf']:
            with self.subTest(value=value), self.assertRaises(filelist.FileListError):
                filelist.local_path(value)

    def test_drop_uri_list_comments_unicode_and_multiple_files(self):
        payload = '# first\r\n{}\r\n# between\r\n{}\r\n'.format(
            self.pdf.as_uri(), self.image.as_uri().replace('file:///', 'file://localhost/'))
        self.assertEqual(filelist.decode_drop(payload, uri_list=True), [self.pdf, self.image])
        self.assertEqual(filelist.decode_drop(payload), [self.pdf, self.image])
        self.assertEqual(filelist.decode_drop(self.pdf.as_uri()), [self.pdf])
        self.assertEqual(filelist.decode_drop('# nothing\n', True), [])

    def test_drop_text_paths_and_rejections(self):
        self.assertEqual(filelist.decode_drop(str(self.pdf) + '\n' + str(self.image)), [self.pdf, self.image])
        for value in ['https://example.test/a.pdf', 'file://host/a.pdf', '/tmp/missing.pdf', b'bytes', '\ud800',
                      str(self.pdf) + '\x00', str(self.pdf) + '\x0b' + str(self.image)]:
            with self.subTest(value=value), self.assertRaises(filelist.FileListError):
                filelist.decode_drop(value)
        with self.assertRaises(filelist.FileListError):
            filelist.decode_drop(str(self.pdf), True)

    def test_round_trip_relative_paths_duplicate_identity_missing_reference(self):
        files = filelist.FileList()
        entries = files.append([self.pdf, self.pdf, self.image])
        files.set_current(1)
        destination = self.root / 'show.pympress-list.json'
        files.save(destination)
        data = json.loads(destination.read_text(encoding='utf-8'))
        self.assertEqual(data['entries'][0]['path'], self.pdf.name)
        self.image.unlink()
        loaded = filelist.FileList.load(destination)
        self.assertEqual(loaded.entries, files.entries)
        self.assertEqual(loaded.current.id, entries[1].id)
        self.assertFalse(loaded.entries[2].exists)

    def test_saved_relative_list_can_be_moved_with_its_files(self):
        first = self.root / 'first'
        first.mkdir()
        local_pdf = first / 'talk.pdf'
        local_pdf.write_bytes(b'PDF')
        files = filelist.FileList()
        files.append(local_pdf)
        files.save(first / 'list.json')
        moved = self.root / 'moved'
        first.rename(moved)
        loaded = filelist.FileList.load(moved / 'list.json')
        self.assertEqual(loaded.entries[0].path, moved / 'talk.pdf')

    @unittest.skipIf(os.name == 'nt', 'Windows filenames cannot contain colons.')
    def test_colons_in_native_filenames_round_trip_as_local_references(self):
        files = filelist.FileList()
        colon_pdf = self.root / 'Talk: Intro.pdf'
        colon_pdf.write_bytes(b'PDF')
        self.assertEqual(filelist.local_path(pathlib.Path(colon_pdf.name), base=self.root), colon_pdf)
        colon_directory = self.root / 'Session: 2026'
        colon_directory.mkdir()
        colon_image = colon_directory / 'Image.png'
        colon_image.write_bytes(b'PNG')
        files.append([colon_pdf, colon_image])
        target = self.root / 'list.json'
        files.save(target)
        serialized = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(serialized['entries'][0]['path'], './Talk: Intro.pdf')
        self.assertEqual(serialized['entries'][1]['path'], './Session: 2026/Image.png')
        self.assertEqual(filelist.FileList.load(target).entries, files.entries)

    def test_strict_schema_bounds_and_remote_references(self):
        files = filelist.FileList()
        files.append(self.pdf)
        target = self.root / 'list.json'
        files.save(target)
        good = json.loads(target.read_text(encoding='utf-8'))
        variants = [[], dict(good, version=True), dict(good, current=True), dict(good, current=1),
                    dict(good, extra='field'), dict(good, entries=['bad']),
                    dict(good, entries=[dict(good['entries'][0], path='https://example.test/a.pdf')]),
                    dict(good, entries=[good['entries'][0], good['entries'][0]])]
        for value in variants:
            with self.subTest(value=value):
                target.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(filelist.FileListError):
                    filelist.FileList.load(target)
        target.write_bytes(b' ' * (filelist.MAX_LIST_BYTES + 1))
        with self.assertRaises(filelist.FileListError):
            filelist.FileList.load(target)

    def test_failed_atomic_save_preserves_existing_list_and_removes_temporary(self):
        files = filelist.FileList()
        files.append(self.pdf)
        target = self.root / 'list.json'
        target.write_text('existing', encoding='utf-8')
        with mock.patch.object(pathlib.Path, 'replace', side_effect=OSError('cannot replace')):
            with self.assertRaises(filelist.FileListError):
                files.save(target)
        self.assertEqual(target.read_text(encoding='utf-8'), 'existing')
        self.assertEqual(list(self.root.glob('.pympress-list-*')), [])

    def test_list_save_cannot_overwrite_a_referenced_file_or_its_alias(self):
        files = filelist.FileList()
        files.append(self.pdf)
        alias = self.root / 'alias.json'
        symlink_or_skip(self, alias, self.pdf)
        original = self.pdf.read_bytes()
        for target in [self.pdf, alias]:
            with self.subTest(target=target), self.assertRaises(filelist.FileListError):
                files.save(target)
        self.assertEqual(self.pdf.read_bytes(), original)


class Continuation(Fixture):
    def test_global_policy_requires_a_current_entry_and_resolves_overrides(self):
        files = filelist.FileList()
        self.assertTrue(files.continue_by_default)
        self.assertFalse(files.can_advance())
        first, second = files.append([self.pdf, self.image])
        self.assertIsNone(first.continue_after)
        self.assertFalse(files.can_advance())
        self.assertTrue(files.can_advance(second))
        files.set_current(0)
        self.assertTrue(files.can_advance())
        files.set_continuation(False)
        self.assertFalse(files.can_advance())
        files.set_entry_continuation([0], True)
        self.assertTrue(files.can_advance())
        self.assertFalse(files.can_advance(second))
        files.set_continuation(True)
        files.set_entry_continuation([0], False)
        self.assertFalse(files.can_advance())
        self.assertTrue(files.can_advance(second))
        files.set_current(-1)
        self.assertFalse(files.can_advance())
        with self.assertRaises(filelist.FileListError):
            files.can_advance(object())

    def test_entry_reset_and_other_settings_updates_keep_stable_identity(self):
        files = filelist.FileList()
        first, duplicate = files.append([self.pdf, self.pdf])
        files.set_current(0)
        files.set_continuation(False)
        changed, = files.set_entry_continuation([0, 0], True)
        self.assertEqual(changed.id, first.id)
        self.assertIs(files.current, changed)
        self.assertIsNone(duplicate.continue_after)
        changed = files.update_settings(0, slide_seconds=12, page_seconds={1: 3})
        self.assertTrue(changed.continue_after)
        reset = files.update_settings(0, continue_after=None)
        self.assertIsNone(reset.continue_after)
        self.assertEqual(reset.id, first.id)
        self.assertEqual(reset.page_seconds, {1: 3})
        self.assertFalse(files.can_advance())
        files.move(0, 1)
        self.assertIs(files.current, reset)
        updated, = files.set_entry_continuation([1], False)
        self.assertEqual(updated.id, first.id)
        self.assertIs(files.current, updated)
        reset, = files.set_entry_continuation([1], None)
        self.assertIsNone(reset.continue_after)
        self.assertIs(files.current, reset)
        self.assertEqual(files.set_entry_continuation([], False), ())

    def test_immutable_entry_replacement_distinguishes_omitted_and_inherited(self):
        entry = filelist.FileEntry('a' * 32, self.pdf, continue_after=False)
        changed = entry.with_settings(slide_seconds=2)
        self.assertFalse(changed.continue_after)
        self.assertFalse(entry.with_settings().continue_after)
        self.assertIsNone(changed.with_settings(continue_after=None).continue_after)
        self.assertTrue(changed._replace(continue_after=True).continue_after)
        self.assertIsNone(filelist.FileEntry._make([entry.id, entry.path, 5, {}, True]).continue_after)
        copied = filelist.FileEntry._make(tuple(changed))
        self.assertEqual(copied, changed)
        self.assertEqual(hash(copied), hash(changed))
        with self.assertRaises(AttributeError):
            entry.continue_after = True
        for operation in [lambda: entry.with_settings(continue_after=1),
                          lambda: entry._replace(continue_after=1),
                          lambda: filelist.FileEntry._make([entry.id, entry.path, 5, {}, True, 1])]:
            with self.assertRaises(filelist.FileListError):
                operation()

    def test_invalid_policy_values_and_indices_leave_all_settings_unchanged(self):
        files = filelist.FileList()
        files.append([self.pdf, self.image])
        files.set_current(0)
        files.set_continuation(False)
        original = files.entries
        for value in [0, 1, 0.0, 'true', [], {}, object()]:
            with self.subTest(value=value):
                for operation in [lambda: files.set_continuation(value),
                                  lambda: files.set_entry_continuation([0, 1], value),
                                  lambda: files.update_settings(0, continue_after=value),
                                  lambda: filelist.FileEntry('b' * 32, self.pdf, continue_after=value)]:
                    with self.assertRaises(filelist.FileListError):
                        operation()
                self.assertFalse(files.continue_by_default)
                self.assertEqual(files.entries, original)
        with self.assertRaises(filelist.FileListError):
            files.set_continuation(None)
        for indices in [[0, 2], [0, -1], [0, True], [0, 1.0], '0', None, 0]:
            with self.subTest(indices=indices), self.assertRaises(filelist.FileListError):
                files.set_entry_continuation(indices, True)
            self.assertEqual(files.entries, original)
            self.assertIs(files.current, original[0])
        with mock.patch.object(filelist, 'MAX_ENTRIES', 1), self.assertRaises(filelist.FileListError):
            files.set_entry_continuation([0, 1], True)
        self.assertEqual(files.entries, original)

    def test_v3_round_trip_preserves_global_inherited_and_explicit_settings(self):
        files = filelist.FileList()
        files.append([self.pdf, self.pdf, self.image])
        files.set_current(1)
        files.set_continuation(False)
        files.set_entry_continuation([1], True)
        files.set_entry_continuation([2], False)
        target = self.root / 'continuation.json'
        files.save(target)
        saved = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(saved['version'], 3)
        self.assertIs(saved['continue_by_default'], False)
        self.assertEqual([entry['continue_after'] for entry in saved['entries']], [None, True, False])
        loaded = filelist.FileList.load(target)
        self.assertEqual(loaded.entries, files.entries)
        self.assertEqual(loaded.current.id, files.current.id)
        self.assertFalse(loaded.continue_by_default)
        self.assertEqual([loaded.can_advance(entry) for entry in loaded.entries], [False, True, False])
        loaded.set_continuation(True)
        self.assertEqual([loaded.can_advance(entry) for entry in loaded.entries], [True, True, False])

    def test_v2_lists_preserve_legacy_continuation_and_existing_playback_settings(self):
        files = filelist.FileList()
        files.append([self.pdf, self.image])
        files.set_current(0)
        files.update_settings(0, slide_seconds=7, page_seconds={1: 9}, transition_enabled=False)
        files.set_transition('fade', 1.25)
        files.set_playback('list', True)
        target = self.root / 'legacy.json'
        files.save(target)
        saved = json.loads(target.read_text(encoding='utf-8'))
        saved['version'] = 2
        del saved['continue_by_default']
        for entry in saved['entries']:
            del entry['continue_after']
        target.write_text(json.dumps(saved), encoding='utf-8')
        loaded = filelist.FileList.load(target)
        self.assertEqual(loaded.entries, files.entries)
        self.assertTrue(loaded.continue_by_default)
        self.assertTrue(loaded.can_advance())
        self.assertTrue(all(entry.continue_after is None for entry in loaded.entries))
        self.assertEqual((loaded.transition_style, loaded.transition_seconds), ('fade', 1.25))
        self.assertEqual((loaded.playback_mode, loaded.playback_loop), ('list', True))

    def test_v3_requires_strict_boolean_values_and_complete_known_schema(self):
        files = filelist.FileList()
        files.append(self.pdf)
        target = self.root / 'strict.json'
        files.save(target)
        good = json.loads(target.read_text(encoding='utf-8'))
        variants = [dict(good, continue_by_default=value) for value in [None, 0, 1, 0.0, 'true', [], {}]]
        variants += [dict(good, entries=[dict(good['entries'][0], continue_after=value)])
                     for value in [0, 1, 0.0, 'true', [], {}]]
        no_default = dict(good)
        del no_default['continue_by_default']
        no_override = dict(good['entries'][0])
        del no_override['continue_after']
        variants += [no_default, dict(good, entries=[no_override]),
                     dict(good, entries=[dict(good['entries'][0], unknown=True)])]
        # New fields are accepted only under the new schema version.
        v2_with_default = dict(good, version=2, entries=[no_override])
        v2_with_override = dict(good, version=2)
        del v2_with_override['continue_by_default']
        variants += [v2_with_default, v2_with_override]
        for value in variants:
            target.write_text(json.dumps(value), encoding='utf-8')
            with self.subTest(value=value), self.assertRaises(filelist.FileListError):
                filelist.FileList.load(target)


class Images(Fixture):
    """Use actual adapter code with only the native codecs/Cairo mocked."""

    def graphics(self, width=120, height=80, oriented=None, image_format='png'):
        pixbuf = mock.Mock()
        pixbuf.get_width.return_value = width
        pixbuf.get_height.return_value = height
        pixbuf.apply_embedded_orientation.return_value = oriented
        loader = types.SimpleNamespace(Pixbuf=types.SimpleNamespace(
            get_file_info=mock.Mock(return_value=(types.SimpleNamespace(get_name=lambda: image_format), width, height)),
            new_from_file=mock.Mock(return_value=pixbuf)))
        surface = mock.Mock()

        def make_surface(path, w, h):
            pathlib.Path(path).write_bytes(b'%PDF-mock')
            return surface

        cairo = types.SimpleNamespace(PDFSurface=mock.Mock(side_effect=make_surface), Context=mock.Mock())
        gdk = types.SimpleNamespace(cairo_set_source_pixbuf=mock.Mock())
        return (loader, gdk, cairo), pixbuf, surface

    def test_conversion_has_correct_aspect_orientation_private_path_and_cleanup(self):
        oriented = mock.Mock()
        oriented.get_width.return_value = 80
        oriented.get_height.return_value = 120
        graphics, pixbuf, surface = self.graphics(oriented=oriented)
        original = self.image.read_bytes()
        with mock.patch.object(image_document, '_graphics_modules', return_value=graphics):
            prepared = image_document.prepare_image(self.image.as_uri())
        self.assertEqual(prepared.source_path, self.image)
        self.assertTrue(prepared.pdf_path.is_file())
        self.assertEqual(prepared.uri, prepared.pdf_path.as_uri())
        args = graphics[2].PDFSurface.call_args.args
        self.assertEqual(args[1:], (80, 120))
        self.assertNotEqual(graphics[0].Pixbuf.new_from_file.call_args.args[0], str(self.image))
        pixbuf.apply_embedded_orientation.assert_called_once()
        graphics[1].cairo_set_source_pixbuf.assert_called_once_with(graphics[2].Context.return_value, oriented, 0, 0)
        surface.finish.assert_called_once()
        self.assertEqual(self.image.read_bytes(), original)
        self.assertEqual(list(prepared.pdf_path.parent.iterdir()), [prepared.pdf_path])
        parent = prepared.pdf_path.parent
        prepared.cleanup()
        prepared.cleanup()
        self.assertFalse(parent.exists())

    def test_page_edge_is_capped_without_changing_aspect(self):
        graphics, _, _ = self.graphics(width=4000, height=2000)
        with mock.patch.object(image_document, '_graphics_modules', return_value=graphics):
            prepared = image_document.prepare_image(self.image)
        self.assertEqual(graphics[2].PDFSurface.call_args.args[1:], (1440, 720))
        prepared.cleanup()

    def test_oversized_header_rejected_before_decode_and_temp_cleaned(self):
        graphics, _, _ = self.graphics(width=10000, height=5000)
        directories = []
        real_tempdir = tempfile.TemporaryDirectory

        def tempdir(**options):
            item = real_tempdir(dir=str(self.root), **options)
            directories.append(pathlib.Path(item.name))
            return item

        with mock.patch.object(image_document, '_graphics_modules', return_value=graphics), \
                mock.patch.object(image_document.tempfile, 'TemporaryDirectory', side_effect=tempdir):
            with self.assertRaises(image_document.ImageError):
                image_document.prepare_image(self.image)
        graphics[0].Pixbuf.new_from_file.assert_not_called()
        self.assertTrue(directories)
        self.assertTrue(all(not directory.exists() for directory in directories))

    def test_decoder_failure_is_friendly_and_cleans_private_files(self):
        graphics, _, _ = self.graphics()
        graphics[0].Pixbuf.new_from_file.side_effect = ValueError('invalid image')
        with mock.patch.object(image_document, '_graphics_modules', return_value=graphics):
            with self.assertRaisesRegex(image_document.ImageError, 'Could not open the image'):
                image_document.prepare_image(self.image)

    def test_file_size_limit_is_checked_before_native_decode(self):
        with mock.patch.object(image_document, 'MAX_IMAGE_BYTES', 1), \
                mock.patch.object(image_document, '_graphics_modules') as graphics:
            with self.assertRaises(image_document.ImageError):
                image_document.prepare_image(self.image)
        graphics.assert_not_called()

    def test_first_gif_frame_is_isolated_before_decoding(self):
        header = bytes.fromhex('47494638396101000100800000000000ffffff')
        image = bytes.fromhex('2c0000000001000100000202440100')
        source = self.root / 'animated.gif'
        source.write_bytes(header + image + image + b'\x3b')
        destination = self.root / 'first.gif'
        image_document._first_gif_frame(source, destination)
        self.assertEqual(destination.read_bytes(), header + image + b'\x3b')
        for invalid in [b'invalid', header + b'\x21\xf9\xff', header + b'\x3b']:
            source.write_bytes(invalid)
            with self.assertRaises(image_document.ImageError):
                image_document._first_gif_frame(source, destination)


if __name__ == '__main__':
    unittest.main(verbosity=2)
