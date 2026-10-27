"""Optional, reversible macOS Space behavior for Pympress's own output window.

Only already-loaded GTK and Objective-C symbols are used. No window identifier
from another application is accepted, and unsupported bridges leave the window
unchanged. See GDK's Quartz gdk_quartz_window_get_nswindow() and Apple's public
NSWindow collectionBehavior/level APIs; no private AppKit API is required.
"""

import ctypes
import logging
import os
import re
import sys
import threading


logger = logging.getLogger(__name__)

# NSWindow.h and CGWindowLevel.h, public AppKit/CoreGraphics SDK constants.
_JOIN_ALL_SPACES = 1 << 0
_MOVE_TO_ACTIVE_SPACE = 1 << 1
_FULLSCREEN_PRIMARY = 1 << 7
_FULLSCREEN_AUXILIARY = 1 << 8
_FULLSCREEN_NONE = 1 << 9
_FLOATING_WINDOW_LEVEL = 3
_TABBING_DISALLOWED = 2
_CONFLICTING_BEHAVIOR = _MOVE_TO_ACTIVE_SPACE | _FULLSCREEN_PRIMARY | _FULLSCREEN_NONE
_GDK_LIBRARY = re.compile(r'^libgdk-3(?:\.\d+)*\.dylib$')
_BRIDGE = None


def _set_signature(function, restype, arguments):
    """Bind each C entry point with its exact return and argument widths."""
    function.restype = restype
    function.argtypes = list(arguments)
    return function


def _loaded_gdk(process):
    """Find GTK's existing Quartz library without loading a new shared object."""
    if hasattr(process, 'gdk_quartz_window_get_nswindow'):
        return process
    # RTLD_NOLOAD prevents a same-named file from being loaded from disk. The
    # paths below are obtained from dyld's current process image inventory only.
    no_load = getattr(os, 'RTLD_NOLOAD', None)
    lazy = getattr(os, 'RTLD_LAZY', None)
    if no_load is None or lazy is None:
        raise RuntimeError('This runtime cannot query an already-loaded GTK library.')
    count = _set_signature(process._dyld_image_count, ctypes.c_uint32, ())
    name = _set_signature(process._dyld_get_image_name, ctypes.c_char_p, (ctypes.c_uint32,))
    for index in range(min(count(), 4096)):
        encoded = name(index)
        if not encoded:
            continue
        path = os.fsdecode(encoded)
        if os.path.isabs(path) and _GDK_LIBRARY.match(os.path.basename(path)):
            library = ctypes.CDLL(path, mode=no_load | lazy)
            if hasattr(library, 'gdk_quartz_window_get_nswindow'):
                return library
    raise RuntimeError('No loaded GTK Quartz window bridge is available.')


def _objc_sender(process, restype, *arguments):
    """Create a separately typed objc_msgSend callable for one ABI signature."""
    address = ctypes.cast(process.objc_msgSend, ctypes.c_void_p).value
    if not address:
        raise RuntimeError('The Objective-C message bridge is unavailable.')
    return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *arguments)(address)


class _Bridge:
    """Keep typed, process-local GTK and Objective-C entry points alive."""

    def __init__(self):
        self.process = ctypes.CDLL(None)
        self.gdk = _loaded_gdk(self.process)
        self.ns_window = _set_signature(self.gdk.gdk_quartz_window_get_nswindow,
                                        ctypes.c_void_p, (ctypes.c_void_p,))
        self.selector = _set_signature(self.process.sel_registerName, ctypes.c_void_p, (ctypes.c_char_p,))
        self.get_class = _set_signature(self.process.objc_getClass, ctypes.c_void_p, (ctypes.c_char_p,))
        self.get_unsigned = _objc_sender(self.process, ctypes.c_ulong)
        self.set_unsigned = _objc_sender(self.process, None, ctypes.c_ulong)
        self.get_signed = _objc_sender(self.process, ctypes.c_long)
        self.set_signed = _objc_sender(self.process, None, ctypes.c_long)
        self.get_bool_argument = _objc_sender(self.process, ctypes.c_bool, ctypes.c_void_p)
        self._selectors = {}

    def select(self, name):
        """Resolve only fixed API selectors supplied by this module."""
        if name not in self._selectors:
            self._selectors[name] = self.selector(name)
        return self._selectors[name]

    def validate(self, pointer):
        """Require an actual NSWindow implementing each public property used."""
        window_class = self.get_class(b'NSWindow')
        if not pointer or not window_class or not self.get_bool_argument(
                pointer, self.select(b'isKindOfClass:'), window_class):
            raise RuntimeError('GTK did not provide a native NSWindow.')
        for selector in (b'collectionBehavior', b'setCollectionBehavior:', b'level', b'setLevel:'):
            if not self.responds(pointer, selector):
                raise RuntimeError('The native window lacks the required overlay properties.')

    def responds(self, pointer, selector):
        """Check an optional public selector before dispatching a native call."""
        return self.get_bool_argument(pointer, self.select(b'respondsToSelector:'), self.select(selector))

    def behavior(self, pointer):
        """Read the native NSUInteger collection behavior without truncation."""
        return self.get_unsigned(pointer, self.select(b'collectionBehavior'))

    def level(self, pointer):
        """Read the signed native NSInteger window level."""
        return self.get_signed(pointer, self.select(b'level'))

    def set_behavior(self, pointer, value):
        """Set the native window's public collectionBehavior property."""
        self.set_unsigned(pointer, self.select(b'setCollectionBehavior:'), value)

    def set_level(self, pointer, value):
        """Set the native window's public level property."""
        self.set_signed(pointer, self.select(b'setLevel:'), value)

    def disable_tabbing(self, pointer):
        """Disallow automatic tabs on this window without changing global policy."""
        self.set_signed(pointer, self.select(b'setTabbingMode:'), _TABBING_DISALLOWED)
        return self.get_signed(pointer, self.select(b'tabbingMode')) == _TABBING_DISALLOWED


