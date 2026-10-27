"""Exercise shared file-list hosting and preferences at the GTK widget boundary."""
try:
    from . import _support
except ImportError:
    import _support

import ast
import configparser
from types import SimpleNamespace
import unittest
from unittest import mock


class Widget:
    def __init__(self, width=1200):
        self.parent = None
        self.children = []
        self.visible = True
        self.width = width
        self.packing = {}
        self.focus = None

    def get_parent(self):
        return self.parent

    def add(self, child):
        if child.parent is not None:
            raise AssertionError('Remove a widget from its existing parent first.')
        self.children.append(child)
        child.parent = self

    def remove(self, child):
        self.children.remove(child)
        child.parent = None

    def get_children(self):
        return list(self.children)

    def pack_start(self, child, expand, fill, padding):
        self.add(child)
        self.packing[child] = (expand, fill, padding, 'start')

    def query_child_packing(self, child):
        return self.packing[child]

    def set_child_packing(self, child, *packing):
        self.packing[child] = packing

    def reorder_child(self, child, position):
        self.children.remove(child)
        self.children.insert(position, child)

    def set_no_show_all(self, value):
        self.no_show_all = value

    def hide(self):
        self.visible = False

    def show(self):
        self.visible = True

    def get_visible(self):
        return self.visible

    def get_allocated_width(self):
        return self.width

    def get_focus(self):
        return self.focus

    def set_focus(self, focus):
        self.focus = focus

    def is_ancestor(self, ancestor):
        parent = self.parent
        while parent is not None:
            if parent is ancestor:
                return True
            parent = parent.parent
        return False

    def grab_focus(self):
        host = self
        while host.parent is not None:
            host = host.parent
        host.focus = self


class Window(Widget):
    def present(self):
        self.visible = True
        pending = list(self.children)
        while pending:
            child = pending.pop()
            if child.get_visible():
                pending.extend(child.children)
                if isinstance(child, Paned):
                    child.signals['size-allocate'](child, None)


class View(Window):
    def __init__(self):
        super().__init__()
        self.visible = False
        self.panel, self.tree = Widget(width=360), Widget()
        self.add(self.panel)
        self.panel.add(self.tree)
        self.tree.selection = ['entry-b', 'entry-c']
        self.refresh = mock.Mock()
        self.set_compact = mock.Mock()
        self.update_dock_controls = mock.Mock()
        self._on_key_press = mock.Mock(return_value=True)
        self._on_key_release = mock.Mock(return_value=False)

    def present(self):
        raise AssertionError('Controller must bypass the forwarding view override.')

    def hide(self):
        raise AssertionError('Controller must bypass the forwarding view override.')


class Paned(Widget):
    @classmethod
    def new(cls, orientation):
        return cls()

    def __init__(self):
        super().__init__()
        self.signals = {}
        self.position = 0
        self.child1 = self.child2 = None

    def set_wide_handle(self, value):
        self.wide_handle = value

    def connect(self, signal, callback):
        self.signals[signal] = callback

    def pack1(self, child, resize, shrink):
        self.add(child)
        self.child1, self.pack1_options = child, (resize, shrink)

    def pack2(self, child, resize, shrink):
        self.add(child)
        self.child2, self.pack2_options = child, (resize, shrink)

    def remove(self, child):
        super().remove(child)
        if self.child1 is child:
            self.child1 = None
        if self.child2 is child:
            self.child2 = None

    def get_position(self):
        return self.position

    def set_position(self, position):
        self.position = position
        self.signals['notify::position'](self, None)


GTK = SimpleNamespace(Window=Window, Paned=Paned, Orientation=SimpleNamespace(HORIZONTAL=0))
GDK = SimpleNamespace(EventType=SimpleNamespace(KEY_PRESS=1, KEY_RELEASE=2))
source = _support.SOURCE / 'pympress/filelist_dock.py'
classes = [node for node in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(node, ast.ClassDef)]
namespace = dict(Gtk=GTK, Gdk=GDK)
exec(compile(ast.Module(body=classes, type_ignores=[]), str(source), 'exec'), namespace)
FileListDock = namespace['FileListDock']


