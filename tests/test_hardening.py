#!/usr/bin/env python3
"""Focused regressions for document trust boundaries; no GUI or active payloads.

Run: python -m unittest tests.test_hardening
The target's actual module definitions are compiled after replacing only its GI
and OS-handler imports with stubs. All filesystem operations stay in TemporaryDirectory.
"""

try:
    from . import _support
except ImportError:
    import _support


import ast
import contextlib
import enum
import logging
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock
from urllib.parse import urlsplit
from urllib.request import url2pathname

SOURCE = _support.SOURCE


def enum_values(name, values):
    return enum.IntEnum(name, values.split())


def load_document(source):
    path = source if source.is_file() else source / 'pympress' / 'document.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    clean = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [alias for alias in node.names if alias.name != 'gi']
            if names:
                node.names = names
                clean.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module in {'gi.repository', 'pympress.util'}:
            continue
        elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and
              isinstance(node.value.func, ast.Attribute) and
              isinstance(node.value.func.value, ast.Name) and
              node.value.func.value.id == 'gi'):
            continue
        else:
            clean.append(node)
    tree.body = clean
    module = types.ModuleType('document_under_test')
    module.__file__ = str(path)
    module.__dict__['_'] = lambda text: text
    module.Poppler = types.SimpleNamespace(
        ActionType=enum_values(
            'ActionType',
            'NONE GOTO_DEST NAMED LAUNCH URI RENDITION MOVIE GOTO_REMOTE OCG_STATE JAVASCRIPT UNKNOWN'),
        DestType=enum_values(
            'DestType',
            'UNKNOWN NAMED XYZ'),
        AnnotType=enum_values(
            'AnnotType',
            'LINK MOVIE SCREEN FILE_ATTACHMENT TEXT POPUP FREE_TEXT STRIKE_OUT HIGHLIGHT UNDERLINE '
            'SQUIGGLY POLYGON POLY_LINE SQUARE CIRCLE CARET LINE STAMP INK UNKNOWN'),
    )
    module.fileopen = mock.Mock(name='blocked_OS_fileopen')
    exec(compile(ast.fix_missing_locations(tree), str(path), 'exec'), module.__dict__)
    module.webbrowser = types.SimpleNamespace(open_new_tab=mock.Mock(name='browser_open'))
    return module, path


@contextlib.contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class FakeGLibError(Exception):
    pass


class InertDialog:
    def __init__(self, gtk, **options):
        self.gtk = gtk
        self.options = options
        self.destroyed = False
        gtk.dialogs.append(self)

    def add_button(self, *args):
        return types.SimpleNamespace(set_image=lambda image: None)

    def set_position(self, *args):
        pass

    def set_markup(self, *args):
        pass

    def format_secondary_markup(self, *args):
        pass

    def set_default_response(self, *args):
        pass

    def run(self):
        return self.gtk.responses.pop(0) if self.gtk.responses else self.gtk.ResponseType.CLOSE

    def destroy(self):
        self.destroyed = True


def load_ui_save_methods(path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    ui = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'UI')
    methods = [node for node in ui.body if isinstance(node, ast.FunctionDef) and
               node.name in {'unsaved_changes', '_save_document', 'save_file'}]
    gtk = types.SimpleNamespace(
        dialogs=[], responses=[], ResponseType=enum_values('ResponseType', 'YES NO CANCEL CLOSE'),
        MessageType=types.SimpleNamespace(WARNING='warning', ERROR='error'),
        WindowPosition=types.SimpleNamespace(CENTER='center'),
        DialogFlags=types.SimpleNamespace(MODAL='modal'), IconSize=types.SimpleNamespace(BUTTON='button'),
        STOCK_SAVE='save', STOCK_CANCEL='cancel', STOCK_CLOSE='close',
        Button=types.SimpleNamespace(new_from_icon_name=lambda *args: types.SimpleNamespace(get_image=lambda: None)))
    gtk.MessageDialog = lambda **kwargs: InertDialog(gtk, **kwargs)
    module = types.ModuleType('ui_save_under_test')
    module.__dict__.update({'Gtk': gtk, 'GLib': types.SimpleNamespace(Error=FakeGLibError),
                           'logger': logging.getLogger('ui_save_under_test'), '_': lambda text: text})
    selected = ast.Module(body=[ast.ClassDef(name='UiSaveMethods', bases=[], keywords=[],
                                             body=methods, decorator_list=[])], type_ignores=[])
    exec(compile(ast.fix_missing_locations(selected), str(path), 'exec'), module.__dict__)
    return module.UiSaveMethods, gtk


