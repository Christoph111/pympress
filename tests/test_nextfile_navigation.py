#!/usr/bin/env python3
"""Exercise actual UI navigation and preview plans without native GTK or codecs.

Navigation uses the real FileList and PlaybackController through the shared
session fixture. Preview tests additionally execute the actual UI planner with
passive provider/frame doubles; native rendering has its own provider tests.
"""

try:
    from . import _support
except ImportError:
    import _support


import unittest
from unittest import mock

from test_session_integration import Doc, SessionFixture, load_ui


SOURCE = _support.SOURCE


class NavigationTests(SessionFixture):
    def at_file_end(self):
        self.ui.current_page = self.ui.preview_page = self.ui.doc.pages_number() - 1
        self.ui.talk_time.unpause()

    def test_manual_next_uses_legacy_enabled_default_at_file_end(self):
        self.at_file_end()
        self.ui.doc_goto_next()
        self.assertEqual(self.ui.file_list.current_index, 1)
        self.assertEqual(self.ui.current_page, 0)
        self.ui.swap_document.assert_called_once_with(self.paths[1].as_uri())

    def test_manual_next_stops_for_global_policy_until_entry_override_allows_it(self):
        self.at_file_end()
        self.ui.file_list.set_continuation(False)
        self.ui.doc_goto_next()
        self.assertEqual(self.ui.file_list.current_index, 0)
        self.assertEqual(self.ui.current_page, 2)
        self.ui.swap_document.assert_not_called()
        self.ui.file_list.set_entry_continuation([0], True)
        self.ui.doc_goto_next()
        self.assertEqual(self.ui.file_list.current_index, 1)

    def test_manual_next_obeys_file_stop_and_reset_to_enabled_global(self):
        self.at_file_end()
        self.ui.file_list.set_entry_continuation([0], False)
        self.ui.doc_goto_next()
        self.ui.swap_document.assert_not_called()
        self.assertEqual(self.ui.current_page, 2)
        self.ui.file_list.set_entry_continuation([0], None)
        self.ui.doc_goto_next()
        self.assertEqual(self.ui.file_list.current_index, 1)

    def test_continuation_does_not_block_pages_within_the_current_file(self):
        self.ui.file_list.set_continuation(False)
        self.ui.talk_time.unpause()
        self.ui.doc_goto_next()
        self.assertEqual(self.ui.current_page, 1)
        self.assertEqual(self.ui.file_list.current_index, 0)
        self.ui.swap_document.assert_not_called()

    def test_explicit_next_file_ignores_automatic_continuation_policy(self):
        self.ui.file_list.set_continuation(False)
        self.ui.file_list.set_entry_continuation([0], False)
        self.assertTrue(self.ui.file_list_next())
        self.assertEqual(self.ui.file_list.current_index, 1)
        self.ui.swap_document.assert_called_once_with(self.paths[1].as_uri())

    def test_page_number_preview_editing_does_not_activate_another_file(self):
        self.at_file_end()
        self.ui.page_number.editing = True
        self.ui.doc_goto_next()
        self.assertEqual(self.ui.file_list.current_index, 0)
        self.assertEqual(self.ui.current_page, 2)
        self.ui.swap_document.assert_not_called()

    def test_list_playback_stops_at_disabled_file_boundary(self):
        self.at_file_end()
        self.ui.file_list.set_playback('list', True)
        self.ui.file_list.set_entry_continuation([0], False)
        self.ui.playback_toggle()
        self.scheduler.fire(elapsed=5)
        self.assertFalse(self.ui.playback.is_active)
        self.assertEqual(self.ui.file_list.current_index, 0)
        self.assertEqual(self.ui.current_page, 2)
        self.ui.swap_document.assert_not_called()

    def test_list_playback_can_override_disabled_global_continuation(self):
        self.at_file_end()
        self.ui.file_list.set_playback('list', False)
        self.ui.file_list.set_continuation(False)
        self.ui.file_list.set_entry_continuation([0], True)
        self.ui.playback_toggle()
        self.scheduler.fire(elapsed=5)
        self.assertTrue(self.ui.playback.running)
        self.assertEqual(self.ui.file_list.current_index, 1)

    def test_current_file_loop_ignores_disabled_continuation(self):
        self.at_file_end()
        self.ui.file_list.set_playback('current', True)
        self.ui.file_list.set_continuation(False)
        self.ui.file_list.set_entry_continuation([0], False)
        self.ui.playback_toggle()
        self.scheduler.fire(elapsed=5)
        self.assertTrue(self.ui.playback.running)
        self.assertEqual(self.ui.current_page, 0)
        self.assertEqual(self.ui.file_list.current_index, 0)
        self.ui.swap_document.assert_not_called()

    def test_list_loop_wraps_to_first_existing_file(self):
        self.ui.file_list.set_current(2)
        self.ui.doc = Doc(self.paths[2].as_uri())
        self.at_file_end()
        self.paths[0].unlink()
        self.ui.file_list.set_playback('list', True)
        self.ui.playback_toggle()
        self.scheduler.fire(elapsed=5)
        self.assertTrue(self.ui.playback.running)
        self.assertEqual(self.ui.file_list.current_index, 1)
        self.ui.swap_document.assert_called_once_with(self.paths[1].as_uri())

    def test_next_file_resolver_respects_order_missing_files_and_incoming_identity(self):
        self.paths[1].unlink()
        original = self.ui.file_list.current
        self.assertEqual(self.ui._next_file_index(), 2)
        self.ui.file_list.move(2, 1)
        self.assertEqual(self.ui._next_file_index(), 1)
        self.ui._opening_entry = self.ui.file_list.entries[1]
        self.assertIsNone(self.ui._next_file_index())
        self.assertEqual(self.ui._next_file_index(loop=True), 0)
        self.assertIs(self.ui.file_list.current, original)
        self.ui.swap_document.assert_not_called()


