"""Audience output modes and one interruptible native-window opacity fade."""
import math
import time


class OutputError(ValueError):
    """An unavailable display or unsupported output configuration."""


class OpacityFade:
    """Interpolate native window opacity without restarting interrupted fades."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = 0.0
        self.duration = 0.0
        self.source = self.target = 1.0

    @property
    def value(self):
        """Return the current bounded opacity using monotonic elapsed time."""
        progress = min(1.0, max(0.0, (self.clock() - self.started) / self.duration)) if self.duration else 1.0
        return self.source + (self.target - self.source) * progress

    @property
    def active(self):
        """Report whether the fade still has unfinished progress."""
        return self.duration > 0 and self.clock() < self.started + self.duration

    def start(self, source, target, seconds):
        """Begin one fade, preserving constant speed when reversing midway."""
        if any(not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in (source, target, seconds)) or not 0 <= seconds <= 5:
            raise ValueError('Invalid opacity fade.')
        if not 0 <= source <= 1 or not 0 <= target <= 1:
            raise ValueError('Opacity must be between zero and one.')
        self.source, self.target = float(source), float(target)
        self.duration = float(seconds) * abs(target - source)
        self.started = self.clock()


def _runtime():
    """Load native display bindings only for an actual output controller."""
    from gi.repository import Gdk, GLib
    return Gdk, GLib


class OutputController:
    """Manage borderless output independently of native fullscreen and slide fades."""

    def __init__(self, ui, clock=time.monotonic):
        self.ui, self.window = ui, ui.c_win
        self.Gdk, self.GLib = _runtime()
        self.display = self.window.get_display()
        self.fade = OpacityFade(clock)
        self._clock = clock
        self._source = None
        self._wait_source = None
        self._waiting = False
        self._wait_started = 0.0
        self._saved = None
        self._placement_needed = False
        self._restore_native = None
        self._selected_monitor = None
        self._visible = None
        self._error = ''
        self.current_opacity = 1.0
        self.target_opacity = 1.0
        saved_mode = ui.config.get('content', 'output_mode', fallback='legacy')
        self.explicit_mode = saved_mode in ('window', 'fullscreen', 'overlay')
        self.mode = saved_mode if self.explicit_mode else 'window'
        try:
            self.monitor_index = ui.config.getint('content', 'output_monitor', fallback=1)
            self.fade_seconds = ui.config.getfloat('content', 'output_fade', fallback=0.5)
        except ValueError:
            self.monitor_index, self.fade_seconds = 1, 0.5
        if not math.isfinite(self.fade_seconds) or not 0 <= self.fade_seconds <= 5:
            self.fade_seconds = 0.5
        if not 0 <= self.monitor_index < self.display.get_n_monitors():
            if self.explicit_mode:
                self._error = _('The saved output display is unavailable. Choose a display in Output settings.')
            else:
                self.monitor_index = max(0, self.display.get_n_monitors() - 1)
        self._removed_signal = self.display.connect('monitor-removed', self._monitor_removed)

    @property
    def monitor_available(self):
        """Report whether the configured index still identifies an available display."""
        return 0 <= self.monitor_index < self.display.get_n_monitors()

    @property
    def visible(self):
        """Report the requested visibility, including an in-progress fade."""
        return self.window.get_visible() if self._visible is None else self._visible

    @property
    def status_text(self):
        """Describe the output state without conflating it with slide playback."""
        if self._error:
            return self._error
        if self._waiting:
            return _('Waiting for the native fullscreen exit…')
        if self.fade.active:
            return _('Fading output in…') if self.visible else _('Fading output out…')
        return _('Output visible') if self.visible else _('Output hidden')

    def monitors(self):
        """Return available displays in logical desktop coordinates."""
        result = []
        for index in range(self.display.get_n_monitors()):
            monitor = self.display.get_monitor(index)
            geometry = monitor.get_geometry()
            name = monitor.get_model() or monitor.get_manufacturer() or _('Unknown display')
            result.append(dict(index=index, label=_('Display {number} · {name} · {width} × {height}').format(
                number=index + 1, name=name, width=geometry.width, height=geometry.height),
                x=geometry.x, y=geometry.y, width=geometry.width, height=geometry.height))
        return result

    def _notify(self):
        """Refresh status without overwriting unapplied settings drafts."""
        editor = getattr(self.ui, 'output_window', None)
        if editor is not None:
            editor.refresh_status()

    def _cancel_source(self, name):
        """Remove an owned GLib callback once."""
        source = getattr(self, name)
        if source is not None:
            self.GLib.source_remove(source)
            setattr(self, name, None)

    def _set_opacity(self, value):
        """Apply alpha to the native top-level window, once rather than per widget."""
        self.window.realize()
        native = self.window.get_window()
        if native is None:
            raise OutputError(_('The output window is not available.'))
        native.set_opacity(value)
        self.current_opacity = value

    def _stop_fade(self):
        """Sample an interrupted fade and cancel its single callback."""
        value = self.fade.value if self._source is not None else self.current_opacity
        self._cancel_source('_source')
        self.fade.start(value, value, 0)
        self._set_opacity(value)

    def _fade_to(self, target, seconds):
        """Start or reverse one fade from the currently visible opacity."""
        if self._source is not None and self.fade.target == target:
            return
        self._stop_fade()
        self.fade.start(self.current_opacity, target, seconds)
        if self.fade.active:
            self._source = self.GLib.timeout_add(16, self._tick)
        else:
            self._finish_fade()
        self._notify()

    def _tick(self):
        """Advance one native alpha value, then unmap fully hidden output."""
        self._set_opacity(self.fade.value)
        if self.fade.active:
            return True
        self._source = None
        self._finish_fade()
        self._notify()
        return False

    def _finish_fade(self):
        """Apply the exact endpoint and release hidden output from input handling."""
        self._set_opacity(self.fade.target)
        if self.fade.target == 0:
            self.window.hide()
            self.ui.set_screensaver(disabled=False)

    def _save_window(self):
        """Retain normal-window preferences before changing decorations or focus."""
        if self._saved is None:
            self._saved = dict(geometry=self.ui.config.get('content', 'geometry'),
                               decorated=self.window.get_decorated(), resizable=self.window.get_resizable(),
                               accept_focus=self.window.get_accept_focus(), focus_on_map=self.window.get_focus_on_map(),
                               skip_pager=self.window.get_skip_pager_hint(),
                               skip_taskbar=self.window.get_skip_taskbar_hint(),
                               type_hint=self.window.get_type_hint(),
                               above=bool(self.window.get_window().get_state() & self.Gdk.WindowState.ABOVE))

    def _restore_window(self):
        """Restore exactly the settings owned by the borderless mode."""
        if self._restore_native is not None:
            self._restore_native()
            self._restore_native = None
        if self._saved is not None:
            saved, self._saved = self._saved, None
            self.window.set_decorated(saved['decorated'])
            self.window.set_resizable(saved['resizable'])
            self.window.set_accept_focus(saved['accept_focus'])
            self.window.set_focus_on_map(saved['focus_on_map'])
            self.window.set_skip_pager_hint(saved['skip_pager'])
            self.window.set_skip_taskbar_hint(saved['skip_taskbar'])
            self.window.set_type_hint(saved['type_hint'])
            self.window.set_keep_above(saved['above'])
            self.window.parse_geometry(saved['geometry'])

    def apply(self, mode, monitor_index, fade_seconds, target_opacity=1.0):
        """Apply a validated display mode and show output with a single overlay fade."""
        if mode not in ('window', 'fullscreen', 'overlay'):
            raise OutputError(_('Choose a valid output mode.'))
        if type(monitor_index) is not int or not 0 <= monitor_index < self.display.get_n_monitors():
            raise OutputError(_('The selected display is no longer available.'))
        if (isinstance(fade_seconds, bool) or not isinstance(fade_seconds, (int, float)) or
                not math.isfinite(fade_seconds) or not 0 <= fade_seconds <= 5 or target_opacity != 1.0):
            raise OutputError(_('Output fade duration must be between 0 and 5 seconds.'))
        if mode == 'overlay':
            if 'Wayland' in type(self.display).__name__:
                raise OutputError(_('Borderless overlay needs a desktop that supports window positioning and opacity.'))
            if 'X11' in type(self.display).__name__ and not self.window.get_screen().is_composited():
                raise OutputError(_('Borderless overlay requires desktop compositing.'))
        if (mode == self.mode and self.explicit_mode and monitor_index == self.monitor_index and
                fade_seconds == self.fade_seconds and self.visible):
            self.show()
            return
        self._error = ''
        self._stop_fade()
        self._cancel_source('_wait_source')
        self._waiting = False
        self.ui.transition.cancel()
        if self.mode == 'overlay' and mode != 'overlay':
            self.window.hide()
            self._restore_window()
        self.mode, self.monitor_index, self.fade_seconds = mode, monitor_index, float(fade_seconds)
        self.explicit_mode = True
        self._placement_needed = True
        self._selected_monitor = self.display.get_monitor(monitor_index)
        if mode == 'overlay':
            self.window.hide()
            self._set_opacity(0)
        for key, value in (('output_mode', mode), ('output_monitor', monitor_index), ('output_fade', fade_seconds)):
            self.ui.config.set('content', key, str(value))
        self.show()

    def _overlay_geometry(self):
        """Prepare borderless display bounds while the output is fully transparent."""
        self._save_window()
        if self._restore_native is not None:
            self._restore_native()
            self._restore_native = None
        self.window.hide()
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_skip_pager_hint(True)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_keep_above(True)
        self.window.unmaximize()
        geometry = self._selected_monitor.get_geometry()
        self.window.move(geometry.x, geometry.y)
        self.window.resize(geometry.width, geometry.height)

    def _defer_native_exit(self):
        """Keep output transparent while waiting for a confirmed native exit."""
        self._stop_fade()
        self._set_opacity(0)
        self.ui._request_fullscreen(self.window, False)
        self.ui.app.set_action_state('content-fullscreen', False)
        if self._wait_source is None:
            self._waiting = True
            self._wait_started = self._clock()
            self._wait_source = self.GLib.timeout_add(30, self._wait_for_window)
        self._notify()

    def _position_window(self):
        """Place a normal window on the chosen monitor before native fullscreen."""
        geometry = self._selected_monitor.get_geometry()
        width, height = self.window.get_size()
        width, height = min(width, geometry.width), min(height, geometry.height)
        self.window.resize(width, height)
        position = (geometry.x + (geometry.width - width) // 2,
                    geometry.y + (geometry.height - height) // 2)
        self.window.move(*position)
        self.window.show_all()
        self.window.move(*position)
        self._placement_needed = False

    def show(self):
        """Show the configured mode after any conflicting native exit finishes."""
        if not self.monitor_available:
            raise OutputError(_('The selected display is no longer available.'))
        self._error = ''
        self._visible = True
        if self._selected_monitor is None:
            self._selected_monitor = self.display.get_monitor(self.monitor_index)
        native = self.window.get_window()
        fullscreen = native is not None and bool(native.get_state() & self.Gdk.WindowState.FULLSCREEN)
        pending = self.ui._fullscreen_pending_target(self.window)
        same_display = (native is not None and
                        self.display.get_monitor_at_window(native) == self._selected_monitor)
        if self.mode == 'fullscreen' and fullscreen and same_display and pending is None:
            self._placement_needed = False
            self._set_opacity(1)
            self.window.show_all()
            self.ui.app.set_action_state('content-fullscreen', True)
            self.ui.set_screensaver(disabled=True)
        elif self.mode == 'fullscreen' and pending is True and not self._placement_needed:
            self._notify()
            return
        elif fullscreen or pending is not None:
            self._defer_native_exit()
            return
        elif self.mode == 'overlay':
            self._show_overlay()
        else:
            if self.mode == 'fullscreen' or self._placement_needed:
                self._position_window()
            else:
                self.window.show_all()
            self._set_opacity(1)
            enabled = self.mode == 'fullscreen'
            self.ui._request_fullscreen(self.window, enabled, self.monitor_index if enabled else None)
            self.ui.app.set_action_state('content-fullscreen', enabled)
            self.ui.set_screensaver(disabled=enabled)
        self._notify()

    def _show_overlay(self):
        """Map transparent output at its final bounds, then run exactly one fade."""
        if not self.window.get_visible() or self._saved is None:
            self._set_opacity(0)
            self._overlay_geometry()
            self.window.show_all()
            # Some backends honor the final position only after mapping.
            geometry = self._selected_monitor.get_geometry()
            self.window.move(geometry.x, geometry.y)
            self.window.resize(geometry.width, geometry.height)
            # Mapping may reset platform window properties; apply Space support
            # only after GTK has finished its own native-window setup.
            from pympress.macos_overlay import prepare_overlay
            self._restore_native = prepare_overlay(self.window.get_window())
        self.ui.app.set_action_state('content-fullscreen', False)
        self.ui.set_screensaver(disabled=True)
        self._fade_to(1.0, self.fade_seconds)

    def _wait_for_window(self):
        """Defer borderless geometry until a native fullscreen exit is acknowledged."""
        native = self.window.get_window()
        pending = self.ui._fullscreen_pending_target(self.window)
        if native is not None and not native.get_state() & self.Gdk.WindowState.FULLSCREEN and pending is None:
            self._wait_source = None
            self._waiting = False
            if self.visible:
                self.show()
            self._notify()
            return False
        if self._clock() - self._wait_started > 6:
            self._wait_source = None
            self._waiting = False
            self._visible = False
            self.window.hide()
            self.ui.set_screensaver(disabled=False)
            self._error = _('Could not leave native fullscreen. Select Window and try again.')
            self._notify()
            return False
        return True

    def hide(self, immediate=False):
        """Fade overlay output away, then hide the window so video receives input."""
        self.explicit_mode = True
        self._visible = False
        self._cancel_source('_wait_source')
        self._waiting = False
        if self.mode == 'overlay':
            self._fade_to(0.0, 0 if immediate else self.fade_seconds)
        else:
            self.window.hide()
            self.ui._request_fullscreen(self.window, False)
            self.ui.app.set_action_state('content-fullscreen', False)
            self.ui.set_screensaver(disabled=False)
        self._notify()

    def toggle(self):
        """Toggle output visibility in the currently selected display mode."""
        self.hide() if self.visible else self.show()

    def native_state_changed(self, fullscreen):
        """Reflect native fullscreen exits without changing an active overlay mode."""
        if (self.mode != 'overlay' and self.visible and not self._waiting and
                self.ui._fullscreen_pending_target(self.window) is None):
            self.mode = 'fullscreen' if fullscreen else 'window'
        self._notify()

    def _monitor_removed(self, display, monitor):
        """Hide output immediately when its selected physical display disappears."""
        if self._selected_monitor == monitor:
            self.hide(immediate=True)
            self._restore_window()
            self.mode = 'window'
            self._selected_monitor = None
            self.monitor_index = -1
            self.ui.config.set('content', 'output_monitor', '-1')
            self._error = _('The output display was disconnected. Output is hidden.')
            self._notify()
        else:
            self.screen_layout_changed()

    def screen_layout_changed(self):
        """Follow the selected physical monitor through display reordering."""
        if self._selected_monitor is None:
            return
        for index in range(self.display.get_n_monitors()):
            if self.display.get_monitor(index) == self._selected_monitor:
                self.monitor_index = index
                self.ui.config.set('content', 'output_monitor', str(index))
                if self.mode == 'overlay' and self.window.get_visible():
                    geometry = self._selected_monitor.get_geometry()
                    self.window.move(geometry.x, geometry.y)
                    self.window.resize(geometry.width, geometry.height)
                return

    def cleanup(self):
        """Cancel owned callbacks and restore native window behavior on shutdown."""
        self._cancel_source('_wait_source')
        self._stop_fade()
        self._restore_window()
        self.display.disconnect(self._removed_signal)
        self.ui.set_screensaver(disabled=False)
