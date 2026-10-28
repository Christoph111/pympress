#!/usr/bin/env python3
"""Exercise actual quit lifecycle and keyboard callbacks with a modal dialog double.

The double dispatches connected key handlers before its focused-button fallback.
Native GTK event propagation is verified separately in the application smoke test.
"""

try:
    from . import _support
except ImportError:
    import _support


import ast
import logging
import signal
from types import SimpleNamespace
import unittest
from unittest import mock


SOURCE = _support.SOURCE
RESPONSES = SimpleNamespace(OK=-5, CANCEL=-6, DELETE_EVENT=-4)
MODIFIERS = SimpleNamespace(SHIFT_MASK=1, LOCK_MASK=2, CONTROL_MASK=4, MOD1_MASK=8, MOD2_MASK=16,
                            META_MASK=268435456, SUPER_MASK=67108864, HYPER_MASK=134217728)
KEYS = dict(KEY_Return=13, KEY_KP_Enter=14, KEY_space=32, KEY_KP_Space=33, KEY_Escape=27,
            KEY_q=113, KEY_Q=81)


class Dialog:
    def __init__(self, response=RESPONSES.CANCEL, key=None, focused=RESPONSES.CANCEL, on_run=None):
        self.result = response
        self.key = key
        self.focused = focused
        self.on_run = on_run
        self.connected = []
        self.default_activations = 0
        self.destroyed = False
        self.present = mock.Mock()
        self.add_buttons = mock.Mock()
        self.format_secondary_text = mock.Mock()
        self.set_default_response = mock.Mock()

    def connect(self, name, callback):
        self.connected.append((name, callback))

    def response(self, response):
        self.result = response

    def run(self):
        if self.on_run is not None:
            self.on_run()
        if self.key is not None:
            event = SimpleNamespace(keyval=self.key)
            handled = any(callback(self, event) for name, callback in self.connected if name == 'key-press-event')
            activation_keys = (KEYS['KEY_space'], KEYS['KEY_KP_Space'], KEYS['KEY_Return'], KEYS['KEY_KP_Enter'])
            if not handled and self.key in activation_keys:
                self.default_activations += 1
                self.response(self.focused)
        return self.result

    def destroy(self):
        self.destroyed = True


def load_application(gtk, gdk, builder, glib, gio, signals, util):
    path = SOURCE / 'pympress/app.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Pympress')
    names = {'__init__', 'quit', '_request_quit', '_quit_without_confirmation', '_confirm_quit',
             '_on_quit_confirmation_key', 'handle_quit_shortcut', 'do_handle_local_options'}
    methods = [node for node in original.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in methods} != names:
        raise AssertionError('Missing tested application method')
    selected = ast.Module(body=[ast.ClassDef(name='Application', bases=[], keywords=[], body=methods,
                                             decorator_list=[])], type_ignores=[])
    namespace = dict(Gtk=gtk, Gdk=gdk, GLib=glib, Gio=gio, builder=builder,
                     logger=logging.getLogger('quit-tests'), signal=signals, util=util)
    exec(compile(ast.fix_missing_locations(selected), str(path), 'exec'), namespace)
    return namespace['Application']