def _get_bridge():
    """Cache successful bindings while retaining their already-loaded library."""
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = _Bridge()
    return _BRIDGE


def _window_pointer(window):
    """Extract a live, toplevel GdkQuartzWindow's validated unnamed capsule."""
    from gi.repository import Gdk

    if not isinstance(window, Gdk.Window) or window.is_destroyed():
        raise ValueError('An existing GTK output window is required.')
    if window.__gtype__.name != 'GdkQuartzWindow' or window.get_display().__gtype__.name != 'GdkQuartzDisplay':
        raise ValueError('The output window does not use GTK Quartz.')
    if window.get_window_type() != Gdk.WindowType.TOPLEVEL:
        raise ValueError('Only an application-owned toplevel window is supported.')
    capsule = window.__gpointer__
    valid = _set_signature(ctypes.pythonapi.PyCapsule_IsValid, ctypes.c_int,
                           (ctypes.py_object, ctypes.c_char_p))
    pointer = _set_signature(ctypes.pythonapi.PyCapsule_GetPointer, ctypes.c_void_p,
                             (ctypes.py_object, ctypes.c_char_p))
    if not valid(capsule, None):
        raise ValueError('GTK did not provide a valid native window capsule.')
    return pointer(capsule, None)


def _main_thread():
    """Keep all AppKit mutations on the Python/GTK application main thread."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('Native overlay properties require the application main thread.')


def disable_tabbing(gdk_window):
    """Keep one owned Quartz window independent of macOS automatic window tabs.

    Call after GTK realizes the window and before its first show. This changes
    only that NSWindow's public tabbingMode property, available since macOS
    10.12; it does not change the user's global tabbing preference.

    Args:
        gdk_window (:class:`~Gdk.Window`): The realized application-owned window.

    Returns:
        bool: Whether the native window confirmed that automatic tabs are
        disallowed. Unsupported platforms or older native APIs return False.
    """
    if sys.platform != 'darwin':
        return False
    try:
        _main_thread()
        pointer = _window_pointer(gdk_window)
        bridge = _get_bridge()
        native = bridge.ns_window(pointer)
        bridge.validate(native)
        if not all(bridge.responds(native, selector) for selector in (b'setTabbingMode:', b'tabbingMode')):
            return False
        return bridge.disable_tabbing(native)
    except Exception as error:
        logger.debug('Could not disable automatic tabs for this macOS window: %s', error)
        return False


def prepare_overlay(gdk_window):
    """Allow the owned Quartz output window to accompany other full-screen apps.

    Args:
        gdk_window (:class:`~Gdk.Window`): The realized, application-owned output
            window. This function does not show, move, resize, or focus it.

    Returns:
        callable or None: An idempotent restore callback, or ``None`` outside
        macOS or when the safe native bridge is unavailable. Restore before
        changing the GTK native window's style or destroying it.
    """
    if sys.platform != 'darwin':
        return None
    try:
        _main_thread()
        owned_pointer = _window_pointer(gdk_window)
        bridge = _get_bridge()
        native = bridge.ns_window(owned_pointer)
        bridge.validate(native)
        old_behavior, old_level = bridge.behavior(native), bridge.level(native)
        behavior = (old_behavior & ~_CONFLICTING_BEHAVIOR) | _JOIN_ALL_SPACES | _FULLSCREEN_AUXILIARY
        try:
            bridge.set_behavior(native, behavior)
            bridge.set_level(native, _FLOATING_WINDOW_LEVEL)
            if bridge.behavior(native) != behavior or bridge.level(native) != _FLOATING_WINDOW_LEVEL:
                raise RuntimeError('The native window did not retain the requested overlay properties.')
        except Exception:
            bridge.set_behavior(native, old_behavior)
            bridge.set_level(native, old_level)
            raise
    except Exception as error:
        logger.warning('Native macOS overlay support is unavailable: %s', error)
        return None

    restored = [False]

    def restore():
        """Restore the exact original properties once while this window is alive."""
        if restored[0]:
            return
        try:
            _main_thread()
            if gdk_window.is_destroyed():
                restored[0] = True
                return
            if _window_pointer(gdk_window) != owned_pointer or bridge.ns_window(owned_pointer) != native:
                raise RuntimeError('The owned GTK native window changed before restoration.')
            bridge.set_behavior(native, old_behavior)
            bridge.set_level(native, old_level)
            restored[0] = True
        except Exception as error:
            logger.warning('Could not restore native macOS overlay properties: %s', error)

    return restore


def prepare_independent_window(window):
    """Prevent automatic macOS tab grouping before showing an owned GTK window."""
    if sys.platform != 'darwin':
        return False
    window.realize()
    return disable_tabbing(window.get_window())