class FileListDockTests(unittest.TestCase):
    def make_dock(self, **settings):
        self.config = configparser.ConfigParser()
        self.config['presenter'] = {key: str(value) for key, value in settings.items()}
        self.window, self.body = Window(), Widget()
        self.window.add(self.body)
        self.toolbar, self.central, self.bottom = Widget(), Widget(), Widget()
        for child, packing in ((self.toolbar, (False, True, 0)),
                               (self.central, (True, False, 7)),
                               (self.bottom, (False, True, 0))):
            self.body.pack_start(child, *packing)
        self.body.set_child_packing(self.central, True, False, 7, 'end')
        self.ui = SimpleNamespace(p_win=self.window, p_central=self.central, config=self.config)
        self.view = View()
        self.dock = FileListDock(self.view, self.ui)
        return self.dock

    def test_changing_hosts_does_not_rebuild_the_model_or_reset_scroll(self):
        dock = self.make_dock()
        dock.present()
        self.view.refresh.reset_mock()
        self.view.tree.scroll_position = 120
        for mode in (True, False, True, False):
            dock.set_docked(mode)
        self.view.refresh.assert_not_called()
        self.assertEqual(self.view.tree.scroll_position, 120)
        self.assertEqual(self.view.tree.selection, ['entry-b', 'entry-c'])

    def test_floating_default_is_hidden_and_does_not_rearrange_presenter(self):
        dock = self.make_dock()
        self.assertFalse(dock.docked)
        self.assertFalse(dock.visible)
        self.assertIsNone(dock.pane)
        self.assertIs(self.view.panel.get_parent(), self.view)
        self.assertEqual(self.body.children, [self.toolbar, self.central, self.bottom])
        self.assertTrue(self.view.panel.no_show_all)
        self.view.update_dock_controls.assert_called_once_with(False)

    def test_saved_dock_mode_restores_hidden_at_same_index_with_original_packing(self):
        dock = self.make_dock(file_list_docked='on')
        self.assertTrue(dock.docked)
        self.assertFalse(dock.visible)
        self.assertFalse(self.view.get_visible())
        self.assertEqual(self.body.children, [self.toolbar, dock.pane, self.bottom])
        self.assertEqual(self.body.query_child_packing(dock.pane), (True, False, 7, 'end'))
        self.assertIs(dock.pane.child2, self.central)
        self.assertIs(dock.pane.child1, self.view.panel)
        self.assertEqual(dock.pane.pack2_options, (True, False))
        self.view.set_compact.assert_called_once_with(True)
        self.view.update_dock_controls.assert_called_once_with(True)

    def test_dock_and_undock_keep_the_same_view_selection_and_visibility(self):
        dock = self.make_dock()
        tree = self.view.tree
        dock.present()
        self.assertTrue(self.view.get_visible())
        dock.set_docked(True)
        pane = dock.pane
        self.assertTrue(dock.visible)
        self.assertFalse(self.view.get_visible())
        self.assertIs(self.window.get_focus(), tree)
        self.assertTrue(self.config.getboolean('presenter', 'file_list_docked'))
        dock.set_docked(False)
        self.assertTrue(dock.visible)
        self.assertTrue(self.view.get_visible())
        self.assertIs(self.view.panel.get_parent(), self.view)
        self.assertIsNone(self.window.get_focus())
        self.assertIs(self.view.get_focus(), tree)
        self.assertEqual(tree.selection, ['entry-b', 'entry-c'])
        self.assertFalse(self.config.getboolean('presenter', 'file_list_docked'))
        dock.set_docked(True)
        self.assertIs(dock.pane, pane)
        self.assertIs(self.view.tree, tree)

    def test_switching_hidden_hosts_does_not_open_either_window(self):
        dock = self.make_dock()
        for mode in (True, True, False, False, True):
            dock.set_docked(mode)
            self.assertFalse(dock.visible)
            self.assertFalse(self.view.get_visible())
        self.assertEqual(self.view.refresh.call_count, 0)

    def test_toggle_hides_current_host_and_docked_hide_releases_focus(self):
        for mode in (False, True):
            with self.subTest(docked=mode):
                dock = self.make_dock(file_list_docked='on' if mode else 'off')
                dock.toggle()
                self.assertTrue(dock.visible)
                self.assertTrue(dock.has_focus())
                self.assertTrue(self.config.getboolean('presenter', 'file_list_visible'))
                dock.toggle()
                self.assertFalse(dock.visible)
                self.assertFalse(self.view.get_visible())
                self.assertFalse(dock.has_focus())
                self.assertFalse(self.config.getboolean('presenter', 'file_list_visible'))
                if mode:
                    self.assertIsNone(self.window.get_focus())

    def test_hide_does_not_clear_focus_outside_panel(self):
        dock = self.make_dock(file_list_docked='on')
        dock.present()
        self.window.set_focus(self.toolbar)
        self.assertFalse(dock.has_focus())
        dock.hide()
        self.assertIs(self.window.get_focus(), self.toolbar)

    def test_cleanup_preserves_all_preferences_in_both_modes(self):
        for mode in ('on', 'off'):
            with self.subTest(mode=mode):
                dock = self.make_dock(file_list_docked=mode, file_list_dock_width=420)
                dock.present()
                before = dict(self.config.items('presenter'))
                dock.cleanup()
                self.assertFalse(dock.visible)
                self.assertFalse(self.view.get_visible())
                self.assertEqual(dict(self.config.items('presenter')), before)

    def test_key_phases_use_actual_host_once_and_preserve_handler_result(self):
        for mode in ('on', 'off'):
            with self.subTest(mode=mode):
                dock = self.make_dock(file_list_docked=mode)
                dock.present()
                host = self.window if dock.docked else self.view
                press = SimpleNamespace(type=GDK.EventType.KEY_PRESS)
                release = SimpleNamespace(type=GDK.EventType.KEY_RELEASE)
                self.assertTrue(dock.handle_key(press))
                self.assertFalse(dock.handle_key(release))
                self.view._on_key_press.assert_called_once_with(host, press)
                self.view._on_key_release.assert_called_once_with(host, release)
                self.assertFalse(dock.handle_key(SimpleNamespace(type=99)))

    def test_hidden_or_unfocused_dock_does_not_consume_presenter_keys(self):
        dock = self.make_dock(file_list_docked='on')
        event = SimpleNamespace(type=GDK.EventType.KEY_PRESS)
        self.assertFalse(dock.handle_key(event))
        dock.present()
        self.window.set_focus(self.central)
        self.assertFalse(dock.handle_key(event))
        self.view._on_key_press.assert_not_called()
        self.view._on_key_release.assert_not_called()

    def test_invalid_saved_preferences_fall_back_without_opening(self):
        dock = self.make_dock(file_list_docked='unknown', file_list_dock_width='broken')
        self.assertFalse(dock.docked)
        self.assertEqual(dock._width, 360)
        self.assertFalse(dock.visible)

    def test_saved_width_is_bounded_and_restored_without_overwriting_preference(self):
        for saved, expected in ((-100, 280), (0, 280), (380, 380), (99999, 800)):
            with self.subTest(saved=saved):
                dock = self.make_dock(file_list_docked='on', file_list_dock_width=saved)
                dock.pane.width = 2000
                dock.present()
                self.assertEqual(dock.pane.get_position(), expected)
                self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), saved)

    def test_visible_allocated_user_move_persists_bounded_width(self):
        dock = self.make_dock(file_list_docked='on')
        dock.present()
        dock.pane.set_position(430)
        self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), 430)
        dock.pane.set_position(9999)
        self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), 540)
        self.assertEqual(dock.pane.get_position(), 540)
        dock.pane.set_position(0)
        self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), 280)
        self.assertEqual(dock.pane.get_position(), 280)

    def test_hidden_or_unallocated_position_notifications_never_save(self):
        dock = self.make_dock(file_list_docked='on', file_list_dock_width=400)
        dock.pane.set_position(0)
        self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), 400)
        dock.present()
        self.view.panel.width = 1
        dock.pane.set_position(200)
        self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), 400)
        self.view.panel.width, dock.pane.width = 360, 1
        dock.pane.set_position(0)
        self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), 400)

    def test_resize_caps_current_width_without_erasing_wider_preference(self):
        dock = self.make_dock(file_list_docked='on', file_list_dock_width=500)
        dock.present()
        for available, expected in ((600, 270), (1200, 500), (400, 180)):
            with self.subTest(available=available):
                dock.pane.width = available
                dock._size_allocated(dock.pane, None)
                self.assertEqual(dock.pane.get_position(), expected)
                self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), 500)

    def test_native_mapping_and_resize_notifications_do_not_erase_preferred_width(self):
        dock = self.make_dock(file_list_docked='on', file_list_dock_width=500)
        # A native map can notify a temporary zero divider before its first allocation.
        self.view.panel.show()
        dock.pane.set_position(0)
        self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), 500)
        dock._size_allocated(dock.pane, None)
        self.assertEqual(dock.pane.get_position(), 500)
        # GTK may also notify a clamped divider while resizing, before size-allocate.
        dock.pane.width = 600
        dock.pane.set_position(250)
        self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), 500)
        dock._size_allocated(dock.pane, None)
        self.assertEqual(dock.pane.get_position(), 270)
        self.assertEqual(self.config.getint('presenter', 'file_list_dock_width'), 500)


if __name__ == '__main__':
    unittest.main()
