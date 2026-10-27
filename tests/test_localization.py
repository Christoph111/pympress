#!/usr/bin/env python3
"""Localization regressions without GTK or catalog-generation dependencies.

Set PYMPRESS_TEST_LOCALE_DIR to a compiled locale tree to additionally check
German translations and English fallback for untranslated messages. Missing
MO entries are valid; source-message coverage belongs to the POT/PO checks.
"""

try:
    from . import _support
except ImportError:
    import _support


import ast
from collections import Counter
import gettext
import importlib.util
import os
from pathlib import Path
import string
import unittest
from unittest import mock

SOURCE = _support.SOURCE


NEW_MODULES = ('filelist.py', 'image_document.py', 'filelist_window.py', 'presenter_tools.py',
               'playback.py', 'playback_window.py', 'transitions.py')


def collect_gettext_messages(paths):
    """Return static gettext msgids, plus locations of dynamic gettext inputs."""
    messages = set()
    dynamic = []
    for path in paths:
        path = Path(path)
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'), filename=str(path))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            if name not in {'_', 'gettext', 'ngettext'}:
                continue
            count = 2 if name == 'ngettext' else 1
            if len(node.args) < count:
                dynamic.append((path.name, node.lineno))
                continue
            for argument in node.args[:count]:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    messages.add(argument.value)
                else:
                    dynamic.append((path.name, node.lineno))
    return messages, dynamic


def collect_plural_messages(paths):
    """Return static singular/plural pairs for checking every compiled form."""
    pairs = set()
    for path in paths:
        for node in ast.walk(ast.parse(Path(path).read_text(encoding='utf-8'))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
                    node.func.id == 'ngettext' and len(node.args) >= 2 and
                    all(isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                        for argument in node.args[:2])):
                pairs.add(tuple(argument.value for argument in node.args[:2]))
    return pairs


def missing_catalog_messages(catalog, messages):
    """Report key presence without requiring translations for every source key.

    An intentional identity translation counts as present. Missing compiled MO
    keys remain valid and use gettext's English fallback.
    """
    keys = {key[0] if isinstance(key, tuple) else key for key in getattr(catalog, '_catalog', {})}
    return sorted(set(messages) - keys)


def format_placeholders(message):
    """Preserve named/positional fields, conversions and numeric format specs."""
    return Counter((field, spec, conversion or '') for _literal, field, spec, conversion
                   in string.Formatter().parse(message) if field is not None)


