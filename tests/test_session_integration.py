#!/usr/bin/env python3
"""Integrate actual slideshow UI callbacks, FileList and PlaybackController."""

try:
    from . import _support
except ImportError:
    import _support


import ast
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from test_presentation_inputs import FakeWindow, WindowState

SOURCE = _support.SOURCE


class Scheduler:
    def __init__(self):
        self.now = 100.
        self.serial = 0
        self.callbacks, self.delays = {}, {}
        self.cancelled = []

    def schedule(self, seconds, callback):
        self.serial += 1
        self.callbacks[self.serial], self.delays[self.serial] = callback, seconds
        return self.serial

    def cancel(self, token):
        self.cancelled.append(token)

    def fire(self, token=None, elapsed=0.):
        self.now += elapsed
        return self.callbacks[self.serial if token is None else token]()


class Doc:
    def __init__(self, uri, count=3):
        self.doc = object()
        self.uri, self.count, self.changed = uri, count, False

    def pages_number(self):
        return self.count

    def has_changes(self):
        return self.changed

    def goto(self, page):
        return min(self.count - 1, max(0, page))

    def notes_page(self, page):
        return None

    def page(self, number):
        if not 0 <= number < self.count:
            return None
        return types.SimpleNamespace(get_aspect_ratio=lambda mode: 16 / 9, label=lambda: str(number + 1))


class Mode:
    def __bool__(self):
        return False

    def complement(self):
        return 'full'


class Clock:
    def __init__(self):
        self.paused = True

    def pause(self):
        self.paused = True

    def unpause(self):
        self.paused = False

    def current_time(self):
        return 0.


class Fade:
    def __init__(self, clock, timeline_class):
        self.timeline = timeline_class(clock)
        self.seconds = 0.

    @property
    def remaining(self):
        return self.timeline.remaining

    def prepare(self, seconds):
        self.seconds = seconds

    def begin(self):
        self.timeline.start(self.seconds)

    def cancel(self):
        self.timeline.stop()
        self.seconds = 0.

    def pause(self):
        self.timeline.pause()

    def resume(self):
        self.timeline.resume()


def load_ui(source):
    sys.path.insert(0, str(source))
    filelist = importlib.import_module('pympress.filelist')
    playback = importlib.import_module('pympress.playback')
    transitions = importlib.import_module('pympress.transitions')
    path = source / 'pympress' / 'ui.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    ui = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'UI')
    names = {'_playback_entry', '_current_play_delay', '_playback_state_changed', 'playback_toggle',
             'playback_stop', 'playback_settings_changed', '_advance_playback', '_prepare_transition',
             'file_list_activate', 'file_list_next', 'file_list_previous', 'goto_page', 'doc_goto_next',
             'doc_goto_prev', 'do_page_change', 'cancel_current_input', 'exit_fullscreen',
             '_request_fullscreen', '_fullscreen_pending_target'}
    methods = [node for node in ui.body if isinstance(node, ast.FunctionDef) and node.name in names]
    selected = ast.Module(body=[ast.ClassDef(name='SessionUI', bases=[], keywords=[], body=methods,
                                             decorator_list=[])], type_ignores=[])
    namespace = {'Gdk': types.SimpleNamespace(WindowState=WindowState),
                 'GLib': types.SimpleNamespace(get_monotonic_time=lambda: 0)}
    exec(compile(ast.fix_missing_locations(selected), str(path), 'exec'), namespace)
    return namespace['SessionUI'], filelist, playback.PlaybackController, transitions.FadeTimeline