class NativePage:
    def __init__(self, mappings=()):
        self.mappings = list(mappings)
        self.active_annotations = []

    def get_label(self):
        return '1'

    def get_size(self):
        return (960, 540)

    def get_link_mapping(self):
        return []

    def get_annot_mapping(self):
        return self.mappings

    def add_annot(self, annotation):
        self.active_annotations.append(annotation)

    def remove_annot(self, annotation):
        self.active_annotations.remove(annotation)


class NativeDocument:
    saved_bytes = b'%PDF-inert-saved-fixture\n'

    def __init__(self, outcome=True):
        self.page = NativePage()
        self.outcome = outcome
        self.saved_uris = []
        self.saved_annotations = []

    def get_n_pages(self):
        return 1

    def get_page(self, number):
        return self.page

    def find_dest(self, name):
        return None

    def save(self, uri):
        self.saved_uris.append(uri)
        self.saved_annotations.append(list(self.page.active_annotations))
        path = Path(url2pathname(urlsplit(uri).path))
        path.write_bytes(self.saved_bytes)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class HardeningTests(unittest.TestCase):
    target = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pympress-hardening-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input_dir = self.root / 'input'
        self.cwd = self.root / 'cwd'
        self.input_dir.mkdir()
        self.cwd.mkdir()
        self.input = self.input_dir / 'slides.pdf'
        self.input.write_bytes(b'%PDF-inert-original\n')
        self.sentinel = self.cwd / self.input.name
        self.sentinel.write_bytes(b'KEEP CWD SENTINEL\n')
        self.target.fileopen.reset_mock()
        self.target.webbrowser.open_new_tab.reset_mock()

    def document(self, uri=None, outcome=True):
        native = NativeDocument(outcome)
        doc = self.target.Document(None, native, uri or self.input.as_uri())
        return doc, native

    def page(self):
        parent = types.SimpleNamespace(goto_page=mock.Mock(), doc=NativeDocument(),
                                       get_full_path=mock.Mock(return_value=self.input),
                                       play_media=mock.Mock(), remove_on_exit=mock.Mock())
        return self.target.Page(NativePage(), 0, parent), parent

    def invoke(self, callback):
        if callback is not None:
            self.assertTrue(callable(callback))
            callback()

    def assert_no_external_handler(self):
        self.target.fileopen.assert_not_called()
        self.target.webbrowser.open_new_tab.assert_not_called()

    def test_absolute_file_uri_preserves_full_path(self):
        with working_directory(self.cwd):
            doc, _ = self.document()
        self.assertEqual(doc.path.resolve(), self.input.resolve())

    def test_relative_path_preserves_parent_directories(self):
        relative = Path('nested') / 'slides.pdf'
        (self.cwd / relative.parent).mkdir()
        (self.cwd / relative).write_bytes(b'%PDF-inert-relative\n')
        with working_directory(self.cwd):
            doc, _ = self.document(str(relative))
            self.assertEqual(doc.path.resolve(), (self.cwd / relative).resolve())

    def test_percent_unicode_space_and_fragment_filename(self):
        path = self.input_dir / 'talk ü 100% #1.pdf'
        path.write_bytes(b'%PDF-inert-unicode\n')
        with working_directory(self.cwd):
            doc, _ = self.document(path.as_uri())
        self.assertEqual(doc.path.resolve(), path.resolve())

    def test_in_place_save_updates_selected_file_and_preserves_cwd_sentinel(self):
        with working_directory(self.cwd):
            doc, native = self.document()
            doc.changes = True
            doc.save_changes()
        self.assertEqual(self.input.read_bytes(), native.saved_bytes)
        self.assertEqual(self.sentinel.read_bytes(), b'KEEP CWD SENTINEL\n')
        self.assertFalse(doc.changes)
        self.assertEqual(set(self.input_dir.iterdir()), {self.input})

    def test_symlink_save_preserves_link_and_updates_target(self):
        link = self.input_dir / 'alias.pdf'
        link.symlink_to(self.input.name)
        with working_directory(self.cwd):
            doc, native = self.document(link.as_uri())
            doc.changes = True
            doc.save_changes()
        self.assertTrue(link.is_symlink(), 'In-place save must not replace the symlink itself')
        self.assertEqual(self.input.read_bytes(), native.saved_bytes)
        self.assertEqual(link.read_bytes(), native.saved_bytes)
        self.assertFalse((self.cwd / link.name).exists())

    def test_nonlocal_uri_cannot_be_saved_in_place(self):
        for uri in ['https://example.invalid/slides.pdf', 'file://remote-host/slides.pdf',
                    self.input.as_uri() + '?unexpected=query', self.input.as_uri() + '#unexpected-fragment']:
            with self.subTest(uri=uri), working_directory(self.cwd):
                native = NativeDocument()
                try:
                    doc = self.target.Document(None, native, uri)
                except (ValueError, OSError):
                    continue
                doc.changes = True
                error = AssertionError('No filesystem writes for nonlocal URI')
                with mock.patch.object(self.target.tempfile, 'NamedTemporaryFile', side_effect=error) as temporary:
                    try:
                        doc.save_changes()
                    except (ValueError, OSError):
                        pass
                    temporary.assert_not_called()
                self.assertEqual(native.saved_uris, [])
                self.assertTrue(doc.changes)
                self.assertEqual(self.sentinel.read_bytes(), b'KEEP CWD SENTINEL\n')

    def test_localhost_file_uri_is_local_and_decoded_once(self):
        path = self.input_dir / 'literal%20name.pdf'
        path.write_bytes(b'%PDF-inert-literal-percent\n')
        uri = 'file://localhost' + path.as_uri()[len('file://'):]
        with working_directory(self.cwd):
            doc, _ = self.document(uri)
        self.assertEqual(doc.path.resolve(), path.resolve())

    def test_save_as_symlink_alias_updates_target_and_preserves_link(self):
        alias = self.input_dir / 'save-as-alias.pdf'
        alias.symlink_to(self.input.name)
        with working_directory(self.cwd):
            doc, native = self.document()
            doc.changes = True
            doc.save_changes(alias.as_uri())
        self.assertTrue(alias.is_symlink())
        self.assertEqual(self.input.read_bytes(), native.saved_bytes)
        self.assertFalse(doc.changes)
        self.assertEqual(set(self.input_dir.iterdir()), {self.input, alias})

    def test_failed_save_as_preserves_existing_destination_and_dirty_state(self):
        destination = self.input_dir / 'destination.pdf'
        destination.write_bytes(b'KEEP SAVE AS DESTINATION\n')
        with working_directory(self.input_dir):
            doc, native = self.document(outcome=False)
            doc.changes = True
            doc.save_changes(destination.as_uri())
        self.assertTrue(doc.changes)
        self.assertEqual(destination.read_bytes(), b'KEEP SAVE AS DESTINATION\n')
        self.assertEqual(self.input.read_bytes(), b'%PDF-inert-original\n')
        self.assertEqual(set(self.input_dir.iterdir()), {self.input, destination})

    def save_failure_case(self, outcome=True, replace_failure=False):
        annotation = object()
        with working_directory(self.input_dir):
            doc, native = self.document(outcome=outcome)
            doc.changes = True
            doc.pages_cache[0] = types.SimpleNamespace(page=native.page, get_annotations=lambda: [annotation])
            before = set(self.input_dir.iterdir())
            replacement = mock.patch.object(Path, 'replace', side_effect=PermissionError('inert replacement failure'))
            with replacement if replace_failure else contextlib.nullcontext():
                try:
                    doc.save_changes()
                except (OSError, RuntimeError):
                    pass
        self.assertTrue(doc.changes, 'A failed save must remain dirty')
        self.assertEqual(native.page.active_annotations, [], 'Temporary annotations must be restored in finally')
        self.assertEqual(set(self.input_dir.iterdir()), before, 'A failed save must not leak its temporary file')
        self.assertEqual(self.input.read_bytes(), b'%PDF-inert-original\n')
        self.assertEqual(native.saved_annotations, [[annotation]])

    def test_false_native_save_keeps_dirty_and_cleans_up(self):
        self.save_failure_case(outcome=False)

    def test_raising_native_save_keeps_dirty_and_restores_annotations(self):
        self.save_failure_case(outcome=RuntimeError('inert native save failure'))

    def test_failed_replace_keeps_dirty_and_restores_annotations(self):
        self.save_failure_case(replace_failure=True)

    def test_successful_save_restores_annotation_state(self):
        annotation = object()
        with working_directory(self.input_dir):
            doc, native = self.document()
            doc.changes = True
            doc.pages_cache[0] = types.SimpleNamespace(page=native.page, get_annotations=lambda: [annotation])
            doc.save_changes()
        self.assertEqual(native.saved_annotations, [[annotation]])
        self.assertEqual(native.page.active_annotations, [])
        self.assertFalse(doc.changes)




    def test_text_annotation_edit_is_retained_in_native_save(self):
        class TextAnnotation:
            def __init__(self, rectangle, contents=''):
                self.rectangle = rectangle
                self.contents = contents

            def get_annot_type(inner):
                return self.target.Poppler.AnnotType.TEXT

            def get_rectangle(inner):
                return inner.rectangle

            def get_contents(inner):
                return inner.contents

            def set_contents(inner, value):
                inner.contents = value

            def set_icon(inner, icon):
                pass

        rectangle = types.SimpleNamespace(x1=10, x2=30, y1=10, y2=30)
        original = TextAnnotation(rectangle, 'Original inert note')
        mapping = types.SimpleNamespace(annot=original, area=rectangle)
        with working_directory(self.input_dir):
            doc, native = self.document()
            native.page = NativePage([mapping])
            native.page.active_annotations.append(original)
            page = self.target.Page(native.page, 0, doc)
            doc.pages_cache[0] = page
            self.assertEqual(native.page.active_annotations, [])
            factory = types.SimpleNamespace(new=lambda native_document, rect: TextAnnotation(rect))
            with mock.patch.object(self.target.Poppler, 'AnnotText', factory, create=True), \
                    mock.patch.object(self.target.Poppler, 'ANNOT_TEXT_ICON_NOTE', 'note', create=True):
                page.set_annotation(0, 'Edited inert note')
            self.assertTrue(doc.has_changes())
            doc.save_changes()
        self.assertEqual([annotation.get_contents()
                         for annotation in native.saved_annotations[0]], ['Edited inert note'])
        self.assertEqual([annotation.get_contents() for annotation in page.get_annotations()], ['Edited inert note'])
        self.assertEqual(native.page.active_annotations, [])
        self.assertFalse(doc.has_changes())
        self.assert_no_external_handler()







