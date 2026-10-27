#!/usr/bin/env python3
"""Exercise actual pointer handlers and presenter/content coordinate mapping."""

try:
    from . import _support
except ImportError:
    import _support


import ast
import enum
import types
import unittest
from unittest import mock

SOURCE = _support.SOURCE


class EventType(enum.IntEnum):
    BUTTON_PRESS = 1
    BUTTON_RELEASE = 2
    ENTER_NOTIFY = 3
    LEAVE_NOTIFY = 4
    MOTION_NOTIFY = 5


class Widget:
    def __init__(self, width=640, height=360):
        self.width, self.height = width, height
        self.mouse_x = self.mouse_y = -1

    def get_allocated_width(self):
        return self.width

    def get_allocated_height(self):
        return self.height

    def get_window(self):
        return self

    def get_pointer(self):
        return types.SimpleNamespace(x=self.mouse_x, y=self.mouse_y)


class TextVariant:
    def __init__(self, value):
        self.value = value

    def get_string(self):
        return self.value

    @classmethod
    def new_string(cls, value):
        return cls(value)


def event(kind, x=160, y=270, button=1, ctrl=False):
    return types.SimpleNamespace(type=kind, button=button, get_coords=lambda: (x, y),
                                 get_state=lambda: 4 if ctrl else 0)


def load_callbacks(source):
    path = source / 'pympress' / 'pointer.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.Import) and any(alias.name == 'gi' for alias in node.names):
            continue
        if isinstance(node, ast.ImportFrom) and node.module in {'gi.repository', 'pympress'}:
            continue
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and
                isinstance(node.value.func, ast.Attribute) and
                isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == 'gi'):
            continue
        keep.append(node)
    tree.body = keep
    pixbuf = types.SimpleNamespace(get_width=lambda: 12, get_height=lambda: 12)
    cursor, paint = mock.Mock(), mock.Mock()
    namespace = {
        'Gdk': types.SimpleNamespace(
            EventType=EventType, ModifierType=types.SimpleNamespace(
                CONTROL_MASK=4), cairo_set_source_pixbuf=paint), 'GdkPixbuf': types.SimpleNamespace(
            Pixbuf=lambda: pixbuf), 'GLib': types.SimpleNamespace(
                    Variant=TextVariant), 'extras': types.SimpleNamespace(
                        Cursor=types.SimpleNamespace(
                            set_cursor=cursor)), '_': lambda text: text}
    exec(compile(ast.fix_missing_locations(tree), str(path), 'exec'), namespace)
    ui_path = source / 'pympress' / 'ui.py'
    ui_tree = ast.parse(ui_path.read_text(encoding='utf-8'), filename=str(ui_path))
    ui = next(node for node in ui_tree.body if isinstance(node, ast.ClassDef) and node.name == 'UI')
    method = next(node for node in ui.body if isinstance(node, ast.FunctionDef) and node.name == 'track_clicks')
    selected = ast.Module(body=[ast.ClassDef(name='InputUI', bases=[], keywords=[], body=[method],
                                             decorator_list=[])], type_ignores=[])
    exec(compile(ast.fix_missing_locations(selected), str(ui_path), 'exec'), namespace)
    return types.SimpleNamespace(Pointer=namespace['Pointer'], Mode=namespace['PointerMode'],
                                 UI=namespace['InputUI'], cursor=cursor, paint=paint)


