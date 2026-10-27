#!/usr/bin/env python3
"""Deterministic slideshow timer checks; no GTK, sleeping or user documents."""

try:
    from . import _support
except ImportError:
    import _support


import unittest
from unittest import mock

from pympress.playback import PlaybackController

SOURCE = _support.SOURCE


class Scheduler:
    def __init__(self):
        self.now = 100.0
        self.next_id = 0
        self.callbacks = {}
        self.delays = {}
        self.cancelled = []

    def schedule(self, seconds, callback):
        self.next_id += 1
        self.callbacks[self.next_id] = callback
        self.delays[self.next_id] = seconds
        return self.next_id

    def cancel(self, handle):
        self.cancelled.append(handle)

    def fire(self, handle=None, elapsed=0):
        self.now += elapsed
        handle = self.next_id if handle is None else handle
        return self.callbacks[handle]()


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = Scheduler()
        self.advance = mock.Mock(return_value=3)
        self.changes = mock.Mock()
        self.player = PlaybackController(self.scheduler.schedule, self.scheduler.cancel,
                                         self.advance, clock=lambda: self.scheduler.now,
                                         on_change=self.changes)

    def test_deadline_advances_once_and_uses_next_slide_duration(self):
        self.player.start(5, mode='list', loop=True)
        self.assertEqual(self.player.state, 'running')
        self.assertEqual(self.player.remaining, 5)
        self.assertFalse(self.scheduler.fire(elapsed=5))
        self.advance.assert_called_once_with('list', True)
        self.assertEqual(self.scheduler.delays[2], 3)
        self.assertEqual(self.player.remaining, 3)
        self.scheduler.callbacks[1]()
        self.advance.assert_called_once()

    def test_early_timeout_rearms_without_early_page_advance(self):
        self.player.start(5)
        self.scheduler.fire(elapsed=3.75)
        self.advance.assert_not_called()
        self.assertEqual(self.scheduler.delays[2], 1.25)
        self.scheduler.fire(elapsed=1.25)
        self.advance.assert_called_once_with('current', False)

    def test_pause_preserves_remaining_time_and_resume_uses_it(self):
        self.player.start(5)
        self.scheduler.now += 2.25
        self.player.pause()
        self.assertTrue(self.player.paused)
        self.assertEqual(self.player.remaining, 2.75)
        self.assertEqual(self.scheduler.cancelled, [1])
        self.scheduler.now += 10000
        self.scheduler.callbacks[1]()
        self.advance.assert_not_called()
        self.assertEqual(self.player.remaining, 2.75)
        self.player.resume()
        self.assertEqual(self.scheduler.delays[2], 2.75)
        self.scheduler.fire(elapsed=2.75)
        self.advance.assert_called_once()

    def test_repeated_pause_resume_are_idempotent(self):
        self.player.start(5)
        self.player.pause()
        self.player.pause()
        self.player.resume()
        self.player.resume()
        self.assertEqual(self.scheduler.next_id, 2)
        self.assertEqual(self.scheduler.cancelled, [1])

    def test_stop_invalidates_all_callbacks_and_reports_stopped(self):
        self.player.start(5)
        self.player.stop()
        self.assertFalse(self.player.is_active)
        self.assertEqual(self.player.remaining, 0)
        self.scheduler.fire(1, elapsed=10)
        self.advance.assert_not_called()
        count = self.changes.call_count
        self.player.stop()
        self.assertEqual(self.changes.call_count, count)

    def test_manual_navigation_resets_running_duration(self):
        self.player.start(5)
        self.scheduler.now += 2
        self.player.reset(8)
        self.assertEqual(self.player.remaining, 8)
        self.scheduler.fire(1, elapsed=3)
        self.advance.assert_not_called()
        self.scheduler.fire(2, elapsed=5)
        self.advance.assert_called_once()

    def test_manual_navigation_preserves_paused_state(self):
        self.player.start(5)
        self.player.pause()
        self.player.reset(9)
        self.assertTrue(self.player.paused)
        self.assertEqual(self.player.remaining, 9)
        self.assertEqual(self.scheduler.next_id, 1)
        self.player.resume()
        self.assertEqual(self.scheduler.delays[2], 9)

    def test_reset_does_not_start_a_stopped_slideshow(self):
        self.player.reset(9)
        self.assertEqual(self.player.state, 'stopped')
        self.assertEqual(self.scheduler.next_id, 0)

    def test_restart_invalidates_previous_mode_and_callback(self):
        self.player.start(5, mode='current')
        self.player.start(2, mode='list', loop=True)
        self.scheduler.fire(1, elapsed=5)
        self.advance.assert_not_called()
        self.scheduler.fire(2)
        self.advance.assert_called_once_with('list', True)

    def test_end_of_show_stops_without_scheduling_more_work(self):
        self.advance.return_value = None
        self.player.start(1)
        self.scheduler.fire(elapsed=1)
        self.assertEqual(self.player.state, 'stopped')
        self.assertEqual(self.scheduler.next_id, 1)
        self.assertEqual(self.player.remaining, 0)

    def test_navigation_that_resets_the_clock_does_not_create_two_timers(self):
        def move_page(_mode, _loop):
            self.player.reset(7)
            return 3
        self.advance.side_effect = move_page
        self.player.start(1)
        self.scheduler.fire(elapsed=1)
        self.assertEqual(self.scheduler.next_id, 2)
        self.assertEqual(self.scheduler.delays[2], 7)
        self.assertEqual(self.player.remaining, 7)

    def test_navigation_can_stop_or_pause_without_being_rearmed(self):
        for action in (self.player.stop, self.player.pause):
            with self.subTest(action=action.__name__):
                self.advance.side_effect = lambda _mode, _loop: (action(), 3)[1]
                self.player.start(1)
                before = self.scheduler.next_id
                self.scheduler.fire(elapsed=1)
                self.assertEqual(self.scheduler.next_id, before)
                self.assertFalse(self.player.running)

    def test_navigation_failure_stops_and_preserves_exception(self):
        self.advance.side_effect = RuntimeError('navigation failed')
        self.player.start(1)
        with self.assertRaisesRegex(RuntimeError, 'navigation failed'):
            self.scheduler.fire(elapsed=1)
        self.assertEqual(self.player.state, 'stopped')

    def test_invalid_timing_and_mode_never_replace_a_live_timer(self):
        self.player.start(5)
        for seconds in [False, None, '5', 0, -1, 0.09, 86401, float('nan'), float('inf'), 10 ** 1000]:
            with self.subTest(seconds=repr(seconds)[:40]), self.assertRaises(ValueError):
                self.player.start(seconds)
        for mode, loop in [('other', False), ('list', 1)]:
            with self.assertRaises(ValueError):
                self.player.start(5, mode, loop)
        self.assertEqual(self.scheduler.next_id, 1)
        self.assertEqual(self.player.remaining, 5)

    def test_invalid_next_slide_duration_stops_safely(self):
        self.advance.return_value = float('nan')
        self.player.start(1)
        with self.assertRaises(ValueError):
            self.scheduler.fire(elapsed=1)
        self.assertEqual(self.player.state, 'stopped')

    def test_failed_scheduler_does_not_leave_a_running_state(self):
        self.player._schedule = mock.Mock(side_effect=RuntimeError('schedule failed'))
        with self.assertRaisesRegex(RuntimeError, 'schedule failed'):
            self.player.start(1)
        self.assertEqual(self.player.state, 'stopped')
        self.assertIsNone(self.player._deadline)


if __name__ == '__main__':
    unittest.main(verbosity=2)
