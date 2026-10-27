#!/usr/bin/env python3
"""Reproduce GTK parent lifetimes while changing overview document lengths.

Drawing areas hold weak parent references; the grid owns its frames. Removing a
frame therefore loses the drawing area's parent unless the actual callback
keeps the frame alive. No GTK display is needed for these regression checks.
"""

try:
    from . import _support
except ImportError:
    import _support


import ast
import gc
import types
import unittest
from unittest import mock
import weakref

SOURCE = _support.SOURCE


class DrawingArea:
    def __init__(self):
        self.parent = lambda: None
        self.name = 'deck0'
        self.queue_draw = mock.Mock()

    def add_events(self, flags):
        pass

    def connect(self, *args):
        pass

    def get_parent(self):
        return self.parent()

    def set_name(self, name):
        self.name = name

    def get_name(self):
        return self.name


class Frame:
    def __init__(self):
        self.child = None
        self.set = mock.Mock()
        self.set_size_request = mock.Mock()

    def add(self, child):
        self.child = child
        child.parent = weakref.ref(self)

    def get_style_context(self):
        return types.SimpleNamespace(add_class=lambda name: None)

    def set_shadow_type(self, shadow):
        pass


class Grid:
    def __init__(self):
        self.positions = {}
        self.show_all = mock.Mock()

    def attach(self, frame, column, row, width, height):
        if frame is None:
            raise TypeError('A detached drawing area lost its GTK frame parent')
        self.positions[frame] = (column, row)

    def remove(self, frame):
        self.positions.pop(frame, None)

    def remove_row(self, row):
        for frame, (column, position) in list(self.positions.items()):
            if position == row:
                self.remove(frame)
            elif position > row:
                self.positions[frame] = column, position - 1

    def remove_column(self, column):
        for frame, (position, row) in list(self.positions.items()):
            if position == column:
                self.remove(frame)
            elif position > column:
                self.positions[frame] = position - 1, row

    def get_allocated_width(self):
        return 900

    def get_allocated_height(self):
        return 600

    def get_row_spacing(self):
        return 10

    def get_column_spacing(self):
        return 10