class PointerInteractionTests(unittest.TestCase):
    target = None

    def setUp(self):
        self.target.cursor.reset_mock()
        self.target.paint.reset_mock()
        pointer = self.target.Pointer.__new__(self.target.Pointer)
        pointer.config = types.SimpleNamespace(set=mock.Mock())
        pointer.p_da_cur, pointer.c_da = Widget(), Widget(1920, 1080)
        pointer.pointer_mode = self.target.Mode.MANUAL
        pointer._manual_pointer_widget = None
        pointer.show_pointer = False
        pointer.pointer_pos = (.5, .5)
        pointer.redraw_current_slide = mock.Mock()
        self.pointer = pointer

    def press(self, widget=None, **kwargs):
        return self.pointer.toggle_pointer(widget or self.pointer.p_da_cur, event(EventType.BUTTON_PRESS, **kwargs))

    def ui(self):
        ui = self.target.UI()
        ui.laser = self.pointer
        ui.zoom = types.SimpleNamespace(toggle_zoom_target=mock.Mock(return_value=False))
        ui.scribbler = types.SimpleNamespace(toggle_scribble=mock.Mock(return_value=False))
        ui.click_link = mock.Mock(return_value=False)
        return ui

    def test_left_hold_on_presenter_needs_no_modifier(self):
        self.assertTrue(self.press())
        self.assertTrue(self.pointer.show_pointer)
        self.assertEqual(self.pointer.pointer_pos, (.25, .75))

    def test_normalized_preview_point_is_rendered_at_same_content_position(self):
        self.press()
        context = types.SimpleNamespace(paint=mock.Mock())
        self.pointer.render_pointer(context, 640, 360)
        self.assertEqual(self.target.paint.call_args.args[2:], (154., 264.))
        self.pointer.render_pointer(context, 1920, 1080)
        self.assertEqual(self.target.paint.call_args.args[2:], (474., 804.))

    def test_drag_motion_updates_and_clamps_to_slide_bounds(self):
        self.press()
        self.assertTrue(self.pointer.track_pointer(self.pointer.p_da_cur,
                                                   event(EventType.MOTION_NOTIFY, x=-50, y=500)))
        self.assertEqual(self.pointer.pointer_pos, (0., 1.))

    def test_other_widget_motion_does_not_move_active_manual_laser(self):
        self.press()
        before = self.pointer.pointer_pos
        self.assertFalse(self.pointer.track_pointer(self.pointer.c_da,
                                                    event(EventType.MOTION_NOTIFY, x=1000, y=200)))
        self.assertFalse(self.pointer.track_pointer(Widget(), event(EventType.MOTION_NOTIFY)))
        self.assertEqual(self.pointer.pointer_pos, before)

    def test_release_hides_and_restores_cursor_of_original_widget(self):
        self.press()
        self.assertTrue(self.pointer.toggle_pointer(self.pointer.c_da, event(EventType.BUTTON_RELEASE)))
        self.assertFalse(self.pointer.show_pointer)
        self.target.cursor.assert_called_with(self.pointer.p_da_cur, 'parent')

    def test_releasing_other_button_does_not_cancel_left_hold(self):
        self.press()
        self.assertFalse(self.pointer.toggle_pointer(self.pointer.p_da_cur,
                                                     event(EventType.BUTTON_RELEASE, button=3)))
        self.assertTrue(self.pointer.show_pointer)

    def test_leaving_preview_hides_and_cancels_manual_laser(self):
        self.press()
        self.assertTrue(self.pointer.track_enter_leave(self.pointer.p_da_cur, event(EventType.LEAVE_NOTIFY)))
        self.assertFalse(self.pointer.show_pointer)
        self.assertFalse(self.pointer.track_enter_leave(self.pointer.p_da_cur, event(EventType.ENTER_NOTIFY)))
        self.assertFalse(self.pointer.track_pointer(self.pointer.p_da_cur, event(EventType.MOTION_NOTIFY)))

    def test_focus_loss_cancellation_is_idempotent(self):
        self.press()
        self.assertTrue(self.pointer.cancel_manual_pointer(object(), object()))
        self.assertFalse(self.pointer.show_pointer)
        self.assertFalse(self.pointer.cancel_manual_pointer())

    def test_content_clicks_retain_links_unless_control_is_pressed(self):
        self.assertFalse(self.press(self.pointer.c_da))
        self.assertFalse(self.pointer.show_pointer)
        self.assertTrue(self.press(self.pointer.c_da, ctrl=True, x=960, y=540))
        self.assertTrue(self.pointer.show_pointer)
        self.assertEqual(self.pointer.pointer_pos, (.5, .5))

    def test_right_click_and_other_previews_do_not_start_laser(self):
        self.assertFalse(self.press(button=3))
        self.assertFalse(self.press(button=3, ctrl=True))
        self.assertFalse(self.press(Widget(), ctrl=True))
        self.assertFalse(self.pointer.show_pointer)

    def test_disabled_and_continuous_modes_do_not_start_manual_hold(self):
        for mode in (self.target.Mode.DISABLED, self.target.Mode.CONTINUOUS):
            with self.subTest(mode=mode):
                self.pointer.pointer_mode = mode
                self.assertFalse(self.press())
                self.assertFalse(self.pointer.show_pointer)

    def test_continuous_enter_places_laser_immediately_and_leave_hides(self):
        self.pointer.pointer_mode = self.target.Mode.CONTINUOUS
        self.assertTrue(self.pointer.track_enter_leave(self.pointer.p_da_cur,
                                                       event(EventType.ENTER_NOTIFY, x=320, y=90)))
        self.assertTrue(self.pointer.show_pointer)
        self.assertEqual(self.pointer.pointer_pos, (.5, .25))
        self.assertTrue(self.pointer.track_enter_leave(self.pointer.p_da_cur, event(EventType.LEAVE_NOTIFY)))
        self.assertFalse(self.pointer.show_pointer)

    def test_l_toggle_switches_continuous_and_manual_even_after_disable(self):
        action = types.SimpleNamespace(change_state=mock.Mock())
        for initial in (self.target.Mode.MANUAL, self.target.Mode.DISABLED):
            with self.subTest(initial=initial):
                self.pointer.pointer_mode = initial
                self.pointer.change_pointermode(action, TextVariant('toggle'))
                self.assertEqual(self.pointer.pointer_mode, self.target.Mode.CONTINUOUS)
                self.pointer.change_pointermode(action, TextVariant('toggle'))
                self.assertEqual(self.pointer.pointer_mode, self.target.Mode.MANUAL)
                self.assertEqual(action.change_state.call_args.args[0].get_string(), 'manual')

    def test_changing_mode_cancels_active_manual_hold(self):
        self.press()
        self.pointer.activate_pointermode(self.target.Mode.DISABLED)
        self.assertFalse(self.pointer.show_pointer)
        self.assertFalse(self.pointer.cancel_manual_pointer())

    def test_zero_sized_widgets_cannot_start_hold_or_divide_by_zero(self):
        self.pointer.p_da_cur.width = 0
        self.assertFalse(self.press())
        self.assertFalse(self.pointer.show_pointer)

    def test_ui_gives_zoom_and_scribble_press_priority(self):
        for active in ('zoom', 'scribbler'):
            with self.subTest(active=active):
                ui = self.ui()
                handler = ui.zoom.toggle_zoom_target if active == 'zoom' else ui.scribbler.toggle_scribble
                handler.return_value = True
                self.assertTrue(ui.track_clicks(self.pointer.p_da_cur, event(EventType.BUTTON_PRESS)))
                self.assertFalse(self.pointer.show_pointer)

    def test_ui_release_finishes_laser_even_if_zoom_became_active(self):
        ui = self.ui()
        self.assertTrue(ui.track_clicks(self.pointer.p_da_cur, event(EventType.BUTTON_PRESS)))
        ui.zoom.toggle_zoom_target.return_value = True
        self.assertTrue(ui.track_clicks(self.pointer.p_da_cur, event(EventType.BUTTON_RELEASE)))
        self.assertFalse(self.pointer.show_pointer)


def setUpModule():
    PointerInteractionTests.target = load_callbacks(SOURCE)


if __name__ == '__main__':
    unittest.main(verbosity=2)
