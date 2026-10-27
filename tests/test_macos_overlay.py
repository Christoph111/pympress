"""Exercise native overlay ownership, ABI widths, and reversible configuration.

Native GTK/AppKit calls are replaced at their boundary. These tests do not
establish placement over a fullscreen video or behavior across macOS Spaces.
"""

import ctypes
import sys
import threading
import types
import unittest
from unittest import mock

try:
    from . import _support
except ImportError:
    import _support

from pympress import macos_overlay

SOURCE = _support.SOURCE


class NativeBridge:
    def __init__(self, behavior=0, level=1):
        self.saved_behavior = self.current_behavior = behavior
        self.saved_level = self.current_level = level
        self.calls = []
        self.ns_window = mock.Mock(return_value=200)
        self.validate = mock.Mock()
        self.fail_level_once = False
        self.retain_properties = True

    def behavior(self, pointer):
        return self.current_behavior

    def level(self, pointer):
        return self.current_level

    def set_behavior(self, pointer, value):
        self.calls.append(('behavior', pointer, value))
        if self.retain_properties:
            self.current_behavior = value

    def set_level(self, pointer, value):
        self.calls.append(('level', pointer, value))
        if self.fail_level_once:
            self.fail_level_once = False
            raise OSError('native setter failed')
        self.current_level = value


class OverlayProperties(unittest.TestCase):
    def setUp(self):
        self.window = types.SimpleNamespace(is_destroyed=mock.Mock(return_value=False))
        self.bridge = NativeBridge()
        self.platform = mock.patch.object(macos_overlay.sys, 'platform', 'darwin')
        self.pointer = mock.patch.object(macos_overlay, '_window_pointer', return_value=100)
        self.bridge_patch = mock.patch.object(macos_overlay, '_get_bridge', return_value=self.bridge)
        for patcher in (self.platform, self.pointer, self.bridge_patch):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_fullscreen_conflicts_are_removed_and_unrelated_flags_preserved(self):
        flags = ((1 << 1) | (1 << 7) | (1 << 9) | (1 << 11) | (1 << 16))
        self.bridge.saved_behavior = self.bridge.current_behavior = flags
        restore = macos_overlay.prepare_overlay(self.window)
        self.assertTrue(callable(restore))
        self.assertEqual(self.bridge.current_behavior, (1 << 0) | (1 << 8) | (1 << 11) | (1 << 16))
        self.assertEqual(self.bridge.current_level, 3)
        self.bridge.validate.assert_called_once_with(200)
        self.bridge.ns_window.assert_called_once_with(100)

    def test_exact_original_properties_are_restored_once(self):
        self.bridge.current_behavior = 0x100080
        self.bridge.current_level = -7
        restore = macos_overlay.prepare_overlay(self.window)
        restore()
        self.assertEqual(self.bridge.current_behavior, 0x100080)
        self.assertEqual(self.bridge.current_level, -7)
        calls = list(self.bridge.calls)
        restore()
        self.assertEqual(self.bridge.calls, calls)

    def test_partial_native_failure_rolls_back_both_properties(self):
        self.bridge.current_behavior = 128
        self.bridge.current_level = 1
        self.bridge.fail_level_once = True
        with self.assertLogs(macos_overlay.logger.name, level='WARNING'):
            self.assertIsNone(macos_overlay.prepare_overlay(self.window))
        self.assertEqual(self.bridge.current_behavior, 128)
        self.assertEqual(self.bridge.current_level, 1)

    def test_ignored_properties_return_no_restore_callback(self):
        self.bridge.current_behavior = 128
        self.bridge.retain_properties = False
        with self.assertLogs(macos_overlay.logger.name, level='WARNING'):
            self.assertIsNone(macos_overlay.prepare_overlay(self.window))
        self.assertEqual(self.bridge.current_behavior, 128)
        self.assertEqual(self.bridge.current_level, self.bridge.saved_level)

    def test_absent_bridge_leaves_window_unchanged(self):
        with mock.patch.object(macos_overlay, '_get_bridge', side_effect=AttributeError('missing symbol')):
            with self.assertLogs(macos_overlay.logger.name, level='WARNING'):
                self.assertIsNone(macos_overlay.prepare_overlay(self.window))
        self.assertEqual(self.bridge.calls, [])

    def test_invalid_window_never_reaches_native_bridge(self):
        with mock.patch.object(macos_overlay, '_window_pointer', side_effect=ValueError('invalid capsule')):
            with self.assertLogs(macos_overlay.logger.name, level='WARNING'):
                self.assertIsNone(macos_overlay.prepare_overlay(self.window))
        self.bridge.ns_window.assert_not_called()
        self.assertEqual(self.bridge.calls, [])

    def test_non_macos_is_noop_without_loading_a_bridge(self):
        with mock.patch.object(macos_overlay.sys, 'platform', 'linux'):
            self.assertIsNone(macos_overlay.prepare_overlay(object()))
        macos_overlay._get_bridge.assert_not_called()
        macos_overlay._window_pointer.assert_not_called()

    def test_background_thread_is_rejected_before_native_pointer_access(self):
        result = []
        with self.assertLogs(macos_overlay.logger.name, level='WARNING'):
            worker = threading.Thread(target=lambda: result.append(macos_overlay.prepare_overlay(self.window)))
            worker.start()
            worker.join()
        self.assertEqual(result, [None])
        macos_overlay._get_bridge.assert_not_called()
        macos_overlay._window_pointer.assert_not_called()

    def test_destroyed_output_is_not_messaged_by_restore(self):
        restore = macos_overlay.prepare_overlay(self.window)
        self.window.is_destroyed.return_value = True
        calls = list(self.bridge.calls)
        restore()
        self.assertEqual(self.bridge.calls, calls)

    def test_replaced_native_window_is_not_messaged_by_restore(self):
        restore = macos_overlay.prepare_overlay(self.window)
        self.bridge.ns_window.return_value = 201
        calls = list(self.bridge.calls)
        with self.assertLogs(macos_overlay.logger.name, level='WARNING'):
            restore()
        self.assertEqual(self.bridge.calls, calls)

    def test_failed_restore_can_be_retried_on_main_thread(self):
        restore = macos_overlay.prepare_overlay(self.window)
        with mock.patch.object(macos_overlay, '_main_thread', side_effect=RuntimeError('wrong thread')):
            with self.assertLogs(macos_overlay.logger.name, level='WARNING'):
                restore()
        restore()
        self.assertEqual(self.bridge.current_behavior, self.bridge.saved_behavior)
        self.assertEqual(self.bridge.current_level, self.bridge.saved_level)


