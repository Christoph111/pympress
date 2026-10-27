#!/usr/bin/env python3
"""Exercise native output ownership and fade timing with deterministic boundaries.

The real controller and opacity model run unchanged. Display/window operations,
GLib scheduling and the optional AppKit bridge are replaced at their boundaries;
these cases do not establish actual window stacking, opacity or Space behavior.
"""

try:
    from . import _support
except ImportError:
    import _support

import configparser
import copy
import enum
import re
import types
import unittest
from unittest import mock

from pympress import output

SOURCE = _support.SOURCE


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Scheduler:
    def __init__(self, clock):
        self.clock = clock
        self.next_id = 0
        self.jobs = {}
        self.added = []
        self.removed = []

    def timeout_add(self, interval, callback):
        self.next_id += 1
        self.jobs[self.next_id] = callback
        self.added.append((self.next_id, interval))
        return self.next_id

    def source_remove(self, source):
        self.removed.append(source)
        self.jobs.pop(source, None)

    def advance(self, seconds):
        self.clock.now += seconds
        for source, callback in list(self.jobs.items()):
            if source in self.jobs and not callback():
                self.jobs.pop(source, None)


class State(enum.IntFlag):
    FULLSCREEN = 1
    ABOVE = 2
    MAXIMIZED = 4


class Monitor:
    def __init__(self, name, x, y, width, height):
        self.name = name
        self.geometry = types.SimpleNamespace(x=x, y=y, width=width, height=height)

    def get_geometry(self):
        return self.geometry

    def get_model(self):
        return self.name

    def get_manufacturer(self):
        return 'Display vendor'


class Display:
    def __init__(self):
        self.monitors = [Monitor('Presenter', 0, 0, 1920, 1080), Monitor('Audience', 1920, -180, 1280, 720)]
        self.callbacks = {}
        self.disconnected = []

    def get_n_monitors(self):
        return len(self.monitors)

    def get_monitor(self, index):
        return self.monitors[index] if 0 <= index < len(self.monitors) else None

    def get_monitor_at_window(self, native):
        x, y = native.window.position
        for monitor in self.monitors:
            rect = monitor.geometry
            if rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height:
                return monitor
        return self.monitors[0] if self.monitors else None

    def connect(self, signal, callback):
        self.callbacks[1] = callback
        return 1

    def disconnect(self, identifier):
        self.callbacks.pop(identifier)
        self.disconnected.append(identifier)

    def remove(self, index):
        monitor = self.monitors.pop(index)
        for callback in list(self.callbacks.values()):
            callback(self, monitor)


class WaylandDisplay(Display):
    pass


class X11Display(Display):
    pass


class Native:
    def __init__(self, window):
        self.window = window
        self.state = 0
        self.opacity = 1.0
        self.opacities = []

    def get_state(self):
        return self.state | (State.ABOVE if self.window.flags['keep_above'] else 0)

    def set_opacity(self, value):
        self.opacity = value
        self.opacities.append(value)
        self.window.calls.append(('opacity', value))