def load_methods(path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    overview = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Overview')
    names = {'setup_doc_callbacks', 'create_drawing_areas', 'reset_grid'}
    methods = [node for node in overview.body if isinstance(node, ast.FunctionDef) and node.name in names]
    selected = ast.Module(body=[ast.ClassDef(name='Overview', bases=[], keywords=[], body=methods,
                                             decorator_list=[])], type_ignores=[])
    idle_calls = []
    namespace = {
        'Gtk': types.SimpleNamespace(
            DrawingArea=DrawingArea,
            AspectFrame=Frame,
            ShadowType=types.SimpleNamespace(
                NONE=0)),
        'Gdk': types.SimpleNamespace(
            EventMask=types.SimpleNamespace(
                TOUCH_MASK=1,
                ENTER_NOTIFY_MASK=2,
                LEAVE_NOTIFY_MASK=4,
                BUTTON_PRESS_MASK=8,
                BUTTON_RELEASE_MASK=16)),
        'GLib': types.SimpleNamespace(
            idle_add=lambda *args: idle_calls.append(args)),
    }
    exec(compile(ast.fix_missing_locations(selected), str(path), 'exec'), namespace)
    return namespace['Overview'], idle_calls


class DeckTransitionTests(unittest.TestCase):
    overview_class = None
    idle_calls = None

    def setUp(self):
        self.idle_calls.clear()
        self.overview = self.overview_class()
        ui = self.overview
        ui.all_pages = True
        ui.max_row_size = 6
        ui.grid_size = 0, 0
        ui.deck_grid = Grid()
        ui.deck0 = DrawingArea()
        first_frame = Frame()
        first_frame.add(ui.deck0)
        ui.deck_grid.attach(first_frame, 0, 0, 1, 1)
        ui.deck_da_list = [ui.deck0]
        ui.c_da = types.SimpleNamespace(get_allocated_width=lambda: 1920, get_allocated_height=lambda: 1080)
        ui.deck_viewport = types.SimpleNamespace(get_window=lambda: types.SimpleNamespace(get_scale_factor=lambda: 2))
        ui.compute_frame_grid = lambda ratio, count: ((count + 2) // 3, min(3, count))
        ui.cache = types.SimpleNamespace(resize_widget=mock.Mock())
        for name in ('on_deck_draw', 'on_deck_click', 'on_deck_hover', 'prerender'):
            setattr(ui, name, mock.Mock())

    def document(self, count, labels=None):
        self.overview.setup_doc_callbacks(
            types.SimpleNamespace(
                pages_number=lambda: count,
                has_labels=lambda: labels is not None,
                get_last_label_pages=lambda: labels))

    def assert_active_frames(self, count):
        ui = self.overview
        self.assertEqual(len(ui.deck_da_list), max(1, count))
        frames = [area.get_parent() for area in ui.deck_da_list]
        self.assertTrue(all(frame is not None for frame in frames))
        self.assertEqual(set(ui.deck_grid.positions), set(frames))
        self.assertEqual(len(set(ui.deck_grid.positions.values())), len(frames))

    def test_pdf_two_pages_image_one_page_pdf_two_pages(self):
        for count in (2, 1, 2):
            self.document(count)
            self.assert_active_frames(count)
            self.assertEqual([area.get_name() for area in self.overview.deck_da_list],
                             ['deck{}'.format(page) for page in range(count)])

    def test_repeated_large_shrink_and_growth_retains_only_current_thumbnails(self):
        for count in (15, 3, 11, 1, 5, 2, 17):
            self.document(count)
            self.overview.reset_grid()
            self.assert_active_frames(count)

    def test_empty_documents_keep_stable_cache_provider(self):
        provider = self.overview.deck0
        for count in (0, 5, 0, 1, 0, 4):
            self.document(count)
            self.assert_active_frames(count)
            self.assertIs(self.overview.deck_da_list[0], provider)

    def test_distinct_label_pages_get_new_names_after_shrink_and_growth(self):
        self.overview.all_pages = False
        for labels in ([1, 4, 7, 9], [2], [0, 3, 8]):
            self.document(10, labels)
            self.assert_active_frames(len(labels))
            self.assertEqual([area.get_name() for area in self.overview.deck_da_list],
                             ['deck{}'.format(page) for page in labels])

    def test_shrinking_releases_retired_drawing_areas_and_frames(self):
        self.document(6)
        retired_areas = [weakref.ref(area) for area in self.overview.deck_da_list[1:]]
        retired_frames = [weakref.ref(area.get_parent()) for area in self.overview.deck_da_list[1:]]
        self.document(1)
        gc.collect()
        self.assertTrue(all(reference() is None for reference in retired_areas))
        self.assertTrue(all(reference() is None for reference in retired_frames))

    def test_reset_after_shrink_schedules_only_current_thumbnails(self):
        self.document(9)
        self.overview.reset_grid()
        self.idle_calls.clear()
        self.document(2)
        self.overview.reset_grid()
        self.assertEqual([args[1] for args in self.idle_calls], self.overview.deck_da_list)
        self.assert_active_frames(2)

    def test_cache_provider_survives_row_and_column_layout_changes(self):
        provider = self.overview.deck0
        self.overview.max_row_size = 2
        for count in (12, 2, 8, 1, 3):
            self.document(count)
            self.overview.reset_grid()
            self.assert_active_frames(count)
            self.assertIs(self.overview.deck_da_list[0], provider)
            self.assertIs(provider.get_parent().child, provider)


def setUpModule():
    path = SOURCE / 'pympress' / 'deck.py'
    DeckTransitionTests.overview_class, DeckTransitionTests.idle_calls = load_methods(path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
