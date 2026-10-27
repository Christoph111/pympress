#!/usr/bin/env python3
"""Run real queue/document callback regressions with mocked native boundaries."""

try:
    from . import _support
except ImportError:
    import _support


import enum
import gc
import importlib.util
import logging
import pathlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from test_presentation_inputs import extracted_class, FakeApplication, FakeWindow, Variant, WindowState

SOURCE = _support.SOURCE


class Mode(enum.IntEnum):
    NONE = 0
    RIGHT = 1
    BOTTOM = 2

    def direction(self):
        return self.name.lower()


class FakeNativeError(Exception):
    pass


class NativeDocument:
    def __init__(self, uri=None, pages=3, guessed_mode=Mode.NONE):
        self.uri = uri
        self.doc = object() if uri else None
        self.page_labels = [str(index + 1) for index in range(pages)]
        self.guess_notes = mock.Mock(return_value=guessed_mode)
        self.set_notes_pos = mock.Mock()

    def pages_number(self):
        return len(self.page_labels)

    def goto(self, page):
        return max(0, min(page, self.pages_number() - 1)) if self.page_labels else -1

    def has_labels(self):
        return False

    def get_structure(self):
        return {}


class Clock:
    def __init__(self):
        self.paused = False
        self.resets = 0

    def pause(self):
        self.paused = True

    def unpause(self):
        self.paused = False

    def reset_timer(self):
        self.resets += 1


def load_integration(source):
    path = source / 'pympress' / 'filelist.py'
    spec = importlib.util.spec_from_file_location('transition_filelist_under_test', path)
    filelist = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = filelist
    spec.loader.exec_module(filelist)
    dependencies = types.SimpleNamespace(create=mock.Mock(), prepare=mock.Mock(), recent=mock.Mock())
    globals_ = {
        'filelist': filelist,
        'image_document': types.SimpleNamespace(
            prepare_image=dependencies.prepare),
        'document': types.SimpleNamespace(
            Document=types.SimpleNamespace(
                create=dependencies.create),
            PdfPage=Mode),
        'GLib': types.SimpleNamespace(
            Error=FakeNativeError,
            get_monotonic_time=lambda: 0,
            Variant=Variant),
        'Gdk': types.SimpleNamespace(
            WindowState=WindowState,
            KEY_Escape=65307,
            EventType=types.SimpleNamespace(
                KEY_PRESS=1,
                KEY_RELEASE=2)),
        'Gtk': types.SimpleNamespace(
            RecentManager=types.SimpleNamespace(
                get_default=lambda: dependencies.recent)),
        'pathlib': pathlib,
        'gc': gc,
        'logger': logging.getLogger('document_transitions_under_test'),
        '_': lambda text: text}
    methods = {'swap_document', 'reload_document', 'file_list_add_paths', 'file_list_activate',
               'file_list_remove', 'file_list_move', 'file_list_clear', 'file_list_load',
               'file_list_next', '_next_file_index', 'file_list_previous', 'goto_page',
               'doc_goto_next', 'doc_goto_prev',
               'on_key_input', 'cancel_current_input', 'exit_fullscreen', 'on_window_state_event',
               '_request_fullscreen', '_fullscreen_pending_target'}
    ui_class = extracted_class(source / 'pympress' / 'ui.py', 'UI', methods, globals_)
    return ui_class, filelist, dependencies