class SessionIntegrationTests(unittest.TestCase):
    ui_class = filelist = controller = timeline_class = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pympress-session-test-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.paths = [root / name for name in ('first.pdf', 'second.pdf', 'third.pdf')]
        for path in self.paths:
            path.write_bytes(b'inert local path fixture')
        self.scheduler = Scheduler()
        ui = self.ui_class()
        self.ui = ui
        ui.file_list = self.filelist.FileList()
        ui.file_list.append(self.paths)
        ui.file_list.set_current(0)
        ui.file_list_status = {}
        ui._opening_entry = None
        ui._playback_advancing = False
        ui.playback_window = ui.presenter_tools = None
        ui.transition = Fade(lambda: self.scheduler.now, self.timeline_class)
        ui.doc = Doc(self.paths[0].as_uri())
        ui.current_page = ui.preview_page = 0
        ui.notes_mode = Mode()
        ui.next_frames_count = 0
        ui.p_frames_next = ui.p_das_next = []
        ui.app = types.SimpleNamespace(set_action_state=mock.Mock(), get_action_state=lambda name: False)
        ui.c_win, ui.p_win = FakeWindow(), FakeWindow()
        ui.set_screensaver = mock.Mock()
        ui.page_number = types.SimpleNamespace(editing=False, update_page_numbers=mock.Mock(),
                                               try_cancel=mock.Mock(return_value=False))
        ui.est_time = types.SimpleNamespace(try_cancel=mock.Mock(return_value=False))
        ui.deck = types.SimpleNamespace(try_cancel=mock.Mock(return_value=False))
        for name in ('c_da', 'c_frame', 'p_da_notes', 'p_frame_notes', 'p_da_cur', 'p_frame_cur'):
            setattr(ui, name, types.SimpleNamespace(set_property=mock.Mock(), queue_draw=mock.Mock()))
        ui.scribbler = types.SimpleNamespace(
            page_change_exits=False, page_change=mock.Mock(), try_cancel=mock.Mock(
                return_value=False), scribble_p_frame=types.SimpleNamespace(
                set_property=mock.Mock(), queue_draw=mock.Mock()))
        ui.annotations = types.SimpleNamespace(load_annotations=mock.Mock(), try_cancel=mock.Mock(return_value=False))
        ui.cache = types.SimpleNamespace(prerender=mock.Mock())
        ui.zoom = types.SimpleNamespace(stop_zooming=mock.Mock(), try_cancel=mock.Mock(return_value=False))
        ui.medias = types.SimpleNamespace(replace_media_overlays=mock.Mock())
        ui.autoplay = types.SimpleNamespace(stop_looping=mock.Mock(return_value=False))
        ui.talk_time = Clock()
        ui.timing = types.SimpleNamespace(transition=mock.Mock())
        ui._refresh_file_list = mock.Mock()
        ui.file_list_error = mock.Mock()
        ui.show_file_list = mock.Mock()
        ui.reject_open = False

        def swap(uri):
            if ui.reject_open:
                ui._open_cancelled = True
                return False
            ui._prepare_transition()
            ui.doc = Doc(uri)
            ui.current_page = ui.preview_page = 0
            ui.talk_time.pause()
            ui.do_page_change(unpause=False)
            return True
        ui.swap_document = mock.Mock(side_effect=swap)
        ui.playback = self.controller(self.scheduler.schedule, self.scheduler.cancel, ui._advance_playback,
                                      clock=lambda: self.scheduler.now, on_change=ui._playback_state_changed)

    def test_start_pause_resume_updates_timer_and_talk_clock_together(self):
        self.assertTrue(self.ui.playback_toggle())
        self.assertTrue(self.ui.playback.running)
        self.assertFalse(self.ui.talk_time.paused)
        self.scheduler.now += 2
        self.ui.playback_toggle()
        self.assertTrue(self.ui.playback.paused)
        self.assertTrue(self.ui.talk_time.paused)
        self.ui.playback_toggle()
        self.assertEqual(self.ui.playback.remaining, 3.)
        self.assertFalse(self.ui.talk_time.paused)

    def test_manual_page_change_resets_running_duration_and_invalidates_old_callback(self):
        self.ui.file_list.update_settings(0, page_seconds={1: 7.})
        self.ui.playback_toggle()
        self.scheduler.now += 2
        self.ui.goto_page(1)
        self.assertEqual(self.ui.playback.remaining, 7.)
        self.scheduler.fire(1, elapsed=3)
        self.assertEqual(self.ui.current_page, 1)

    def test_manual_page_change_preserves_slideshow_and_talk_clock_pause(self):
        self.ui.playback_toggle()
        self.ui.playback_toggle()
        self.ui.goto_page(1)
        self.assertTrue(self.ui.playback.paused)
        self.assertTrue(self.ui.talk_time.paused)
        self.assertEqual(self.ui.current_page, 1)

    def test_right_arrow_during_slideshow_pause_navigates_without_resuming(self):
        self.ui.playback_toggle()
        self.ui.playback_toggle()
        self.ui.doc_goto_next()
        self.assertEqual(self.ui.current_page, 1)
        self.assertTrue(self.ui.playback.paused)
        self.assertTrue(self.ui.talk_time.paused)

    def test_automatic_page_advance_arms_exactly_one_next_timer(self):
        self.ui.file_list.update_settings(0, page_seconds={1: 7.})
        self.ui.playback_toggle()
        self.scheduler.fire(elapsed=5)
        self.assertEqual(self.ui.current_page, 1)
        self.assertEqual(self.scheduler.serial, 2)
        self.assertEqual(self.ui.playback.remaining, 7.)
        self.assertFalse(self.ui._playback_advancing)

    def test_current_mode_stops_at_document_end(self):
        self.ui.current_page = self.ui.preview_page = 2
        self.ui.playback_toggle()
        self.scheduler.fire(elapsed=5)
        self.assertFalse(self.ui.playback.is_active)
        self.assertEqual(self.ui.current_page, 2)
        self.ui.swap_document.assert_not_called()

    def test_list_boundary_uses_incoming_file_timing_and_transition_override(self):
        self.ui.file_list.set_playback('list', False)
        self.ui.file_list.set_transition('fade', .5)
        self.ui.file_list.update_settings(1, slide_seconds=9., transition_enabled=False)
        self.ui.current_page = self.ui.preview_page = 2
        self.ui.playback_toggle()
        self.scheduler.fire(elapsed=5)
        self.assertEqual(self.ui.file_list.current_index, 1)
        self.assertEqual(self.ui.playback.remaining, 9.)
        self.assertEqual(self.scheduler.serial, 2)
        self.assertEqual(self.ui.transition.remaining, 0.)
        self.assertIsNone(self.ui._opening_entry)

    def test_dirty_document_stops_at_file_boundary_without_prompt_or_swap(self):
        self.ui.file_list.set_playback('list', True)
        self.ui.current_page = self.ui.preview_page = 2
        self.ui.doc.changed = True
        self.ui.playback_toggle()
        self.scheduler.fire(elapsed=5)
        self.assertFalse(self.ui.playback.is_active)
        self.assertEqual(self.ui.file_list.current_index, 0)
        self.ui.swap_document.assert_not_called()

    def test_canceled_automatic_file_open_stops_and_retains_current_document(self):
        self.ui.file_list.set_playback('list', False)
        self.ui.current_page = self.ui.preview_page = 2
        old = self.ui.doc
        self.ui.reject_open = True
        self.ui.playback_toggle()
        self.scheduler.fire(elapsed=5)
        self.assertIs(self.ui.doc, old)
        self.assertEqual(self.ui.file_list.current_index, 0)
        self.assertFalse(self.ui.playback.is_active)
        self.assertIsNone(self.ui._opening_entry)
        self.assertFalse(self.ui._playback_advancing)

    def test_list_playback_skips_missing_files(self):
        self.ui.file_list.set_playback('list', False)
        self.paths[1].unlink()
        self.ui.current_page = self.ui.preview_page = 2
        self.ui.playback_toggle()
        self.scheduler.fire(elapsed=5)
        self.assertEqual(self.ui.file_list.current_index, 2)

    def test_stop_invalidates_callback_and_pauses_talk_clock(self):
        self.ui.playback_toggle()
        self.ui.playback_stop()
        self.scheduler.fire(1, elapsed=50)
        self.assertFalse(self.ui.playback.is_active)
        self.assertTrue(self.ui.talk_time.paused)
        self.assertEqual(self.ui.current_page, 0)

    def test_canceled_manual_file_open_preserves_live_timer(self):
        self.ui.playback_toggle()
        self.scheduler.now += 2
        self.ui.reject_open = True
        self.assertFalse(self.ui.file_list_activate(1))
        self.assertEqual(self.ui.playback.remaining, 3.)
        self.assertTrue(self.ui.playback.running)
        self.assertEqual(self.ui.file_list.current_index, 0)

    def test_pause_during_fade_freezes_both_fade_and_hold_until_resume(self):
        self.ui.file_list.set_transition('fade', 2.)
        self.ui.playback_toggle()
        self.ui.goto_page(1)
        self.scheduler.now += .5
        self.ui.playback_toggle()
        self.assertEqual(self.ui.transition.remaining, 1.5)
        self.assertEqual(self.ui.playback.remaining, 6.5)
        self.scheduler.now += 100.
        self.assertEqual(self.ui.transition.remaining, 1.5)
        self.assertEqual(self.ui.playback.remaining, 6.5)
        self.ui.playback_toggle()
        self.scheduler.now += 1.5
        self.assertEqual(self.ui.transition.remaining, 0.)
        self.assertEqual(self.ui.playback.remaining, 5.)

    def test_manual_navigation_while_paused_cuts_and_starts_fresh_hold(self):
        self.ui.file_list.set_transition('fade', 2.)
        self.ui.playback_toggle()
        self.ui.goto_page(1)
        self.ui.playback_toggle()
        self.ui.goto_page(2)
        self.assertTrue(self.ui.playback.paused)
        self.assertTrue(self.ui.talk_time.paused)
        self.assertEqual(self.ui.transition.remaining, 0.)
        self.assertEqual(self.ui.playback.remaining, 5.)

    def test_escape_stops_slideshow_and_exits_fullscreen_in_same_press(self):
        self.ui.playback_toggle()
        self.ui.c_win.state = self.ui.p_win.state = WindowState.FULLSCREEN
        self.assertTrue(self.ui.cancel_current_input(None))
        self.assertFalse(self.ui.playback.is_active)
        self.assertTrue(self.ui.talk_time.paused)
        self.ui.c_win.unfullscreen.assert_called_once()
        self.ui.p_win.unfullscreen.assert_called_once()


def setUpModule():
    (SessionIntegrationTests.ui_class, SessionIntegrationTests.filelist,
     SessionIntegrationTests.controller, SessionIntegrationTests.timeline_class) = load_ui(SOURCE)


if __name__ == '__main__':
    unittest.main(verbosity=2)