def callback_fixture():
    """Reuse the real-model/AST callback fixture without running its test suite."""
    path = Path(__file__).with_name('test_filelist_window.py')
    spec = importlib.util.spec_from_file_location('localization_window_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = callback_fixture()

    def setUp(self):
        self.fixture = self.helpers.FileListWindowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.window = self.fixture.window
        self.model = self.fixture.ui.file_list

    def test_english_fallback_is_used_by_actual_file_list_callback(self):
        english = gettext.NullTranslations()
        self.model.set_current(2)
        with mock.patch.dict(self.helpers.namespace, {'_': english.gettext, 'ngettext': english.ngettext}):
            self.window.refresh()
        self.assertEqual(self.window.summary.text, '5 files · Active: 3/5 – C.pdf')
        self.assertEqual(self.window.store[2][self.window.STATUS], 'Now showing')
        self.assertEqual(self.window.store[1][self.window.KIND], 'Image')

    def test_translated_templates_reorder_fields_before_interpolation(self):
        source = '{count} files · Active: {position}/{total} – {name}'
        translated = 'Aktiv {position}/{total}: {name} ({count} Dateien)'
        seen = []

        def translate(message):
            seen.append(message)
            return translated if message == source else message

        def translate_plural(singular, plural, count):
            return translate(singular if count == 1 else plural)
        self.model.set_current(2)
        with mock.patch.dict(self.helpers.namespace, {'_': translate, 'ngettext': translate_plural}):
            self.window.refresh()
        self.assertIn(source, seen)
        self.assertNotIn('5 files · Active: 3/5 – C.pdf', seen)
        self.assertEqual(self.window.summary.text, 'Aktiv 3/5: C.pdf (5 Dateien)')
        self.assertEqual(format_placeholders(source), format_placeholders(translated))

    def test_complete_singular_and_empty_list_templates_are_translatable(self):
        self.model.clear()
        self.model.append(self.fixture.directory / 'A.pdf')
        single = '{count} file · No file from the list is active'
        empty = '{count} files · No file from the list is active'
        messages = []

        def translate(message):
            messages.append(message)
            return {'{count} file · No file from the list is active': 'Eine Datei: {count}; nichts aktiv',
                    '{count} files · No file from the list is active': 'Keine aktive Datei, Anzahl {count}'
                    }.get(message, message)

        def translate_plural(singular, plural, count):
            return translate(singular if count == 1 else plural)
        with mock.patch.dict(self.helpers.namespace, {'_': translate, 'ngettext': translate_plural}):
            self.window.refresh()
            self.assertEqual(self.window.summary.text, 'Eine Datei: 1; nichts aktiv')
            self.model.clear()
            self.window.refresh()
        self.assertIn(single, messages)
        self.assertIn(empty, messages)
        self.assertEqual(self.window.summary.text, 'Keine aktive Datei, Anzahl 0')

    def test_actual_counter_supports_catalogs_with_three_plural_forms(self):
        singular = '{count} file · Active: {position}/{total} – {name}'
        catalog = gettext.GNUTranslations()
        catalog._catalog = {(singular, index): '{}: {{count}} · {{name}} · {{position}}/{{total}}'.format(label)
                            for index, label in enumerate(('ONE', 'FEW', 'MANY'))}
        catalog.plural = lambda count: 0 if count == 1 else 1 if count in (2, 3, 4) else 2
        for count, label in ((1, 'ONE'), (2, 'FEW'), (5, 'MANY')):
            with self.subTest(count=count):
                self.model.clear()
                self.model.append([self.fixture.directory / 'A.pdf'] * count)
                self.model.set_current(0)
                with mock.patch.dict(self.helpers.namespace,
                                     {'_': catalog.gettext, 'ngettext': catalog.ngettext}):
                    self.window.refresh()
                self.assertEqual(self.window.summary.text, '{}: {} · A.pdf · 1/{}'.format(label, count, count))

    def test_new_modules_do_not_translate_already_formatted_messages(self):
        paths = [SOURCE / 'pympress' / name for name in NEW_MODULES]
        messages, dynamic = collect_gettext_messages(paths)
        self.assertEqual(dynamic, [], 'gettext inputs must be complete, static templates')
        self.assertIn('Current slide', messages)
        self.assertIn('{count} files · Active: {position}/{total} – {name}', messages)
        self.assertIn('File list · Pympress', messages)

    def test_catalog_coverage_distinguishes_missing_from_identity_translation(self):
        catalog = gettext.NullTranslations()
        catalog._catalog = {'Status': 'Status', 'File': 'Datei'}
        self.assertEqual(missing_catalog_messages(catalog, {'Status', 'File', 'Image'}), ['Image'])
        self.assertEqual(format_placeholders('{name}: {seconds:.1f} s'),
                         format_placeholders('{seconds:.1f} s: {name}'))

    def test_partial_compiled_catalog_keeps_untranslated_ui_in_english(self):
        catalog = gettext.GNUTranslations()
        catalog._catalog = {'_File': '_Datei', 'Current slide': 'Aktuelle Folie'}
        catalog.plural = lambda count: int(count != 1)
        self.model.set_current(2)
        with mock.patch.dict(self.helpers.namespace, {'_': catalog.gettext, 'ngettext': catalog.ngettext}):
            self.window.refresh()
        self.assertEqual(catalog.gettext('_File'), '_Datei')
        self.assertEqual(catalog.gettext('Image'), 'Image')
        self.assertEqual(self.window.summary.text, '5 files · Active: 3/5 – C.pdf')

    def test_compiled_german_catalog_preserves_placeholders_and_english_fallback(self):
        configured = os.environ.get('PYMPRESS_TEST_LOCALE_DIR')
        locale_dir = Path(configured) if configured else SOURCE / 'pympress/share/locale'
        catalog_path = locale_dir / 'de/LC_MESSAGES/pympress.mo'
        if not catalog_path.is_file():
            self.skipTest('Compile catalogs or set PYMPRESS_TEST_LOCALE_DIR to check German translations')
        with catalog_path.open('rb') as stream:
            catalog = gettext.GNUTranslations(stream)
        messages, dynamic = collect_gettext_messages([SOURCE / 'pympress' / name for name in NEW_MODULES])
        self.assertEqual(dynamic, [])
        known_existing = ('_File', 'Open...', 'Notes', 'Current slide')
        self.assertTrue(any(catalog.gettext(message) and catalog.gettext(message) != message
                            for message in known_existing),
                        'The German catalog must retain an existing German translation')
        missing = set(missing_catalog_messages(catalog, messages))
        for message in messages:
            translated = catalog.gettext(message)
            if message in missing:
                self.assertEqual(translated, message, 'An untranslated message must fall back to English')
            else:
                self.assertTrue(translated, 'Empty PO translations must be omitted when compiling MO files')
            self.assertEqual(format_placeholders(message), format_placeholders(translated),
                             'Translation placeholders changed: {!r}'.format(message))
        paths = [SOURCE / 'pympress' / name for name in NEW_MODULES]
        for singular, plural in collect_plural_messages(paths):
            for count in (0, 1, 2, 5, 21):
                translated = catalog.ngettext(singular, plural, count)
                expected = singular if count == 1 else plural
                key = (singular, catalog.plural(count))
                if key not in catalog._catalog:
                    self.assertEqual(translated, expected)
                self.assertEqual(format_placeholders(expected), format_placeholders(translated))


if __name__ == '__main__':
    unittest.main(verbosity=2)