class DocumentTransitionTests(unittest.TestCase):
    ui_class = None
    filelist_module = None
    dependencies = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pympress-queue-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.paths = [self.directory / filename for filename in ('first.pdf', 'second.pdf', 'third.pdf')]
        for path in self.paths:
            path.write_bytes(b'inert existing path; native parser is mocked')
        self.dependencies.create.reset_mock(side_effect=True)
        self.dependencies.prepare.reset_mock(side_effect=True)
        self.dependencies.recent.reset_mock()
        self.created = {}
        self.failures = set()

        def create(ui, uri):
            if uri in self.failures:
                raise FakeNativeError('inert native parsing failure')
            return self.created.get(uri, NativeDocument(uri, pages=3 if uri else 0))
        self.dependencies.create.side_effect = create

    def ui(self):
        ui = self.ui_class()
        ui.file_list = self.filelist_module.FileList()
        ui.file_list.append(self.paths)
        ui.file_list.set_current(0)
        ui.file_list_status = {}
        ui.file_list_window = None
        ui._refresh_file_list = mock.Mock()
        ui.file_list_error = mock.Mock()
        ui.error_opening_file = mock.Mock()
        ui.unsaved_changes = mock.Mock(return_value=False)
        ui.source_uri = self.paths[0].as_uri()
        ui.image_document = None
        ui.doc = NativeDocument(ui.source_uri)
        ui.current_page = ui.preview_page = 2
        ui.notes_mode = Mode.NONE
        ui.config = types.SimpleNamespace(get=lambda section, key: 'right' if key == 'horizontal' else 'bottom')
        ui.app = FakeApplication()
        ui.app.activate_action = mock.Mock()
        ui.c_win, ui.p_win = FakeWindow(), FakeWindow()
        ui.set_screensaver = mock.Mock()
        ui.switch_mode = mock.Mock(side_effect=lambda action, target_mode: setattr(ui, 'notes_mode', target_mode))
        ui.file_watcher = types.SimpleNamespace(watch_file=mock.Mock(), stop_watching=mock.Mock())
        ui.cache = types.SimpleNamespace(swap_document=mock.Mock())
        ui.page_number = types.SimpleNamespace(
            editing=False, set_last=mock.Mock(),
            enable_labels=mock.Mock(),
            setup_doc_callbacks=mock.Mock(),
            try_cancel=mock.Mock(return_value=False))
        ui.autoplay = types.SimpleNamespace(set_doc_pages=mock.Mock(), stop_looping=mock.Mock(return_value=False))
        for name in ('est_time', 'zoom', 'scribbler', 'deck', 'annotations'):
            setattr(ui, name, types.SimpleNamespace(try_cancel=mock.Mock(return_value=False),
                                                    key_event=mock.Mock(return_value=False),
                                                    setup_doc_callbacks=mock.Mock()))
        ui.medias = types.SimpleNamespace(purge_media_overlays=mock.Mock())
        ui.timing = types.SimpleNamespace(set_document_metadata=mock.Mock())
        ui.talk_time = Clock()
        ui.do_page_change = mock.Mock()
        return ui

    def snapshot(self, ui):
        return (ui.doc, ui.source_uri, ui.image_document, ui.current_page, ui.preview_page,
                tuple(entry.id for entry in ui.file_list.entries), ui.file_list.current_index)

    def prepared_image(self, path):
        path.write_bytes(b'inert image fixture')
        prepared = types.SimpleNamespace(source_path=path, uri=(self.directory / 'prepared.pdf').as_uri(),
                                         cleanup=mock.Mock())
        self.dependencies.prepare.return_value = prepared
        return prepared

    def test_cancel_swap_keeps_all_current_state_and_does_no_open_work(self):
        ui = self.ui()
        before = self.snapshot(ui)
        ui.unsaved_changes.return_value = True
        self.assertFalse(ui.swap_document(self.paths[1].as_uri()))
        self.assertEqual(self.snapshot(ui), before)
        self.dependencies.create.assert_not_called()
        self.dependencies.prepare.assert_not_called()
        ui.file_watcher.watch_file.assert_not_called()

    def test_failed_pdf_open_preserves_active_document_and_selection(self):
        ui = self.ui()
        before = self.snapshot(ui)
        self.failures.add(self.paths[1].as_uri())
        self.assertFalse(ui.file_list_activate(1))
        self.assertEqual(self.snapshot(ui), before)
        self.assertEqual(ui.file_list_status[ui.file_list.entries[1].id], 'Could not open file')
        ui.cache.swap_document.assert_not_called()

    def test_cancel_does_not_mark_queued_file_as_corrupt(self):
        ui = self.ui()
        ui.unsaved_changes.return_value = True
        self.assertFalse(ui.file_list_activate(1))
        self.assertEqual(ui.file_list_status, {})
        self.assertEqual(ui.file_list.current_index, 0)

    def test_removing_active_file_can_be_canceled_without_mutating_queue(self):
        ui = self.ui()
        before = self.snapshot(ui)
        ui.unsaved_changes.return_value = True
        self.assertFalse(ui.file_list_remove([0, 1]))
        self.assertEqual(self.snapshot(ui), before)

    def test_clearing_queue_can_be_canceled_without_mutating_current_state(self):
        ui = self.ui()
        before = self.snapshot(ui)
        ui.unsaved_changes.return_value = True
        self.assertFalse(ui.file_list_clear())
        self.assertEqual(self.snapshot(ui), before)

    def test_loading_list_can_be_canceled_without_replacing_current_queue(self):
        ui = self.ui()
        replacement = self.filelist_module.FileList()
        replacement.append([self.paths[2]])
        list_path = self.directory / 'replacement.json'
        replacement.save(list_path)
        before = self.snapshot(ui)
        ui.unsaved_changes.return_value = True
        self.assertFalse(ui.file_list_load(list_path))
        self.assertEqual(self.snapshot(ui), before)

    def test_moving_active_file_preserves_document_and_entry_identity(self):
        ui = self.ui()
        original, entry = ui.doc, ui.file_list.current
        self.assertTrue(ui.file_list_move(0, 2))
        self.assertIs(ui.doc, original)
        self.assertEqual(ui.file_list.current, entry)
        self.assertEqual(ui.file_list.current_index, 2)
        self.dependencies.create.assert_not_called()

    def test_successful_image_open_watches_source_and_releases_previous_image(self):
        ui = self.ui()
        previous = types.SimpleNamespace(cleanup=mock.Mock())
        ui.image_document = previous
        image = self.directory / 'new.png'
        prepared = self.prepared_image(image)
        self.assertTrue(ui.swap_document(image.as_uri()))
        self.assertEqual(ui.source_uri, image.as_uri())
        self.assertIs(ui.image_document, prepared)
        self.assertEqual(ui.doc.uri, prepared.uri)
        ui.file_watcher.watch_file.assert_called_once_with(image.as_uri(), ui.reload_document)
        previous.cleanup.assert_called_once()
        prepared.cleanup.assert_not_called()

    def test_failed_new_image_open_releases_only_new_temporary_image(self):
        ui = self.ui()
        previous = types.SimpleNamespace(cleanup=mock.Mock())
        ui.image_document = previous
        image = self.directory / 'bad.png'
        prepared = self.prepared_image(image)
        self.failures.add(prepared.uri)
        before = self.snapshot(ui)
        self.assertFalse(ui.swap_document(image.as_uri()))
        self.assertEqual(self.snapshot(ui), before)
        prepared.cleanup.assert_called_once()
        previous.cleanup.assert_not_called()

    def test_portrait_image_is_not_split_into_pdf_notes(self):
        ui = self.ui()
        image = self.directory / 'portrait.png'
        prepared = self.prepared_image(image)
        self.created[prepared.uri] = NativeDocument(prepared.uri, pages=1, guessed_mode=Mode.BOTTOM)
        self.assertTrue(ui.swap_document(image.as_uri()))
        self.assertEqual(ui.notes_mode, Mode.NONE)

    def test_end_of_document_advances_queue_and_keeps_playback_running(self):
        ui = self.ui()
        ui.doc_goto_next()
        self.assertEqual(ui.file_list.current_index, 1)
        self.assertEqual(ui.source_uri, self.paths[1].as_uri())
        self.assertEqual((ui.current_page, ui.preview_page), (0, 0))
        self.assertFalse(ui.talk_time.paused)

    def test_paused_document_first_right_starts_clock_without_changing_file(self):
        ui = self.ui()
        ui.talk_time.paused = True
        before = self.snapshot(ui)
        ui.doc_goto_next()
        self.assertEqual(self.snapshot(ui), before)
        self.assertFalse(ui.talk_time.paused)

    def test_first_page_previous_opens_previous_files_last_page(self):
        ui = self.ui()
        self.assertTrue(ui.file_list_activate(1))
        ui.doc_goto_prev()
        self.assertEqual(ui.file_list.current_index, 0)
        self.assertEqual(ui.source_uri, self.paths[0].as_uri())
        self.assertEqual((ui.current_page, ui.preview_page), (2, 2))

    def test_advance_skips_missing_queue_entry(self):
        ui = self.ui()
        self.paths[1].unlink()
        ui.doc_goto_next()
        self.assertEqual(ui.file_list.current_index, 2)
        self.assertEqual(ui.source_uri, self.paths[2].as_uri())

    def test_cancel_end_transition_keeps_original_slide_and_queue_selection(self):
        ui = self.ui()
        before = self.snapshot(ui)
        ui.unsaved_changes.return_value = True
        ui.doc_goto_next()
        self.assertEqual(self.snapshot(ui), before)

    def test_escape_press_and_release_cancels_only_editor(self):
        ui = self.ui()
        ui.c_win.state = WindowState.FULLSCREEN
        ui.page_number.try_cancel.side_effect = [True, False]
        for event_type in (1, 2):
            ui.on_key_input(ui.p_win, types.SimpleNamespace(keyval=65307, type=event_type))
        ui.page_number.try_cancel.assert_called_once()
        ui.c_win.unfullscreen.assert_not_called()

    def test_escape_press_and_release_requests_fullscreen_exit_once(self):
        ui = self.ui()
        ui.c_win.state = ui.p_win.state = WindowState.FULLSCREEN
        for event_type in (1, 2):
            ui.on_key_input(ui.p_win, types.SimpleNamespace(keyval=65307, type=event_type))
        ui.c_win.unfullscreen.assert_called_once()
        ui.p_win.unfullscreen.assert_called_once()

    def test_native_fullscreen_exit_updates_action_and_releases_inhibition(self):
        ui = self.ui()
        ui.app.set_action_state('content-fullscreen', True)
        event = types.SimpleNamespace(changed_mask=WindowState.FULLSCREEN, new_window_state=0)
        ui.on_window_state_event(ui.c_win, event)
        self.assertFalse(ui.app.get_action_state('content-fullscreen'))
        ui.set_screensaver.assert_called_once_with(disabled=False)


def setUpModule():
    cls, filelist, dependencies = load_integration(SOURCE)
    DocumentTransitionTests.ui_class = cls
    DocumentTransitionTests.filelist_module = filelist
    DocumentTransitionTests.dependencies = dependencies


if __name__ == '__main__':
    unittest.main(verbosity=2)
