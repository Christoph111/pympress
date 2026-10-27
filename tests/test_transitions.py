#!/usr/bin/env python3
"""Check transition bounds, compositing and lifecycle without native GTK/Cairo."""

try:
    from . import _support
except ImportError:
    import _support


import ast
import importlib.util
import sys
import types
import unittest
from unittest import mock

SOURCE = _support.SOURCE


class Surface:
    def __init__(self, format_, width, height, colour=(0., 0., 0.)):
        self.width, self.height, self.colour = width, height, colour
        self.flush = mock.Mock()

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height


class Context:
    instances = []

    def __init__(self, target):
        self.target = target
        self.source = (0., 0., 0.)
        self.scale = mock.Mock()
        self.translate = mock.Mock()
        self.save = mock.Mock()
        self.restore = mock.Mock()
        self.instances.append(self)

    def set_source_rgb(self, *colour):
        self.source = colour

    def set_source_surface(self, surface, x, y):
        self.source = surface.colour

    def paint(self):
        self.target.colour = self.source

    def paint_with_alpha(self, alpha):
        self.target.colour = tuple(new * alpha + old * (1 - alpha)
                                   for new, old in zip(self.source, self.target.colour))


class Layer:
    def __init__(self):
        self.visible = False
        self.callbacks = {}
        self.serial = 0
        for name in ('set_hexpand', 'set_vexpand', 'set_no_show_all', 'connect', 'queue_draw'):
            setattr(self, name, mock.Mock())
        self.remove_tick_callback = mock.Mock(side_effect=lambda token: self.callbacks.pop(token, None))

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def add_tick_callback(self, callback):
        self.serial += 1
        self.callbacks[self.serial] = callback
        return self.serial

    def get_allocated_width(self):
        return 1280

    def get_allocated_height(self):
        return 720


class Overlay:
    def __init__(self):
        for name in ('add', 'add_overlay', 'set_overlay_pass_through', 'show_all'):
            setattr(self, name, mock.Mock())

    def get_allocated_width(self):
        return 1280

    def get_allocated_height(self):
        return 720

    def get_scale_factor(self):
        return 2


def load_module(source):
    path = source / 'pympress' / 'transitions.py'
    spec = importlib.util.spec_from_file_location('transitions_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ui_path = source / 'pympress' / 'ui.py'
    tree = ast.parse(ui_path.read_text(encoding='utf-8'), filename=str(ui_path))
    ui = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'UI')
    methods = [node for node in ui.body if isinstance(node, ast.FunctionDef) and
               node.name in {'_prepare_transition', '_playback_entry', '_current_play_delay'}]
    selected = ast.Module(body=[ast.ClassDef(name='TransitionUI', bases=[], keywords=[], body=methods,
                                             decorator_list=[])], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(selected), str(ui_path), 'exec'), namespace)
    return module, namespace['TransitionUI']