class Frame:
    def __init__(self):
        self.label = mock.Mock()
        self.label_container = object()
        self.ratio = self.tooltip = None

    def get_label_widget(self):
        return self.label_container

    def set_tooltip_text(self, value):
        self.tooltip = value

    def set_property(self, name, value):
        if name != 'ratio':
            raise AssertionError('Unexpected frame property')
        self.ratio = value


class Provider:
    def __init__(self, page_count=2):
        self.page_count = page_count
        self.error = None
        self.selected = []
        self.pages = []

    def get(self, entry):
        self.selected.append(entry)
        return self

    def aspect(self, page):
        if not 0 <= page < self.page_count:
            raise AssertionError('Preview requested an out-of-range page')
        self.pages.append(page)
        return 1.25


class PreviewPlanningTests(SessionFixture):
    def setUp(self):
        super().setUp()
        ui = self.ui
        del ui._refresh_next_preview
        ui.next_frames_count = 4
        ui.p_frames_next = [Frame() for _ in range(4)]
        ui._next_frame_label_widgets = [frame.label for frame in ui.p_frames_next]
        ui.p_das_next = [mock.Mock() for _ in range(4)]
        ui._next_frame_labels = ['Next slide', 'Slide +2', 'Slide +3', 'Slide +4']
        ui.next_file_preview = Provider()

    def test_remaining_current_pages_are_followed_by_named_external_first_pages(self):
        self.ui._refresh_next_preview()
        incoming = self.ui.file_list.entries[1]
        self.assertEqual(self.ui._next_preview_pages, [(None, 1), (None, 2), (incoming, 0), (incoming, 1)])
        self.assertEqual(self.ui.next_file_preview.selected, [incoming])
        self.assertEqual(self.ui.next_file_preview.pages, [0, 1])
        self.assertEqual(self.ui.p_frames_next[0].tooltip, 'Next slide')
        self.assertEqual(self.ui.p_frames_next[2].tooltip, 'Next file: second.pdf · page 1')
        self.assertEqual(self.ui.p_frames_next[3].tooltip, 'Next file: second.pdf · page 2')
        self.ui._next_frame_label_widgets[2].set_text.assert_called_once_with('Next file: second.pdf · page 1')
        self.assertEqual(self.ui.p_frames_next[0].ratio, 16 / 9)
        self.assertEqual(self.ui.p_frames_next[2].ratio, 1.25)

    def test_disabled_policy_clears_external_preview_until_override_is_reset(self):
        self.ui.preview_page = 2
        self.ui.file_list.set_continuation(False)
        self.ui._refresh_next_preview()
        self.assertEqual(self.ui._next_preview_pages, [(None, None)] * 4)
        self.assertEqual(self.ui.next_file_preview.selected, [None])
        self.ui.file_list.set_continuation(True)
        self.ui.file_list.set_entry_continuation([0], False)
        self.ui._refresh_next_preview()
        self.assertEqual(self.ui._next_preview_pages, [(None, None)] * 4)
        self.ui.file_list.set_entry_continuation([0], None)
        self.ui._refresh_next_preview()
        incoming = self.ui.file_list.entries[1]
        self.assertEqual(self.ui._next_preview_pages[0], (incoming, 0))

    def test_visible_frame_count_and_external_page_bounds_are_respected(self):
        self.ui.preview_page = 2
        self.ui.next_frames_count = 3
        self.ui.next_file_preview.page_count = 1
        self.ui._refresh_next_preview()
        incoming = self.ui.file_list.entries[1]
        self.assertEqual(self.ui._next_preview_pages,
                         [(incoming, 0), (incoming, None), (incoming, None), (None, None)])
        self.assertEqual(self.ui.next_file_preview.pages, [0])

    def test_only_current_pages_do_not_open_an_external_provider(self):
        self.ui.next_frames_count = 1
        self.ui._refresh_next_preview()
        self.assertEqual(self.ui._next_preview_pages, [(None, 1)] + [(None, None)] * 3)
        self.assertEqual(self.ui.next_file_preview.selected, [None])

    def test_current_file_playback_stop_and_loop_have_matching_preview_plans(self):
        self.ui.preview_page = self.ui.current_page = 2
        self.ui.file_list.set_continuation(False)
        self.ui.playback.start(5, mode='current', loop=False)
        self.assertEqual(self.ui._next_preview_pages, [(None, None)] * 4)
        self.ui.playback.loop = True
        self.ui._refresh_next_preview()
        self.assertEqual(self.ui._next_preview_pages, [(None, 0), (None, 1), (None, 2), (None, 0)])
        self.assertEqual(self.ui.next_file_preview.selected, [None, None])

    def test_playback_stop_restores_the_manual_next_file_preview(self):
        self.ui.preview_page = self.ui.current_page = 2
        self.ui.playback.start(5, mode='current', loop=False)
        self.assertEqual(self.ui._next_preview_pages, [(None, None)] * 4)
        self.ui.playback.pause()
        self.assertEqual(self.ui._next_preview_pages, [(None, None)] * 4)
        self.ui.playback.stop()
        self.assertEqual(self.ui._next_preview_pages[0], (self.ui.file_list.entries[1], 0))

    def test_boundary_preview_skips_missing_files_in_current_list_order(self):
        self.paths[1].unlink()
        self.ui.preview_page = 2
        self.ui._refresh_next_preview()
        incoming = self.ui.file_list.entries[2]
        self.assertEqual(self.ui._next_preview_pages[0], (incoming, 0))
        self.assertEqual(self.ui.p_frames_next[0].tooltip, 'Next file: third.pdf · page 1')
        self.ui.file_list.move(2, 1)
        self.ui._refresh_next_preview()
        self.assertEqual(self.ui._next_preview_pages[0], (incoming, 0))
        self.assertEqual(self.ui.file_list.current_index, 0)

    def test_list_playback_loop_wraps_preview_without_selecting_the_first_file(self):
        self.ui.file_list.set_current(2)
        self.ui.doc = Doc(self.paths[2].as_uri())
        self.ui.preview_page = self.ui.current_page = 2
        self.ui.playback.start(5, mode='list', loop=True)
        self.ui._refresh_next_preview()
        incoming = self.ui.file_list.entries[0]
        self.assertEqual(self.ui._next_preview_pages[:2], [(incoming, 0), (incoming, 1)])
        self.assertEqual(self.ui.file_list.current_index, 2)
        self.ui.playback.loop = False
        self.ui._refresh_next_preview()
        self.assertEqual(self.ui._next_preview_pages, [(None, None)] * 4)

    def test_many_frames_share_one_provider_selection_per_refresh(self):
        self.ui.preview_page = 2
        self.ui._refresh_next_preview()
        self.ui._refresh_next_preview()
        incoming = self.ui.file_list.entries[1]
        self.assertEqual(self.ui.next_file_preview.selected, [incoming, incoming])
        self.assertEqual(self.ui.next_file_preview.pages, [0, 1, 0, 1])
        self.assertEqual([frame.tooltip for frame in self.ui.p_frames_next[:2]],
                         ['Next file: second.pdf · page 1', 'Next file: second.pdf · page 2'])

    def test_opening_entry_uses_its_policy_and_following_file_before_selection_commits(self):
        self.ui.preview_page = 2
        self.ui.file_list.set_continuation(False)
        self.ui.file_list.set_entry_continuation([1], True)
        self.ui._opening_entry = self.ui.file_list.entries[1]
        self.ui._refresh_next_preview()
        incoming = self.ui.file_list.entries[2]
        self.assertEqual(self.ui._next_preview_pages[0], (incoming, 0))
        self.assertEqual(self.ui.file_list.current_index, 0)

    def test_unavailable_preview_retains_filename_hint_without_page_requests(self):
        self.ui.preview_page = 2
        self.ui.next_file_preview.page_count = 0
        self.ui.next_file_preview.error = 'Decoder rejected fixture'
        self.ui._refresh_next_preview()
        self.assertEqual(self.ui._next_preview_pages[0], (self.ui.file_list.entries[1], None))
        self.assertEqual(self.ui.p_frames_next[0].tooltip, 'Next file: second.pdf · Preview unavailable')
        self.assertEqual(self.ui.next_file_preview.pages, [])

    def test_aspect_failure_immediately_marks_the_preview_unavailable(self):
        self.ui.preview_page = 2
        provider = self.ui.next_file_preview

        def fail_aspect(page):
            provider.error = 'Invalid native page geometry'
            provider.page_count = 0
            return 1.0

        provider.aspect = mock.Mock(side_effect=fail_aspect)
        self.ui._refresh_next_preview()
        incoming = self.ui.file_list.entries[1]
        self.assertEqual(self.ui._next_preview_pages, [(incoming, None)] * 4)
        provider.aspect.assert_called_once_with(0)
        for frame, label in zip(self.ui.p_frames_next, self.ui._next_frame_label_widgets):
            self.assertEqual(frame.tooltip, 'Next file: second.pdf · Preview unavailable')
            label.set_text.assert_called_once_with('Next file: second.pdf · Preview unavailable')

    def test_empty_document_placeholder_does_not_load_an_external_preview(self):
        placeholder = Doc(None, count=1).page(0)
        for count in (0, 1):
            with self.subTest(count=count):
                self.ui.doc = Doc(None, count=count)
                self.ui.doc.doc = None
                self.ui.doc.page = mock.Mock(return_value=placeholder)
                self.ui.current_page = self.ui.preview_page = -1 if count == 0 else 0
                self.ui.next_file_preview.selected.clear()
                self.ui._refresh_next_preview()
                self.assertEqual(self.ui._next_preview_pages, [(None, None)] * 4)
                self.assertEqual(self.ui.next_file_preview.selected, [None])
                self.assertEqual(self.ui.next_file_preview.pages, [])

    def test_refresh_does_not_change_document_selection_timer_or_active_cache(self):
        self.ui.preview_page = 2
        self.ui.playback.start(5, mode='list', loop=True)
        doc, cache, current = self.ui.doc, self.ui.cache, self.ui.file_list.current
        page_state = self.ui.current_page, self.ui.preview_page
        controller_state = self.ui.playback.state, self.ui.playback.remaining
        timer_state = (self.scheduler.serial, dict(self.scheduler.callbacks),
                       dict(self.scheduler.delays), list(self.scheduler.cancelled))
        self.ui._refresh_next_preview()
        self.assertIs(self.ui.doc, doc)
        self.assertIs(self.ui.cache, cache)
        self.assertIs(self.ui.file_list.current, current)
        self.assertEqual((self.ui.current_page, self.ui.preview_page), page_state)
        self.assertEqual((self.ui.playback.state, self.ui.playback.remaining), controller_state)
        self.assertEqual((self.scheduler.serial, self.scheduler.callbacks, self.scheduler.delays,
                          self.scheduler.cancelled), timer_state)
        self.ui.cache.prerender.assert_not_called()
        self.ui.swap_document.assert_not_called()


def setUpModule():
    (NavigationTests.ui_class, NavigationTests.filelist,
     NavigationTests.controller, NavigationTests.timeline_class) = load_ui(SOURCE)
    (PreviewPlanningTests.ui_class, PreviewPlanningTests.filelist,
     PreviewPlanningTests.controller, PreviewPlanningTests.timeline_class) = load_ui(
        SOURCE, extra_methods={'_refresh_next_preview'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
