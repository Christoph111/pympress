# -*- coding: utf-8 -*-
#
#       pympress
#
#       Copyright 2009, 2010 Thomas Jost <thomas.jost@gmail.com>
#       Copyright 2015 Cimbali <me@cimba.li>
#
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 2 of the License, or
#       (at your option) any later version.
#
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.
"""
:mod:`pympress.app` -- The Gtk.Application managing the lifetime and CLI
------------------------------------------------------------------------
"""

import logging
logger = logging.getLogger(__name__)

import os
import sys
import signal
import platform

import gi
import cairo
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Gio

from pympress import util, config, document, ui, builder


class Pympress(Gtk.Application):
    """ Class representing the single pympress Gtk application.
    """
    #: The :class:`~pympress.ui.UI` object that is the interface of pympress
    gui = None
    #: The :class:`~pympress.config.Config` object that holds pympress conferences
    config = None
    #: `list` of actions to be passed to the GUI that were queued before GUI was created
    action_startup_queue = []
    #: `bool` to automatically upgrade log level (DEBUG / INFO at init, then ERROR), False if user set log level
    auto_log_level = True

    options = {
        # long_name:  (short_name (int), flags (GLib.OptionFlags), arg (GLib.OptionArg)
        'talk-time':  (ord('t'), GLib.OptionFlags.NONE, GLib.OptionArg.STRING),
        'notes':      (ord('N'), GLib.OptionFlags.NONE, GLib.OptionArg.STRING),
        'log':        (0,        GLib.OptionFlags.NONE, GLib.OptionArg.STRING),
        'version':    (ord('v'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE),
        'pause':      (ord('P'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE),
        'reset':      (ord('r'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE),
        'next':       (ord('n'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE),
        'prev':       (ord('p'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE),
        'first':      (ord('f'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE),
        'last':       (ord('l'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE),
        'blank':      (ord('b'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE),
        'quit':       (ord('q'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE),
    }

    option_descriptions = {
        #  long_name: (description, arg_description)
        'talk-time': (_('The estimated (intended) talk time in minutes') + ' ' +
                      _('(and optionally seconds)'), 'mm[:ss]'),
        'notes':     (_('Set the position of notes on the pdf page') + ' ' +
                      _('(none, left, right, top, bottom, after, odd, or prefix).') + ' ' +
                      _('Overrides the detection from the file.'), '<position>'),
        'log':       (_('Set level of verbosity in log file:') + ' ' +
                      _('{}, {}, {}, {}, or {}').format('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'), '<level>'),
        'version':   (_('Print version and exit'), None),
        'pause':     (_('Toggle pause of talk timer'), None),
        'reset':     (_('Reset talk timer'), None),
        'next':      (_('Next slide'), None),
        'prev':      (_('Previous slide'), None),
        'first':     (_('First slide'), None),
        'last':      (_('Last slide'), None),
        'blank':     (_('Blank/unblank content screen'), None),
        'quit':      (_('Close opened pympress instance'), None),
    }

    version_string = ' '.join([
        'Pympress:', util.get_pympress_meta()['version'],
        '; Python:', platform.python_version(),
        '; OS:', platform.system(), platform.release(), platform.version(),
        '; Gtk {}.{}.{}'.format(Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version()),
        '; GLib {}.{}.{}'.format(GLib.MAJOR_VERSION, GLib.MINOR_VERSION, GLib.MICRO_VERSION),
        '; Poppler', document.Poppler.get_version(), document.Poppler.get_backend().value_nick,
        '; Cairo', cairo.cairo_version_string(), ', pycairo', cairo.version,
    ])


    def __init__(self):
        GLib.set_application_name('pympress')
        # GLib.set_prgname('pympress')  # Let prgname be auto-determined from sys.argv[0]
        Gtk.Application.__init__(self, application_id='io.github.pympress',
                                 flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.CAN_OVERRIDE_APP_ID)
        self._quit_pending = self._quit_started = False
        self._quit_dialog = None

        self.register(None)

        if not self.get_is_remote():
            builder.Builder.setup_actions({
                'log-level': dict(activate=self.set_log_level, state=logger.getEffectiveLevel(), parameter_type=int),
                'quit-without-confirmation': dict(activate=self._quit_without_confirmation),
            }, action_map=self)

        # Connect proper exit function to interrupt
        signal.signal(signal.SIGINT, self._quit_without_confirmation)

        for opt in self.options:
            self.add_main_option(opt, *self.options[opt], *self.option_descriptions.get(opt, ['', None]))


    def handle_quit_shortcut(self, event):
        """Consume a primary-modifier Q press before focused controls handle it."""
        if event.type != Gdk.EventType.KEY_PRESS or event.keyval not in (Gdk.KEY_q, Gdk.KEY_Q):
            return False
        primary = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.SUPER_MASK
        state = event.state
        if util.IS_MAC_OS:
            keymap = Gdk.Keymap.get_default()
            if keymap is not None:
                state = keymap.add_virtual_modifiers(state)
                command = keymap.add_virtual_modifiers(Gdk.ModifierType.MOD2_MASK)
                if command & Gdk.ModifierType.META_MASK:
                    state &= ~Gdk.ModifierType.MOD2_MASK
        state &= Gtk.accelerator_get_default_mod_mask()
        if state & primary and not state & ~primary:
            self.quit()
            return True
        return False


    def quit(self, *args):  # noqa: A003 -- we need to override app.quit()
        """Request interactive exit, consuming window deletion when cancelled."""
        return self._request_quit(confirm=True)


    def _quit_without_confirmation(self, *args):
        """Keep CLI and SIGINT exit non-interactive except for unsaved work."""
        return self._request_quit(confirm=False)


    def _request_quit(self, confirm):
        """Run one exit request while preserving unsaved-document protection."""
        if self._quit_started:
            return False
        if self._quit_pending:
            if self._quit_dialog is not None:
                self._quit_dialog.present()
            return True
        self._quit_pending = True
        try:
            if self.gui is not None:
                if confirm and not self._confirm_quit():
                    return True
                if self.gui.unsaved_changes():
                    return True
                self.gui.set_screensaver(False)
            self._quit_started = True
            try:
                Gtk.Application.quit(self)
            except Exception:
                self._quit_started = False
                raise
            return False
        finally:
            self._quit_pending = False


    def _confirm_quit(self):
        """Ask before closing the presentation, with a conservative default."""
        dialog = Gtk.MessageDialog(transient_for=self.gui.p_win,
                                   flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                                   message_type=Gtk.MessageType.QUESTION, text=_('Quit Pympress?'))
        dialog.format_secondary_text(_('The presentation and all Pympress windows will close.\n'
                                       'Press Enter to quit, or Space or Escape to cancel.'))
        dialog.add_buttons(_('Cancel'), Gtk.ResponseType.CANCEL, _('OK'), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        dialog.connect('key-press-event', self._on_quit_confirmation_key)
        self._quit_dialog = dialog
        try:
            return dialog.run() == Gtk.ResponseType.OK
        finally:
            self._quit_dialog = None
            dialog.destroy()


    def _on_quit_confirmation_key(self, dialog, event):
        """Handle explicit quit keys before GTK activates a focused button."""
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            dialog.response(Gtk.ResponseType.OK)
            return True
        if event.keyval in (Gdk.KEY_space, Gdk.KEY_KP_Space, Gdk.KEY_Escape):
            dialog.response(Gtk.ResponseType.CANCEL)
            return True
        return False


    def do_startup(self):
        """ Common start-up tasks for primary and remote instances.

        NB. super(self) causes segfaults, Gtk.Application needs to be used as base.
        """
        self.config = config.Config()

        # prefer X11 on posix systems because Wayland still has some shortcomings for us,
        # specifically libVLC and the ability to disable screensavers
        if util.IS_POSIX:
            Gdk.set_allowed_backends('x11,*')

        logger.info(self.version_string)

        # If there is no display, we can’t get further than this.
        if os.getenv('PYMPRESS_HEADLESS_TEST', ''):
            sys.exit(0)

        Gtk.Application.do_startup(self)


    def do_activate(self, timestamp=GLib.get_current_time()):
        """ Activate: show UI windows.

        Build them if they do not exist, otherwise bring to front.
        """
        if self.gui is None:
            if self.auto_log_level:
                self.activate_action('log-level', logging.INFO)
                self.action_startup_queue.append(('log-level', logging.ERROR))

            # Build the UI and windows
            self.gui = ui.UI(self, self.config)

            while self.action_startup_queue:
                self.activate_action(*self.action_startup_queue.pop(0))

        Gtk.Application.do_activate(self)
        self.gui.p_win.present_with_time(timestamp)


    def set_action_enabled(self, name, value):
        """ Parse an action name and set its enabled state to True or False.

        Args:
            name (`str`): the name of the stateful action
            value (`bool`): wheether the action should be enabled or disabled
        """
        self.lookup_action(name).set_enabled(value)


    def set_action_state(self, name, value):
        """ Parse an action name and set its state wrapped in a :class:`~GLib.Variant`.

        Args:
            name (`str`): the name of the stateful action
            value (`str`, `int`, `bool` or `float`): the value to set.
        """
        self.lookup_action(name).change_state(GLib.Variant(builder.Builder._glib_type_strings[type(value)], value))


    def get_action_state(self, name):
        """ Parse an action name and return its unwrapped state from the :class:`~GLib.Variant`.

        Args:
            name (`str`): the name of the stateful action

        Returns:
            `str`, `int`, `bool` or `float`: the value contained in the action
        """
        state = self.lookup_action(name).get_state()
        return builder.Builder._glib_type_getters[state.get_type_string()](state)


    def activate_action(self, name, parameter=None):
        """ Parse an action name and activate it, with parameter wrapped in a :class:`~GLib.Variant` if it is not None.

        Args:
            name (`str`): the name of the stateful action
            parameter: an object or None to pass as a parameter to the action, wrapped in a GLib.Variant
        """
        if not self.get_is_remote() and self.gui is None and name not in ['log-level']:
            self.action_startup_queue.append((name, parameter))
            return

        if parameter is not None:
            parameter = GLib.Variant(builder.Builder._glib_type_strings[type(parameter)], parameter)

        Gio.ActionGroup.activate_action(self, name, parameter)


    def do_open(self, files, n_files, hint):
        """ Add all requested files in order and open the first added file.

        Args:
            files (`list` of :class:`~Gio.File`): representing an array of files to open
            n_files (`int`): the number of files passed.
            hint (`str`): a hint, such as view, edit, etc. Should always be the empty string.
        """
        if not n_files:
            return

        self.do_activate(timestamp=GLib.get_current_time())
        self.gui.file_list_add_paths([file.get_uri() for file in files[:n_files]], activate=True)


    def do_shutdown(self):
        """ Perform various cleanups and save preferences.
        """
        if self.gui is not None:
            self.gui.cleanup()

        self.config.save_config()
        Gtk.Application.do_shutdown(self)
        util.close_opened_resources()


    def set_log_level(self, action, param):
        """ Action that sets the logging level (on the root logger of the active instance)

        Args:
            action (:class:`~Gio.Action`): The action activatd
            param (:class:~`GLib.Variant`): The desired level as an int wrapped in a GLib.Variant
        """
        logging.getLogger(None).setLevel(param.get_int64())
        action.change_state(param)


    def do_handle_local_options(self, opts_variant_dict):
        """ Parse command line options, returned as a VariantDict

        Returns:
            `tuple`: estimated talk time, log level, notes positions.
        """
        # convert GVariantDict -> GVariant -> dict
        opts = opts_variant_dict.end().unpack()

        simple_actions = {
            'pause': 'pause-timer',
            'reset': 'reset-timer',
            'next': 'next-page',
            'prev': 'prev-page',
            'blank': 'blank-screen',
            'quit': 'quit-without-confirmation',
            'first': 'first-page',
            'last': 'last-page',
        }

        for opt, arg in opts.items():
            if opt == "version":
                print(self.version_string)
                return 0

            elif opt == "log":
                numeric_level = getattr(logging, arg.upper(), None)
                if isinstance(numeric_level, int):
                    self.auto_log_level = False
                    self.activate_action('log-level', numeric_level)
                else:
                    print(_("Invalid log level \"{}\", try one of {}").format(
                        arg, "DEBUG, INFO, WARNING, ERROR, CRITICAL"
                    ))

            elif opt == "notes":
                arg = arg.lower()[:1]
                if arg == 'n': self.activate_action('notes-pos', 'none')
                if arg == 'l': self.activate_action('notes-pos', 'left')
                if arg == 'r': self.activate_action('notes-pos', 'right')
                if arg == 't': self.activate_action('notes-pos', 'top')
                if arg == 'b': self.activate_action('notes-pos', 'bottom')
                if arg == 'a': self.activate_action('notes-pos', 'after')
                if arg == 'o': self.activate_action('notes-pos', 'odd')
                if arg == 'p': self.activate_action('notes-pos', 'map')  # prefix -> map

            elif opt == "talk-time":
                t = ["0" + n.strip() for n in arg.split(':')]
                try:
                    m = int(t[0])
                    s = int(t[1])
                except ValueError:
                    print(_("Invalid time (mm or mm:ss expected), got \"{}\"").format(arg))
                    return 2
                except IndexError:
                    s = 0
                self.activate_action('set-talk-time', m * 60 + s)

            elif opt in simple_actions:
                self.activate_action(simple_actions[opt])

        return -1