class IndependentNativeWindows(unittest.TestCase):
    def setUp(self):
        self.window = mock.sentinel.owned_window
        self.bridge = NativeBridge()
        self.bridge.responds = mock.Mock(return_value=True)
        self.bridge.disable_tabbing = mock.Mock(return_value=True)
        for patcher in (mock.patch.object(macos_overlay.sys, 'platform', 'darwin'),
                        mock.patch.object(macos_overlay, '_window_pointer', return_value=100),
                        mock.patch.object(macos_overlay, '_get_bridge', return_value=self.bridge)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_owned_window_is_validated_and_only_its_tabbing_mode_changes(self):
        self.assertTrue(macos_overlay.disable_tabbing(self.window))
        macos_overlay._window_pointer.assert_called_once_with(self.window)
        self.bridge.ns_window.assert_called_once_with(100)
        self.bridge.validate.assert_called_once_with(200)
        self.bridge.disable_tabbing.assert_called_once_with(200)
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.bridge.responds.call_args_list,
                         [mock.call(200, b'setTabbingMode:'), mock.call(200, b'tabbingMode')])

    def test_older_native_api_without_tabbing_selector_is_unchanged(self):
        self.bridge.responds.return_value = False
        self.assertFalse(macos_overlay.disable_tabbing(self.window))
        self.bridge.disable_tabbing.assert_not_called()
        self.assertEqual(self.bridge.calls, [])

    def test_missing_native_getter_is_also_unsupported(self):
        self.bridge.responds.side_effect = [True, False]
        self.assertFalse(macos_overlay.disable_tabbing(self.window))
        self.bridge.disable_tabbing.assert_not_called()

    def test_unconfirmed_native_tabbing_property_returns_false(self):
        self.bridge.disable_tabbing.return_value = False
        self.assertFalse(macos_overlay.disable_tabbing(self.window))

    def test_non_macos_does_not_load_or_validate_native_windows(self):
        with mock.patch.object(macos_overlay.sys, 'platform', 'win32'):
            self.assertFalse(macos_overlay.disable_tabbing(self.window))
        macos_overlay._get_bridge.assert_not_called()
        macos_overlay._window_pointer.assert_not_called()

    def test_invalid_window_is_rejected_before_bridge_access(self):
        with mock.patch.object(macos_overlay, '_window_pointer', side_effect=ValueError('not owned')):
            self.assertFalse(macos_overlay.disable_tabbing(123))
        macos_overlay._get_bridge.assert_not_called()
        self.bridge.disable_tabbing.assert_not_called()

    def test_native_capability_failure_returns_false(self):
        self.bridge.validate.side_effect = RuntimeError('unavailable NSWindow')
        self.assertFalse(macos_overlay.disable_tabbing(self.window))
        self.bridge.disable_tabbing.assert_not_called()

    def test_native_tabbing_only_dispatches_integer_property_to_instance(self):
        bridge = object.__new__(macos_overlay._Bridge)
        bridge.select = mock.Mock(side_effect=lambda value: value)
        bridge.set_signed = mock.Mock()
        bridge.get_signed = mock.Mock(return_value=2)
        self.assertTrue(bridge.disable_tabbing(200))
        bridge.set_signed.assert_called_once_with(200, b'setTabbingMode:', 2)
        bridge.get_signed.assert_called_once_with(200, b'tabbingMode')

    def test_background_thread_cannot_mutate_window_tabbing(self):
        result = []
        worker = threading.Thread(target=lambda: result.append(macos_overlay.disable_tabbing(self.window)))
        worker.start()
        worker.join()
        self.assertEqual(result, [False])
        macos_overlay._window_pointer.assert_not_called()
        self.bridge.disable_tabbing.assert_not_called()