class UISaveTests(unittest.TestCase):
    ui_class = None
    gtk = None

    def setUp(self):
        self.gtk.dialogs.clear()
        self.gtk.responses.clear()

    def ui(self, changed=True, save_result=True, save_error=None):
        ui = self.ui_class()
        ui.p_win = object()
        ui.doc = types.SimpleNamespace(has_changes=lambda: changed,
                                       save_changes=mock.Mock(return_value=save_result, side_effect=save_error))
        return ui

    def answer(self, ui, response, reload=False):
        self.gtk.responses.append(response)
        try:
            result = ui.unsaved_changes(reload=reload)
        except (OSError, ValueError, FakeGLibError) as error:
            self.fail('Save error escaped the UI and lost its keep-open decision: ' + str(error))
        self.assertTrue(all(dialog.destroyed for dialog in self.gtk.dialogs))
        return result

    def test_unchanged_document_needs_no_prompt(self):
        ui = self.ui(changed=False)
        self.assertFalse(ui.unsaved_changes())
        self.assertEqual(self.gtk.dialogs, [])
        ui.doc.save_changes.assert_not_called()

    def test_close_yes_save_success_can_continue(self):
        ui = self.ui()
        self.assertFalse(self.answer(ui, self.gtk.ResponseType.YES))
        ui.doc.save_changes.assert_called_once()

    def test_close_yes_save_false_keeps_document_open(self):
        ui = self.ui(save_result=False)
        self.assertTrue(self.answer(ui, self.gtk.ResponseType.YES))
        self.assertTrue(any(dialog.options.get('message_type') == self.gtk.MessageType.ERROR
                            for dialog in self.gtk.dialogs))

    def test_close_yes_save_errors_keep_document_open_and_report_error(self):
        for error in [OSError('inert write failure'), ValueError('inert unsupported target'),
                      FakeGLibError('inert native failure')]:
            with self.subTest(error=type(error).__name__):
                self.gtk.dialogs.clear()
                ui = self.ui(save_error=error)
                self.assertTrue(self.answer(ui, self.gtk.ResponseType.YES))
                self.assertTrue(any(dialog.options.get('message_type') == self.gtk.MessageType.ERROR
                                    for dialog in self.gtk.dialogs))

    def test_reload_yes_success_cancels_reload_after_saving(self):
        ui = self.ui()
        self.assertTrue(self.answer(ui, self.gtk.ResponseType.YES, reload=True))
        ui.doc.save_changes.assert_called_once()

    def test_reload_yes_save_failure_keeps_document_open(self):
        ui = self.ui(save_result=False)
        self.assertTrue(self.answer(ui, self.gtk.ResponseType.YES, reload=True))

    def test_discard_can_continue_without_saving(self):
        for reload in [False, True]:
            with self.subTest(reload=reload):
                ui = self.ui()
                self.assertFalse(self.answer(ui, self.gtk.ResponseType.NO, reload=reload))
                ui.doc.save_changes.assert_not_called()

    def test_cancel_keeps_document_open_without_saving(self):
        ui = self.ui()
        self.assertTrue(self.answer(ui, self.gtk.ResponseType.CANCEL))
        ui.doc.save_changes.assert_not_called()


def setUpModule():
    HardeningTests.target, path = load_document(SOURCE)
    UISaveTests.ui_class, UISaveTests.gtk = load_ui_save_methods(path.with_name('ui.py'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