class Window:
    def __init__(self, display):
        self.display = display
        self.visible = True
        self.position = (20, 40)
        self.size = (960, 540)
        self.flags = dict(decorated=True, resizable=True, accept_focus=True, focus_on_map=True,
                          skip_pager_hint=False, skip_taskbar_hint=False, type_hint='normal', keep_above=False)
        self.calls = []
        self.native = Native(self)
        self.screen = types.SimpleNamespace(is_composited=lambda: True)
        self.fullscreen = mock.Mock()
        self.fullscreen_on_monitor = mock.Mock()
        self.unfullscreen = mock.Mock()

    def __getattr__(self, name):
        if name.startswith('get_') and name[4:] in self.flags:
            return lambda: self.flags[name[4:]]
        if name.startswith('set_') and name[4:] in self.flags:
            def setter(value):
                self.flags[name[4:]] = value
                self.calls.append((name, value))
            return setter
        raise AttributeError(name)

    def get_display(self):
        return self.display

    def get_window(self):
        return self.native

    def get_screen(self):
        return self.screen

    def realize(self):
        self.calls.append(('realize',))

    def get_visible(self):
        return self.visible

    def get_size(self):
        return self.size

    def get_position(self):
        return self.position

    def show_all(self):
        self.visible = True
        self.calls.append(('show',))

    def hide(self):
        self.visible = False
        self.calls.append(('hide',))

    def unmaximize(self):
        self.native.state &= ~State.MAXIMIZED
        self.calls.append(('unmaximize',))

    def move(self, x, y):
        self.position = (x, y)
        self.calls.append(('move', x, y))

    def resize(self, width, height):
        self.size = (width, height)
        self.calls.append(('resize', width, height))

    def parse_geometry(self, geometry):
        match = re.fullmatch(r'(\d+)x(\d+)([+-]\d+)([+-]\d+)', geometry)
        if match:
            width, height, x, y = map(int, match.groups())
            self.size, self.position = (width, height), (x, y)
        self.calls.append(('geometry', geometry))


class OpacityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.fade = output.OpacityFade(self.clock)

    def test_full_fade_has_exact_endpoints_and_monotonic_midpoint(self):
        self.fade.start(0, 1, 2)
        self.assertEqual(self.fade.value, 0)
        self.clock.now = 1
        self.assertEqual(self.fade.value, 0.5)
        self.assertTrue(self.fade.active)
        self.clock.now = 2
        self.assertEqual(self.fade.value, 1)
        self.assertFalse(self.fade.active)
        self.clock.now = 20
        self.assertEqual(self.fade.value, 1)

    def test_reverse_uses_current_opacity_and_preserves_full_span_velocity(self):
        self.fade.start(0, 1, 2)
        self.clock.now = 0.5
        current = self.fade.value
        self.fade.start(current, 0, 2)
        self.assertEqual(self.fade.value, 0.25)
        self.assertEqual(self.fade.duration, 0.5)
        self.clock.now = 0.75
        self.assertEqual(self.fade.value, 0.125)
        self.clock.now = 1
        self.assertEqual(self.fade.value, 0)
        self.assertFalse(self.fade.active)

    def test_zero_duration_and_unchanged_target_have_no_active_fade(self):
        self.fade.start(0, 1, 0)
        self.assertEqual(self.fade.value, 1)
        self.assertFalse(self.fade.active)
        self.fade.start(0.4, 0.4, 5)
        self.assertEqual(self.fade.value, 0.4)
        self.assertFalse(self.fade.active)

    def test_invalid_arguments_leave_an_existing_fade_unchanged(self):
        self.fade.start(0.2, 0.8, 3)
        before = vars(self.fade).copy()
        for args in [(-0.1, 1, 1), (0, 1.1, 1), (0, 1, -1), (0, 1, 6),
                     (float('nan'), 1, 1), (0, 1, float('inf')), (0, 'opaque', 1)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.fade.start(*args)
            self.assertEqual(vars(self.fade), before)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.scheduler = Scheduler(self.clock)
        self.display = Display()
        self.window = Window(self.display)
        self.original_flags = self.window.flags.copy()
        self.config = configparser.ConfigParser()
        self.config['content'] = {'geometry': '960x540+20+40'}
        self.pending = None
        self.actions = {}
        self.ui = types.SimpleNamespace(
            c_win=self.window, config=self.config, transition=types.SimpleNamespace(cancel=mock.Mock()),
            set_screensaver=mock.Mock(), output_window=types.SimpleNamespace(refresh_status=mock.Mock()))
        self.ui.app = types.SimpleNamespace(set_action_state=mock.Mock(side_effect=self.set_action))
        self.ui._fullscreen_pending_target = mock.Mock(side_effect=lambda window: self.pending)
        self.ui._request_fullscreen = mock.Mock(side_effect=self.request_fullscreen)
        self.restore_native = mock.Mock()
        runtime = mock.patch.object(output, '_runtime', return_value=(
            types.SimpleNamespace(WindowState=State), self.scheduler))
        runtime.start()
        self.addCleanup(runtime.stop)
        bridge = mock.patch('pympress.macos_overlay.prepare_overlay', return_value=self.restore_native)
        self.prepare_native = bridge.start()
        self.addCleanup(bridge.stop)
        self.controller = output.OutputController(self.ui, self.clock)

    def test_unavailable_saved_overlay_display_stays_invalid_until_explicit_choice(self):
        self.config.set('content', 'output_mode', 'overlay')
        self.config.set('content', 'output_monitor', '9')
        controller = output.OutputController(self.ui, self.clock)
        self.assertEqual(controller.monitor_index, 9)
        self.assertFalse(controller.monitor_available)
        before = list(self.window.calls)
        with self.assertRaises(output.OutputError):
            controller.show()
        self.assertEqual(self.window.calls, before)
        self.assertIn('unavailable', controller.status_text)
        controller.apply('overlay', 1, 0)
        self.assertTrue(controller.monitor_available)
        self.assertEqual(self.window.position, (1920, -180))
        controller.cleanup()

    def test_implicit_default_can_choose_the_only_available_display(self):
        self.display.monitors = self.display.monitors[:1]
        controller = output.OutputController(self.ui, self.clock)
        self.assertFalse(controller.explicit_mode)
        self.assertEqual(controller.monitor_index, 0)
        self.assertTrue(controller.monitor_available)
        controller.cleanup()

    def test_hiding_fullscreen_does_not_forget_the_selected_output_mode(self):
        self.controller.apply('fullscreen', 1, 0)
        self.window.native.state = State.FULLSCREEN
        self.pending = None
        self.controller.hide()
        self.window.native.state = 0
        self.pending = None
        self.controller.native_state_changed(False)
        self.assertEqual(self.controller.mode, 'fullscreen')
        self.controller.show()
        self.ui._request_fullscreen.assert_called_with(self.window, True, 1)

    def test_monitor_reordering_keeps_the_physical_output_and_fade(self):
        self.overlay(1)
        source = self.controller._source
        self.display.remove(0)
        self.assertEqual(self.controller.monitor_index, 0)
        self.assertIs(self.controller._selected_monitor, self.display.monitors[0])
        self.assertEqual(self.controller._source, source)
        self.assertTrue(self.controller.visible)

    def test_removing_first_output_does_not_reuse_its_index_for_another_display(self):
        self.controller.apply('overlay', 0, 0)
        self.display.remove(0)
        self.assertFalse(self.controller.visible)
        self.assertEqual(self.controller.monitor_index, -1)
        with self.assertRaises(output.OutputError):
            self.controller.show()
        self.assertFalse(self.window.visible)

    def test_native_fullscreen_positions_normal_window_before_entry(self):
        def enter(screen, monitor):
            self.assertEqual(monitor, 1)
            self.assertIs(self.display.get_monitor_at_window(self.window.native), self.display.monitors[1])
        self.window.fullscreen_on_monitor.side_effect = enter
        self.controller.apply('fullscreen', 1, 0)
        self.window.fullscreen_on_monitor.assert_called_once()

    def test_repeated_show_during_native_entry_does_not_cancel_or_repeat_it(self):
        self.controller.apply('fullscreen', 1, 0)
        self.controller.show()
        self.controller.apply('fullscreen', 1, 0)
        self.window.fullscreen_on_monitor.assert_called_once()
        self.window.unfullscreen.assert_not_called()

    def test_changing_fullscreen_monitor_waits_for_exit_before_relocating(self):
        self.window.native.state = State.FULLSCREEN
        self.controller.apply('fullscreen', 1, 0)
        self.assertEqual(self.window.position, (20, 40))
        self.window.fullscreen_on_monitor.assert_not_called()
        self.window.native.state = 0
        self.pending = None
        self.controller.native_state_changed(False)
        self.assertEqual(self.controller.mode, 'fullscreen')
        self.scheduler.advance(.03)
        self.assertIs(self.display.get_monitor_at_window(self.window.native), self.display.monitors[1])
        self.window.fullscreen_on_monitor.assert_called_once_with(self.window.screen, 1)

    def test_window_placement_waits_for_native_fullscreen_exit(self):
        self.window.native.state = State.FULLSCREEN
        self.controller.apply('window', 1, 0)
        self.assertEqual(self.window.position, (20, 40))
        self.window.native.state = 0
        self.pending = None
        self.scheduler.advance(.03)
        self.assertIs(self.display.get_monitor_at_window(self.window.native), self.display.monitors[1])
        self.assertEqual(self.controller.mode, 'window')

    def set_action(self, name, value):
        self.actions[name] = value

    def request_fullscreen(self, window, enabled, monitor=None):
        self.pending = enabled
        if enabled:
            window.fullscreen_on_monitor(window.screen, monitor) if monitor is not None else window.fullscreen()
        else:
            window.unfullscreen()
        return True

    def overlay(self, seconds=0.5):
        self.controller.apply('overlay', 1, seconds)

    def test_overlay_uses_selected_logical_bounds_and_no_native_fullscreen(self):
        self.overlay()
        self.window.fullscreen.assert_not_called()
        self.window.fullscreen_on_monitor.assert_not_called()
        self.ui._request_fullscreen.assert_not_called()
        self.assertIn(('move', 1920, -180), self.window.calls)
        self.assertIn(('resize', 1280, 720), self.window.calls)
        self.assertFalse(self.window.flags['decorated'])
        self.assertFalse(self.window.flags['accept_focus'])
        self.assertFalse(self.window.flags['focus_on_map'])
        self.assertTrue(self.window.flags['keep_above'])
        self.prepare_native.assert_called_once_with(self.window.native)
        self.assertEqual(self.window.native.opacity, 0)
        self.scheduler.advance(0.5)
        self.assertEqual(self.window.native.opacity, 1)
        self.assertEqual(self.scheduler.jobs, {})

    def test_repeated_show_keeps_one_fade_and_original_deadline(self):
        self.overlay(seconds=1)
        source = self.controller._source
        self.scheduler.advance(0.25)
        self.assertEqual(self.window.native.opacity, 0.25)
        self.controller.show()
        self.assertEqual(self.controller._source, source)
        self.assertEqual(self.controller.fade.started, 0)
        self.assertEqual(len(self.scheduler.jobs), 1)
        self.scheduler.advance(0.75)
        self.assertEqual(self.window.native.opacity, 1)
        self.assertIsNone(self.controller._source)

    def test_applying_identical_overlay_settings_keeps_one_existing_fade(self):
        self.overlay(seconds=1)
        source = self.controller._source
        self.scheduler.advance(0.25)
        self.overlay(seconds=1)
        self.assertEqual(self.controller._source, source)
        self.assertEqual(self.controller.fade.started, 0)
        self.assertEqual(self.window.native.opacity, 0.25)
        self.assertEqual(len(self.scheduler.jobs), 1)
        self.prepare_native.assert_called_once_with(self.window.native)
        self.scheduler.advance(0.75)
        self.assertEqual(self.window.native.opacity, 1)

    def test_hide_reverses_from_current_opacity_at_constant_speed(self):
        self.overlay(seconds=2)
        self.scheduler.advance(0.5)
        previous = self.controller._source
        self.controller.hide()
        self.assertEqual(self.window.native.opacity, 0.25)
        self.assertIn(previous, self.scheduler.removed)
        self.assertEqual(self.controller.fade.duration, 0.5)
        self.assertEqual(len(self.scheduler.jobs), 1)
        self.scheduler.advance(0.25)
        self.assertEqual(self.window.native.opacity, 0.125)
        self.scheduler.advance(0.25)
        self.assertEqual(self.window.native.opacity, 0)
        self.assertFalse(self.window.visible)
        self.ui.set_screensaver.assert_called_with(disabled=False)

    def test_hidden_window_is_unmapped_only_at_exact_zero_and_can_reopen(self):
        self.overlay(seconds=1)
        self.scheduler.advance(1)
        self.controller.hide()
        self.assertFalse(self.controller.visible)
        self.assertTrue(self.window.visible)
        self.scheduler.advance(0.5)
        self.assertTrue(self.window.visible)
        self.assertEqual(self.window.native.opacity, 0.5)
        self.scheduler.advance(0.5)
        self.assertFalse(self.window.visible)
        self.assertEqual(self.window.native.opacity, 0)
        self.controller.show()
        self.assertTrue(self.window.visible)
        self.assertEqual(self.window.native.opacity, 0)
        self.assertEqual(len(self.scheduler.jobs), 1)
        self.scheduler.advance(1)
        self.assertEqual(self.window.native.opacity, 1)

    def test_pending_native_exit_defers_decorations_and_geometry_until_acknowledged(self):
        self.window.native.state = State.FULLSCREEN
        self.overlay()
        self.ui._request_fullscreen.assert_called_once_with(self.window, False)
        self.assertTrue(self.controller._waiting)
        self.assertEqual(self.window.flags, self.original_flags)
        self.assertFalse(any(call[0] in ('move', 'resize') for call in self.window.calls))
        self.assertEqual(self.window.native.opacity, 0)
        self.scheduler.advance(0.03)
        self.assertTrue(self.controller._waiting)
        self.window.native.state = 0
        self.pending = None
        self.scheduler.advance(0.03)
        self.assertFalse(self.controller._waiting)
        self.assertIn(('move', 1920, -180), self.window.calls)
        self.assertFalse(self.window.flags['decorated'])
        self.assertEqual(len(self.scheduler.jobs), 1)

    def test_pending_native_entry_is_cancelled_even_before_native_flag_is_set(self):
        self.pending = True
        self.overlay()
        self.ui._request_fullscreen.assert_called_once_with(self.window, False)
        self.assertTrue(self.controller._waiting)
        self.assertFalse(any(call[0] == 'move' for call in self.window.calls))

    def test_repeated_show_waiting_for_native_exit_retains_one_deadline(self):
        self.window.native.state = State.FULLSCREEN
        self.overlay()
        source, deadline_start = self.controller._wait_source, self.controller._wait_started
        self.clock.now = 2
        self.controller.show()
        self.assertEqual(self.controller._wait_source, source)
        self.assertEqual(self.controller._wait_started, deadline_start)
        self.assertEqual(len(self.scheduler.jobs), 1)

    def test_waiting_for_native_exit_cancels_existing_opacity_callback(self):
        self.overlay(seconds=2)
        self.scheduler.advance(0.5)
        old_source = self.controller._source
        self.pending = True
        self.controller.show()
        self.assertIsNone(self.controller._source)
        self.assertNotIn(old_source, self.scheduler.jobs)
        self.assertEqual(self.window.native.opacity, 0)
        self.assertEqual(len(self.scheduler.jobs), 1)

    def test_failed_native_exit_hides_output_at_bounded_deadline(self):
        self.window.native.state = State.FULLSCREEN
        self.overlay()
        self.scheduler.advance(6.01)
        self.assertFalse(self.window.visible)
        self.assertFalse(self.controller.visible)
        self.assertFalse(self.controller._waiting)
        self.assertEqual(self.scheduler.jobs, {})
        self.assertIn('Could not leave native fullscreen', self.controller.status_text)
        self.ui.set_screensaver.assert_called_with(disabled=False)

    def test_selected_monitor_disconnection_hides_and_restores_native_behavior(self):
        self.overlay(seconds=2)
        self.scheduler.advance(0.25)
        self.display.remove(1)
        self.assertEqual(self.window.native.opacity, 0)
        self.assertFalse(self.window.visible)
        self.assertFalse(self.controller.visible)
        self.assertEqual(self.controller.mode, 'window')
        self.assertEqual(self.window.flags, self.original_flags)
        self.assertIsNone(self.controller._selected_monitor)
        self.assertEqual(self.scheduler.jobs, {})
        self.restore_native.assert_called_once_with()
        self.assertIn('disconnected', self.controller.status_text)

    def test_removing_unselected_monitor_does_not_hide_current_output(self):
        self.overlay()
        self.display.remove(0)
        self.assertTrue(self.window.visible)
        self.assertTrue(self.controller.visible)
        self.restore_native.assert_not_called()

    def test_switching_to_normal_window_restores_all_owned_flags_and_geometry(self):
        self.overlay()
        self.scheduler.advance(0.5)
        self.controller.apply('window', 0, 0.5)
        self.assertEqual(self.window.flags, self.original_flags)
        self.assertIn(('geometry', '960x540+20+40'), self.window.calls)
        self.assertEqual(self.window.size, (960, 540))
        self.restore_native.assert_called_once_with()
        self.assertEqual(self.window.native.opacity, 1)
        self.assertTrue(self.window.visible)
        self.assertEqual(self.scheduler.jobs, {})
        self.window.fullscreen.assert_not_called()
        self.window.fullscreen_on_monitor.assert_not_called()

    def test_invalid_settings_preserve_running_output_and_configuration(self):
        self.overlay(seconds=2)
        self.scheduler.advance(0.25)
        before = (self.controller.mode, self.controller.monitor_index, self.controller.fade_seconds,
                  self.controller._source, copy.deepcopy(dict(self.config['content'])),
                  self.window.flags.copy(), self.window.calls[:], self.scheduler.jobs.copy())
        bad = [('unknown', 1, 0.5), ('overlay', True, 0.5), ('overlay', -1, 0.5),
               ('overlay', 2, 0.5), ('overlay', 1, float('nan')), ('overlay', 1, float('inf')),
               ('overlay', 1, -1), ('overlay', 1, 6), ('overlay', 1, True), ('overlay', 1, 'slow')]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(output.OutputError):
                self.controller.apply(*args)
            after = (self.controller.mode, self.controller.monitor_index, self.controller.fade_seconds,
                     self.controller._source, dict(self.config['content']), self.window.flags.copy(),
                     self.window.calls[:], self.scheduler.jobs.copy())
            self.assertEqual(after, before)

    def test_unsupported_overlay_desktops_are_rejected_before_window_mutation(self):
        for display in (WaylandDisplay(), X11Display()):
            with self.subTest(display=type(display).__name__):
                self.controller.display = display
                self.window.screen.is_composited = lambda: False
                before = self.window.calls[:]
                with self.assertRaises(output.OutputError):
                    self.overlay()
                self.assertEqual(self.window.calls, before)
                self.assertEqual(self.controller.mode, 'window')
                self.assertEqual(dict(self.config['content']), {'geometry': '960x540+20+40'})

    def test_cleanup_cancels_callbacks_and_restores_native_properties_once(self):
        self.overlay(seconds=2)
        source = self.controller._source
        self.controller.cleanup()
        self.assertEqual(self.scheduler.jobs, {})
        self.assertIn(source, self.scheduler.removed)
        self.assertEqual(self.window.flags, self.original_flags)
        self.restore_native.assert_called_once_with()
        self.assertEqual(self.display.disconnected, [1])
        self.ui.set_screensaver.assert_called_with(disabled=False)

    def test_cleanup_cancels_native_exit_poll_without_showing_deferred_overlay(self):
        self.window.native.state = State.FULLSCREEN
        self.overlay()
        self.controller.cleanup()
        self.window.native.state = 0
        self.pending = None
        self.scheduler.advance(1)
        self.assertEqual(self.scheduler.jobs, {})
        self.assertFalse(any(call[0] == 'move' for call in self.window.calls))
        self.prepare_native.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