class BridgeSignatures(unittest.TestCase):
    def test_each_native_signature_uses_pointer_sized_arguments(self):
        process = types.SimpleNamespace(sel_registerName=mock.Mock(), objc_getClass=mock.Mock())
        gdk = types.SimpleNamespace(gdk_quartz_window_get_nswindow=mock.Mock())
        senders = []

        def sender(_process, restype, *arguments):
            senders.append((restype, arguments))
            return mock.Mock()

        with mock.patch.object(macos_overlay.ctypes, 'CDLL', return_value=process), \
                mock.patch.object(macos_overlay, '_loaded_gdk', return_value=gdk), \
                mock.patch.object(macos_overlay, '_objc_sender', side_effect=sender):
            bridge = macos_overlay._Bridge()
        self.assertEqual(bridge.ns_window.argtypes, [ctypes.c_void_p])
        self.assertIs(bridge.ns_window.restype, ctypes.c_void_p)
        self.assertEqual(process.sel_registerName.argtypes, [ctypes.c_char_p])
        self.assertEqual(process.objc_getClass.argtypes, [ctypes.c_char_p])
        self.assertEqual(senders, [(ctypes.c_ulong, ()), (None, (ctypes.c_ulong,)),
                                   (ctypes.c_long, ()), (None, (ctypes.c_long,)),
                                   (ctypes.c_bool, (ctypes.c_void_p,))])

    def test_objc_sender_binds_receiver_and_selector_as_pointers(self):
        native = types.SimpleNamespace(objc_msgSend=object())
        prototype = mock.Mock(return_value=mock.sentinel.sender)
        with mock.patch.object(macos_overlay.ctypes, 'cast', return_value=types.SimpleNamespace(value=123)), \
                mock.patch.object(macos_overlay.ctypes, 'CFUNCTYPE', return_value=prototype) as factory:
            result = macos_overlay._objc_sender(native, ctypes.c_ulong, ctypes.c_void_p)
        factory.assert_called_once_with(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        prototype.assert_called_once_with(123)
        self.assertIs(result, mock.sentinel.sender)

    def test_successful_bridge_is_cached_but_failure_is_retryable(self):
        outcomes = [OSError('not loaded yet'), mock.sentinel.bridge]
        with mock.patch.object(macos_overlay, '_BRIDGE', None), \
                mock.patch.object(macos_overlay, '_Bridge', side_effect=outcomes) as bridge_type:
            with self.assertRaises(OSError):
                macos_overlay._get_bridge()
            self.assertIs(macos_overlay._get_bridge(), mock.sentinel.bridge)
            self.assertIs(macos_overlay._get_bridge(), mock.sentinel.bridge)
            self.assertEqual(bridge_type.call_count, 2)

    def test_global_gdk_symbol_avoids_any_library_loading(self):
        process = types.SimpleNamespace(gdk_quartz_window_get_nswindow=mock.Mock())
        with mock.patch.object(macos_overlay.ctypes, 'CDLL') as loader:
            self.assertIs(macos_overlay._loaded_gdk(process), process)
        loader.assert_not_called()

    def test_dyld_paths_only_open_existing_gdk_with_no_load(self):
        process = types.SimpleNamespace(_dyld_image_count=mock.Mock(return_value=4),
                                        _dyld_get_image_name=mock.Mock(side_effect=[
                                            b'libgdk-3.0.dylib', b'/tmp/other.dylib',
                                            b'/bundle/libgdk-3.0.dylib', b'/bundle/unused.dylib']))
        library = types.SimpleNamespace(gdk_quartz_window_get_nswindow=mock.Mock())
        with mock.patch.object(macos_overlay.os, 'RTLD_NOLOAD', 16, create=True), \
                mock.patch.object(macos_overlay.os, 'RTLD_LAZY', 1, create=True), \
                mock.patch.object(macos_overlay.ctypes, 'CDLL', return_value=library) as loader:
            self.assertIs(macos_overlay._loaded_gdk(process), library)
        loader.assert_called_once_with('/bundle/libgdk-3.0.dylib', mode=17)
        self.assertEqual(process._dyld_image_count.argtypes, [])
        self.assertEqual(process._dyld_get_image_name.argtypes, [ctypes.c_uint32])

    def test_no_loaded_matching_library_fails_without_opening_paths(self):
        process = types.SimpleNamespace(_dyld_image_count=mock.Mock(return_value=1),
                                        _dyld_get_image_name=mock.Mock(return_value=b'/tmp/foreign-libgdk.dylib'))
        with mock.patch.object(macos_overlay.os, 'RTLD_NOLOAD', 16, create=True), \
                mock.patch.object(macos_overlay.os, 'RTLD_LAZY', 1, create=True), \
                mock.patch.object(macos_overlay.ctypes, 'CDLL') as loader:
            with self.assertRaisesRegex(RuntimeError, 'No loaded GTK'):
                macos_overlay._loaded_gdk(process)
        loader.assert_not_called()


class WindowOwnership(unittest.TestCase):
    def setUp(self):
        class Window:
            __gtype__ = types.SimpleNamespace(name='GdkQuartzWindow')

            def is_destroyed(self):
                return False

            def get_display(self):
                return types.SimpleNamespace(__gtype__=types.SimpleNamespace(name='GdkQuartzDisplay'))

            def get_window_type(self):
                return 'toplevel'

        self.Window = Window
        self.gdk = types.SimpleNamespace(Window=Window, WindowType=types.SimpleNamespace(TOPLEVEL='toplevel'))
        gi = types.ModuleType('gi')
        repository = types.ModuleType('gi.repository')
        repository.Gdk = self.gdk
        self.patch = mock.patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_unnamed_gobject_capsule_preserves_full_pointer_width(self):
        capsule_new = ctypes.pythonapi.PyCapsule_New
        capsule_new.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
        capsule_new.restype = ctypes.py_object
        window = self.Window()
        value = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8 - 2)) + 123
        window.__gpointer__ = capsule_new(value, None, None)
        self.assertEqual(macos_overlay._window_pointer(window), value)
        self.assertEqual(ctypes.pythonapi.PyCapsule_GetPointer.argtypes, [ctypes.py_object, ctypes.c_char_p])

    def test_plain_native_identifiers_are_rejected(self):
        for candidate in (123, None, object()):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                macos_overlay._window_pointer(candidate)

    def test_non_quartz_backend_is_rejected_before_capsule_access(self):
        window = self.Window()
        window.__gtype__ = types.SimpleNamespace(name='GdkX11Window')
        with self.assertRaisesRegex(ValueError, 'Quartz'):
            macos_overlay._window_pointer(window)

    def test_child_window_is_rejected(self):
        window = self.Window()
        window.get_window_type = lambda: 'child'
        with self.assertRaisesRegex(ValueError, 'toplevel'):
            macos_overlay._window_pointer(window)

    def test_missing_or_invalid_capsule_never_reaches_a_native_getter(self):
        window = self.Window()
        window.__gpointer__ = object()
        with self.assertRaisesRegex(ValueError, 'capsule'):
            macos_overlay._window_pointer(window)


if __name__ == '__main__':
    unittest.main()