class TransitionTests(unittest.TestCase):
    target = None
    policy_class = None

    def setUp(self):
        self.clock = 0.
        self.timeline = self.target.FadeTimeline(clock=lambda: self.clock)
        self.cairo = types.SimpleNamespace(FORMAT_RGB24=0, ImageSurface=Surface, Context=Context)
        repository = types.ModuleType('gi.repository')
        repository.Gtk = types.SimpleNamespace(Overlay=Overlay, DrawingArea=Layer)
        gi = types.ModuleType('gi')
        gi.repository = repository
        patch = mock.patch.dict(sys.modules, {'cairo': self.cairo, 'gi': gi, 'gi.repository': repository})
        patch.start()
        self.addCleanup(patch.stop)
        self.page = types.SimpleNamespace(can_render=lambda: True, get_aspect_ratio=lambda mode: 16 / 9,
                                          render_cairo=mock.Mock())
        self.ui = types.SimpleNamespace(
            c_win=types.SimpleNamespace(
                remove=mock.Mock(), add=mock.Mock()), c_frame=types.SimpleNamespace(
                get_property=lambda property_: .5), blanked=False, doc=types.SimpleNamespace(
                doc=object(), page=lambda number: self.page), current_page=0, notes_mode=types.SimpleNamespace(
                    complement=lambda: 'full'), laser=types.SimpleNamespace(
                        show_pointer=False, render_pointer=mock.Mock()))

    def fade(self):
        fade = self.target.Crossfade(self.ui)
        fade.timeline = self.timeline
        fade._snapshot = mock.Mock(return_value=Surface(0, 1280, 720))
        return fade

    def test_timeline_duration_validation_and_zero_cut(self):
        for invalid in (-.001, 5.001, float('nan'), float('inf'), '1', None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.timeline.start(invalid)
        self.timeline.start(0)
        self.assertFalse(self.timeline.active)
        self.assertEqual(self.timeline.progress, 1.)
        self.assertEqual(self.timeline.remaining, 0.)

    def test_timeline_progress_remaining_and_completion(self):
        self.timeline.start(2)
        self.clock = .5
        self.assertEqual(self.timeline.progress, .25)
        self.assertEqual(self.timeline.remaining, 1.5)
        self.clock = 4
        self.assertEqual(self.timeline.progress, 1.)
        self.assertFalse(self.timeline.active)

    def test_timeline_stop_and_restart_clear_old_deadline(self):
        self.timeline.start(5)
        self.clock = 2
        self.timeline.stop()
        self.assertEqual(self.timeline.remaining, 0.)
        self.timeline.start(1)
        self.clock = 2.5
        self.assertEqual(self.timeline.progress, .5)

    def test_pause_freezes_progress_and_resume_excludes_paused_wall_time(self):
        self.timeline.start(2.)
        self.clock = .5
        self.timeline.pause()
        self.clock = 1000.
        self.timeline.pause()
        self.assertEqual(self.timeline.progress, .25)
        self.assertEqual(self.timeline.remaining, 1.5)
        self.timeline.resume()
        self.timeline.resume()
        self.assertEqual(self.timeline.progress, .25)
        self.clock += .5
        self.assertEqual(self.timeline.progress, .5)
        self.timeline.stop()
        self.timeline.resume()
        self.assertFalse(self.timeline.active)

    def test_snapshot_cap_includes_retina_and_extreme_aspect_ratios(self):
        for width, height, scale in ((1920, 1080, 2), (7680, 4320, 3), (1e9, 1, 2), (1, 1e9, 2), (1, 1, 1)):
            with self.subTest(viewport=(width, height, scale)):
                w, h = self.target.bounded_size(width, height, scale)
                self.assertGreaterEqual(min(w, h), 1)
                self.assertLessEqual(w * h, 8_000_000)

    def test_invalid_snapshot_dimensions_are_rejected(self):
        for width, height in ((0, 1), (1, -1), (float('nan'), 2), (1, float('inf'))):
            with self.subTest(size=(width, height)), self.assertRaises(ValueError):
                self.target.bounded_size(width, height)

    def test_slide_fit_keeps_aspect_ratio_and_alignment(self):
        self.assertEqual(self.target.fit_page(1000, 1000, 2), (0., 250., 1000, 500.))
        self.assertEqual(self.target.fit_page(1000, 1000, .5, 0., 1.), (0., 0., 500., 1000.))

    def test_mix_is_opaque_linear_crossfade_without_black_dip(self):
        old, new = Surface(0, 10, 10, (1., 0., 0.)), Surface(0, 10, 10, (0., 0., 1.))
        for progress, expected in ((0., (1., 0., 0.)), (.25, (.75, 0., .25)), (1., (0., 0., 1.))):
            output = Surface(0, 10, 10)
            self.target.paint_mix(Context(output), old, new, progress, 10, 10)
            self.assertEqual(output.colour, expected)

    def test_mix_progress_is_clamped(self):
        old, new = Surface(0, 10, 10, (1., 0., 0.)), Surface(0, 10, 10, (0., 1., 0.))
        for progress, expected in ((-3., old.colour), (3., new.colour)):
            output = Surface(0, 10, 10)
            self.target.paint_mix(Context(output), old, new, progress, 20, 30)
            self.assertEqual(output.colour, expected)

    def test_overlay_does_not_intercept_audience_window_input(self):
        fade = self.fade()
        fade.overlay.set_overlay_pass_through.assert_called_once_with(fade.layer, True)
        self.assertFalse(fade.layer.visible)

    def test_prepare_then_begin_owns_two_buffers_and_one_tick(self):
        fade = self.fade()
        self.assertTrue(fade.prepare(1.))
        self.assertIsNotNone(fade.pending)
        self.assertFalse(fade.layer.visible)
        fade.begin()
        self.assertIsNone(fade.pending)
        self.assertIsNotNone(fade.old)
        self.assertIsNotNone(fade.new)
        self.assertEqual(len(fade.layer.callbacks), 1)
        self.assertTrue(fade.layer.visible)

    def test_rapid_navigation_freezes_visible_mix_and_removes_old_tick(self):
        fade = self.fade()
        fade._snapshot.side_effect = [Surface(0, 1280, 720, (1., 0., 0.)), Surface(0, 1280, 720, (0., 0., 1.))]
        fade.prepare(1.)
        fade.begin()
        token = fade.tick_id
        self.clock = .25
        self.assertTrue(fade.prepare(1.))
        self.assertEqual(fade.pending.colour, (.75, 0., .25))
        fade.layer.remove_tick_callback.assert_called_once_with(token)
        self.assertFalse(fade.layer.visible)
        self.assertIsNone(fade.old)

    def test_cancel_drops_all_buffers_and_tick(self):
        fade = self.fade()
        fade.prepare(1.)
        fade.begin()
        fade.cancel()
        self.assertEqual((fade.old, fade.new, fade.pending, fade.tick_id), (None, None, None, None))
        self.assertEqual(fade.layer.callbacks, {})
        self.assertFalse(fade.layer.visible)
        self.assertFalse(fade.timeline.active)

    def test_pause_keeps_visible_mix_and_resume_arms_one_tick(self):
        fade = self.fade()
        fade._snapshot.side_effect = [Surface(0, 1280, 720, (1., 0., 0.)), Surface(0, 1280, 720, (0., 0., 1.))]
        fade.prepare(1.)
        fade.begin()
        self.clock = .25
        fade.pause()
        fade.pause()
        self.assertEqual(fade.layer.callbacks, {})
        self.assertTrue(fade.layer.visible)
        self.clock = 1000.
        output = Surface(0, 1280, 720)
        fade._draw(fade.layer, Context(output))
        self.assertEqual(output.colour, (.75, 0., .25))
        self.assertEqual(fade.remaining, .75)
        fade.resume()
        fade.resume()
        self.assertEqual(len(fade.layer.callbacks), 1)
        self.clock += .25
        self.assertEqual(fade.timeline.progress, .5)

    def test_tick_completion_removes_overlay(self):
        fade = self.fade()
        fade.prepare(1.)
        fade.begin()
        self.clock = 1.1
        self.assertFalse(fade._tick(fade.layer, object()))
        self.assertFalse(fade.layer.visible)
        self.assertIsNone(fade.old)

    def test_zero_duration_blanking_and_empty_document_choose_cut(self):
        for reason in ('zero', 'blanked', 'empty'):
            with self.subTest(reason=reason):
                fade = self.fade()
                fade.prepare(1.)
                fade.begin()
                if reason == 'blanked':
                    self.ui.blanked = True
                if reason == 'empty':
                    self.ui.doc.doc = None
                self.assertFalse(fade.prepare(0. if reason == 'zero' else 1.))
                self.assertFalse(fade.layer.visible)
                self.assertIsNone(fade.old)
                self.ui.blanked, self.ui.doc.doc = False, object()

    def test_resize_between_old_and_new_snapshot_falls_back_to_cut(self):
        fade = self.fade()
        fade._snapshot.side_effect = [Surface(0, 1280, 720), Surface(0, 640, 360)]
        fade.prepare(1.)
        fade.begin()
        self.assertFalse(fade.layer.visible)
        self.assertIsNone(fade.old)

    def test_native_snapshot_failures_never_abort_navigation(self):
        class FakeCairoError(Exception):
            pass
        for error in (FakeCairoError('native rendering failure'),
                      MemoryError('inert allocation failure'), OverflowError()):
            for stage in ('prepare', 'begin'):
                with self.subTest(error=type(error), stage=stage):
                    fade = self.fade()
                    if stage == 'begin':
                        fade.prepare(1.)
                    fade._snapshot.side_effect = error
                    if stage == 'prepare':
                        self.assertFalse(fade.prepare(1.))
                    else:
                        fade.begin()
                    self.assertFalse(fade.layer.visible)
                    self.assertIsNone(fade.old)

    def test_laser_remains_above_crossfade_and_fits_slide_rectangle(self):
        fade = self.fade()
        fade.prepare(1.)
        fade.begin()
        self.ui.laser.show_pointer = True
        context = Context(Surface(0, 1280, 720))
        self.assertTrue(fade._draw(fade.layer, context))
        self.ui.laser.render_pointer.assert_called_once_with(context, 1280, 720.)

    def policy_ui(self):
        ui = self.policy_class()
        ui._opening_entry = None
        ui.current_page = 2
        entry = types.SimpleNamespace(transition_enabled=True, duration_for_page=mock.Mock(return_value=4.))
        ui.file_list = types.SimpleNamespace(current=entry, transition_style='fade', transition_seconds=.75)
        ui.transition = types.SimpleNamespace(prepare=mock.Mock(), remaining=.5)
        ui.playback = types.SimpleNamespace(paused=False)
        return ui

    def test_entry_override_and_cut_policy_control_fade(self):
        ui = self.policy_ui()
        ui._prepare_transition()
        ui.transition.prepare.assert_called_with(.75)
        ui.file_list.current.transition_enabled = False
        ui._prepare_transition()
        ui.transition.prepare.assert_called_with(0.)
        ui.file_list.current.transition_enabled = True
        ui.file_list.transition_style = 'cut'
        ui._prepare_transition()
        ui.transition.prepare.assert_called_with(0.)

    def test_incoming_file_uses_its_own_override_and_hold(self):
        ui = self.policy_ui()
        ui._opening_entry = types.SimpleNamespace(transition_enabled=False, duration_for_page=lambda page: 2.)
        ui._prepare_transition()
        ui.transition.prepare.assert_called_with(0.)
        self.assertEqual(ui._current_play_delay(), 2.5)

    def test_hold_delay_includes_only_remaining_fade(self):
        ui = self.policy_ui()
        self.assertEqual(ui._current_play_delay(), 4.5)
        ui.transition.remaining = 0.
        self.assertEqual(ui._current_play_delay(), 4.)

    def test_manual_navigation_while_paused_uses_cut(self):
        ui = self.policy_ui()
        ui.playback.paused = True
        ui._prepare_transition()
        ui.transition.prepare.assert_called_once_with(0.)


def setUpModule():
    TransitionTests.target, TransitionTests.policy_class = load_module(SOURCE)


if __name__ == '__main__':
    unittest.main(verbosity=2)