class QuitTests(unittest.TestCase):
    def setUp(self):
        self.dialog = Dialog()
        accelerator_mask = (MODIFIERS.SHIFT_MASK | MODIFIERS.CONTROL_MASK | MODIFIERS.MOD1_MASK |
                            MODIFIERS.META_MASK | MODIFIERS.SUPER_MASK | MODIFIERS.HYPER_MASK)
        self.gtk = SimpleNamespace(
            Application=SimpleNamespace(__init__=mock.Mock(), quit=mock.Mock()),
            MessageDialog=mock.Mock(side_effect=lambda **kwargs: self.dialog), ResponseType=RESPONSES,
            DialogFlags=SimpleNamespace(MODAL=1, DESTROY_WITH_PARENT=2),
            MessageType=SimpleNamespace(QUESTION=1),
            accelerator_get_default_mod_mask=lambda: accelerator_mask)
        self.gdk = SimpleNamespace(ModifierType=MODIFIERS,
                                   EventType=SimpleNamespace(KEY_PRESS=1, KEY_RELEASE=2), **KEYS)
        self.builder = SimpleNamespace(Builder=SimpleNamespace(setup_actions=mock.Mock()))
        self.glib = SimpleNamespace(set_application_name=mock.Mock())
        self.gio = SimpleNamespace(ApplicationFlags=SimpleNamespace(HANDLES_OPEN=1, CAN_OVERRIDE_APP_ID=2))
        self.signals = SimpleNamespace(SIGINT=signal.SIGINT, signal=mock.Mock())
        self.util = SimpleNamespace(IS_MAC_OS=False)
        cls = load_application(self.gtk, self.gdk, self.builder, self.glib, self.gio, self.signals, self.util)
        self.app = object.__new__(cls)
        self.app.register = mock.Mock()
        self.app.get_is_remote = mock.Mock(return_value=False)
        self.app.set_log_level = mock.Mock()
        self.app.options = self.app.option_descriptions = {}
        cls.__init__(self.app)
        self.app.gui = SimpleNamespace(p_win=object(), unsaved_changes=mock.Mock(return_value=False),
                                       set_screensaver=mock.Mock())

    def test_cancel_keeps_windows_and_unsaved_work_untouched_and_consumes_delete(self):
        self.assertTrue(self.app.quit(object(), object()))
        self.dialog.set_default_response.assert_called_once_with(RESPONSES.CANCEL)
        self.assertTrue(self.dialog.destroyed)
        self.assertIsNone(self.app._quit_dialog)
        self.assertFalse(self.app._quit_pending)
        self.app.gui.unsaved_changes.assert_not_called()
        self.app.gui.set_screensaver.assert_not_called()
        self.gtk.Application.quit.assert_not_called()

    def test_dialog_window_close_is_cancellation(self):
        self.dialog.result = RESPONSES.DELETE_EVENT
        self.assertTrue(self.app.quit())
        self.gtk.Application.quit.assert_not_called()

    def test_return_confirms_even_when_cancel_button_is_focused(self):
        for key in (KEYS['KEY_Return'], KEYS['KEY_KP_Enter']):
            with self.subTest(key=key):
                self.app._quit_started = False
                self.dialog = Dialog(key=key, focused=RESPONSES.CANCEL)
                self.assertFalse(self.app.quit())
                self.assertEqual(self.dialog.default_activations, 0)
                self.assertTrue(self.dialog.destroyed)
        self.assertEqual(self.gtk.Application.quit.call_count, 2)

    def test_space_and_escape_cancel_even_when_ok_button_is_focused(self):
        for key in (KEYS['KEY_space'], KEYS['KEY_KP_Space'], KEYS['KEY_Escape']):
            with self.subTest(key=key):
                self.dialog = Dialog(key=key, focused=RESPONSES.OK)
                self.assertTrue(self.app.quit())
                self.assertEqual(self.dialog.default_activations, 0)
                self.assertTrue(self.dialog.destroyed)
        self.app.gui.unsaved_changes.assert_not_called()
        self.gtk.Application.quit.assert_not_called()

    def test_other_keys_are_not_consumed_by_the_confirmation(self):
        self.assertFalse(self.app._on_quit_confirmation_key(self.dialog, SimpleNamespace(keyval=KEYS['KEY_q'])))
        self.assertEqual(self.dialog.result, RESPONSES.CANCEL)

    def test_confirmation_precedes_existing_unsaved_protection_and_single_shutdown(self):
        events = []
        self.dialog = Dialog(response=RESPONSES.OK, on_run=lambda: events.append('confirm'))
        self.app.gui.unsaved_changes.side_effect = lambda: events.append('unsaved') or False
        self.app.gui.set_screensaver.side_effect = lambda value: events.append(('screensaver', value))
        self.gtk.Application.quit.side_effect = lambda app: events.append('quit')
        self.assertFalse(self.app.quit())
        self.assertFalse(self.app.quit())
        self.assertEqual(events, ['confirm', 'unsaved', ('screensaver', False), 'quit'])
        self.gtk.MessageDialog.assert_called_once()
        self.assertFalse(self.app._quit_pending)

    def test_unsaved_cancel_prevents_shutdown_and_allows_another_request(self):
        self.dialog.result = RESPONSES.OK
        self.app.gui.unsaved_changes.return_value = True
        self.assertTrue(self.app.quit())
        self.assertFalse(self.app._quit_started)
        self.app.gui.set_screensaver.assert_not_called()
        self.gtk.Application.quit.assert_not_called()
        self.app.gui.unsaved_changes.return_value = False
        self.dialog = Dialog(response=RESPONSES.OK)
        self.assertFalse(self.app.quit())
        self.gtk.Application.quit.assert_called_once_with(self.app)

    def test_reentrant_request_presents_existing_dialog_without_duplicate_prompt(self):
        nested = []
        self.dialog.on_run = lambda: nested.append(self.app.quit())
        self.assertTrue(self.app.quit())
        self.assertEqual(nested, [True])
        self.dialog.present.assert_called_once()
        self.gtk.MessageDialog.assert_called_once()
        self.gtk.Application.quit.assert_not_called()

    def test_reentrant_request_during_unsaved_prompt_cannot_duplicate_cleanup(self):
        nested = []
        self.dialog.result = RESPONSES.OK

        def unsaved():
            nested.append(self.app.quit())
            return False

        self.app.gui.unsaved_changes.side_effect = unsaved
        self.assertFalse(self.app.quit())
        self.assertEqual(nested, [True])
        self.gtk.MessageDialog.assert_called_once()
        self.app.gui.set_screensaver.assert_called_once_with(False)
        self.gtk.Application.quit.assert_called_once_with(self.app)

    def test_failed_dialog_releases_guard_and_destroys_the_window(self):
        self.dialog.run = mock.Mock(side_effect=RuntimeError('dialog failure'))
        with self.assertRaises(RuntimeError):
            self.app.quit()
        self.assertFalse(self.app._quit_pending)
        self.assertFalse(self.app._quit_started)
        self.assertIsNone(self.app._quit_dialog)
        self.assertTrue(self.dialog.destroyed)
        self.dialog = Dialog(response=RESPONSES.OK)
        self.assertFalse(self.app.quit())

    def test_failed_base_quit_allows_a_later_exit_request(self):
        self.dialog.result = RESPONSES.OK
        self.gtk.Application.quit.side_effect = RuntimeError('base quit failure')
        with self.assertRaises(RuntimeError):
            self.app.quit()
        self.assertFalse(self.app._quit_started)
        self.assertFalse(self.app._quit_pending)
        self.gtk.Application.quit.side_effect = None
        self.dialog = Dialog(response=RESPONSES.OK)
        self.assertFalse(self.app.quit())

    def test_no_gui_exits_without_showing_a_confirmation(self):
        self.app.gui = None
        self.assertFalse(self.app.quit())
        self.gtk.MessageDialog.assert_not_called()
        self.gtk.Application.quit.assert_called_once_with(self.app)

    def test_signal_and_cli_action_bypass_only_the_new_confirmation(self):
        handler = self.signals.signal.call_args.args[1]
        self.assertEqual(handler, self.app._quit_without_confirmation)
        actions = self.builder.Builder.setup_actions.call_args.args[0]
        self.assertEqual(actions['quit-without-confirmation']['activate'], handler)
        self.assertFalse(handler(signal.SIGINT, None))
        self.gtk.MessageDialog.assert_not_called()
        self.app.gui.unsaved_changes.assert_called_once()
        self.app.gui.set_screensaver.assert_called_once_with(False)
        self.gtk.Application.quit.assert_called_once_with(self.app)

    def test_automation_still_cannot_discard_unsaved_work(self):
        self.app.gui.unsaved_changes.return_value = True
        self.assertTrue(self.app._quit_without_confirmation())
        self.gtk.MessageDialog.assert_not_called()
        self.gtk.Application.quit.assert_not_called()

    def test_command_line_quit_uses_the_explicit_noninteractive_action(self):
        self.app.activate_action = mock.Mock()
        options = SimpleNamespace(end=lambda: SimpleNamespace(unpack=lambda: {'quit': True}))
        self.assertEqual(self.app.do_handle_local_options(options), -1)
        self.app.activate_action.assert_called_once_with('quit-without-confirmation')

    def test_primary_quit_shortcut_is_consumed_including_when_dialog_is_cancelled(self):
        for primary in (MODIFIERS.CONTROL_MASK, MODIFIERS.META_MASK, MODIFIERS.SUPER_MASK):
            for key in (KEYS['KEY_q'], KEYS['KEY_Q']):
                with self.subTest(primary=primary, key=key):
                    event = SimpleNamespace(type=self.gdk.EventType.KEY_PRESS, keyval=key,
                                            state=primary | MODIFIERS.LOCK_MASK | MODIFIERS.MOD2_MASK)
                    self.assertTrue(self.app.handle_quit_shortcut(event))
        self.assertEqual(self.gtk.MessageDialog.call_count, 6)
        self.gtk.Application.quit.assert_not_called()

    def test_plain_modified_and_release_keys_do_not_quit(self):
        states = [0, MODIFIERS.LOCK_MASK, MODIFIERS.CONTROL_MASK | MODIFIERS.SHIFT_MASK,
                  MODIFIERS.META_MASK | MODIFIERS.MOD1_MASK, MODIFIERS.SUPER_MASK | MODIFIERS.HYPER_MASK]
        events = [SimpleNamespace(type=self.gdk.EventType.KEY_PRESS, keyval=KEYS['KEY_q'], state=state)
                  for state in states]
        events += [SimpleNamespace(type=self.gdk.EventType.KEY_RELEASE, keyval=KEYS['KEY_q'],
                                   state=MODIFIERS.META_MASK),
                   SimpleNamespace(type=self.gdk.EventType.KEY_PRESS, keyval=KEYS['KEY_Return'],
                                   state=MODIFIERS.META_MASK)]
        for event in events:
            with self.subTest(event=event):
                self.assertFalse(self.app.handle_quit_shortcut(event))
        self.gtk.MessageDialog.assert_not_called()
        self.gtk.Application.quit.assert_not_called()

    def test_mac_command_accepts_native_raw_and_virtual_modifier_states(self):
        self.util.IS_MAC_OS = True
        self.gtk.accelerator_get_default_mod_mask = lambda: 469762077

        def add_virtual(state):
            return state | MODIFIERS.META_MASK if state & MODIFIERS.MOD2_MASK else state

        keymap = SimpleNamespace(add_virtual_modifiers=mock.Mock(side_effect=add_virtual))
        self.gdk.Keymap = SimpleNamespace(get_default=lambda: keymap)
        for state in (16, 268435472, 268435456, 268435474):
            with self.subTest(state=state):
                event = SimpleNamespace(type=self.gdk.EventType.KEY_PRESS, keyval=KEYS['KEY_q'], state=state)
                self.assertTrue(self.app.handle_quit_shortcut(event))
        self.assertEqual(self.gtk.MessageDialog.call_count, 4)
        self.gtk.Application.quit.assert_not_called()

    def test_mac_command_does_not_swallow_shift_or_option_variants(self):
        self.util.IS_MAC_OS = True
        self.gtk.accelerator_get_default_mod_mask = lambda: 469762077
        self.gdk.Keymap = SimpleNamespace(get_default=lambda: SimpleNamespace(
            add_virtual_modifiers=lambda state: state | MODIFIERS.META_MASK
            if state & MODIFIERS.MOD2_MASK else state))
        for extra in (MODIFIERS.SHIFT_MASK, MODIFIERS.MOD1_MASK, MODIFIERS.HYPER_MASK):
            for command in (16, 268435472):
                with self.subTest(extra=extra, command=command):
                    event = SimpleNamespace(type=self.gdk.EventType.KEY_PRESS,
                                            keyval=KEYS['KEY_q'], state=command | extra)
                    self.assertFalse(self.app.handle_quit_shortcut(event))
        self.gtk.MessageDialog.assert_not_called()

    def test_mac_physical_mod2_is_only_removed_when_keymap_maps_it_to_command(self):
        self.util.IS_MAC_OS = True
        self.gtk.accelerator_get_default_mod_mask = lambda: 469762077
        keymap = SimpleNamespace(add_virtual_modifiers=lambda state: state)
        self.gdk.Keymap = SimpleNamespace(get_default=lambda: keymap)
        for state in (16, 268435472):
            with self.subTest(state=state):
                event = SimpleNamespace(type=self.gdk.EventType.KEY_PRESS, keyval=KEYS['KEY_q'], state=state)
                self.assertFalse(self.app.handle_quit_shortcut(event))
        self.gtk.MessageDialog.assert_not_called()

    def test_linux_control_with_numlock_does_not_use_mac_command_mapping(self):
        self.gdk.Keymap = SimpleNamespace(get_default=mock.Mock(side_effect=AssertionError('Mac-only mapping')))
        event = SimpleNamespace(type=self.gdk.EventType.KEY_PRESS, keyval=KEYS['KEY_q'],
                                state=MODIFIERS.CONTROL_MASK | MODIFIERS.MOD2_MASK)
        self.assertTrue(self.app.handle_quit_shortcut(event))
        self.gdk.Keymap.get_default.assert_not_called()
        self.gtk.MessageDialog.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
