#!/usr/bin/env python3
"""Update or validate gettext catalogs without contacting a translation service.

Requires Babel (already part of the project's build dependencies). By default,
only checks files. --update refreshes the POT and appends missing PO messages,
preserving existing catalog bytes and translations. --compile-dir writes MO
files into an explicitly selected output directory. Python and GtkBuilder XML
use the same message markers as share/locale/babel_mapping.cfg.
"""

import argparse
import io
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_file
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po


ROOT = Path(__file__).resolve().parents[1]
LOCALES = Path('pympress/share/locale')


def extract_catalog(root):
    """Extract static Python gettext calls and translatable GtkBuilder text."""
    catalog = Catalog(domain='pympress', project='pympress', charset='utf-8')
    for path in sorted((root / 'pympress').rglob('*.py')):
        extracted = extract_from_file('python', str(path), comment_tags=('Translators:',))
        for line, message, comments, context in extracted:
            catalog.add(message, locations=[(str(path.relative_to(root)), line)],
                        auto_comments=comments, context=context)
    for pattern in ('*.xml', '*.glade'):
        for path in sorted((root / 'pympress/share/xml').glob(pattern)):
            for element in ET.parse(str(path)).getroot().iter():
                if element.get('translatable', '').lower() not in ('yes', 'true', '1'):
                    continue
                text = ''.join(element.itertext())
                if text.strip():
                    catalog.add(text, locations=[(str(path.relative_to(root)), 0)],
                                context=element.get('context'))
    return catalog


def message_ids(catalog):
    """Include plural forms and contexts while excluding the metadata header."""
    return {(message.id, message.context) for message in catalog if message.id}


def render_po(catalog, template=False):
    """Serialize sorted entries without locations, optionally omitting the header."""
    output = io.BytesIO()
    write_po(output, catalog, width=0, no_location=True, omit_header=template, sort_output=True)
    return output.getvalue()


def append_missing_messages(path, catalog, template):
    """Append untranslated source entries without rewriting existing PO data.

    Locale-specific plural counts come from Babel's parsed catalog. Existing
    messages, headers, translator comments, obsolete entries and formatting stay
    byte-for-byte intact. A changed plural shape for an existing gettext key
    requires an explicit translator edit rather than a duplicate or fuzzy guess.
    """
    existing = message_ids(catalog)
    additions = Catalog(locale=catalog.locale, domain='pympress', charset=catalog.charset)
    for message in template:
        if not message.id or (message.id, message.context) in existing:
            continue
        if catalog.get(message.id, context=message.context) is not None:
            raise ValueError('Existing message has a different plural form: {!r}. '
                             'Update that entry explicitly.'.format(message.id))
        empty = ('',) * catalog.num_plurals if isinstance(message.id, tuple) else ''
        additions.add(message.id, string=empty, context=message.context,
                      flags=message.flags - {'fuzzy'}, auto_comments=message.auto_comments)
    if not len(additions):
        return
    original = path.read_bytes()
    separator = b'' if original.endswith(b'\n\n') else b'\n' if original.endswith(b'\n') else b'\n\n'
    appended = render_po(additions, template=True).lstrip(b'\n')
    with path.open('ab') as stream:
        stream.write(separator + appended)


def main(argv=None):
    """Validate source coverage and formats, optionally updating or compiling catalogs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--update', action='store_true',
                        help='Refresh POT and append missing PO entries without rewriting existing data')
    parser.add_argument('--compile-dir', type=Path, help='Write compiled catalogs outside the source tree')
    args = parser.parse_args(argv)
    root = args.source.resolve()
    template = extract_catalog(root)
    template_path = root / LOCALES / 'pympress.pot'
    errors = []
    if args.update:
        rendered = render_po(template, template=True)
        if not template_path.is_file() or template_path.read_bytes() != rendered:
            template_path.write_bytes(rendered)
    with template_path.open('rb') as stream:
        stored_template = read_po(stream)
    missing = message_ids(template) - message_ids(stored_template)
    extra = message_ids(stored_template) - message_ids(template)
    if missing or extra:
        errors.append('POT is out of date: {} missing and {} obsolete message IDs. Run --update.'.format(
            len(missing), len(extra)))
    catalogs = sorted((root / LOCALES).glob('*/LC_MESSAGES/pympress.po'))
    for path in catalogs:
        language = path.parent.parent.name
        with path.open('rb') as stream:
            catalog = read_po(stream, locale=language)
        if args.update:
            try:
                append_missing_messages(path, catalog, template)
            except ValueError as error:
                errors.append('{}: {}'.format(language, error))
            # Reload serialized empty translations so Babel validates the same
            # strings and locale-specific plural forms that translators edit.
            with path.open('rb') as stream:
                catalog = read_po(stream, locale=language)
        missing = message_ids(template) - message_ids(catalog)
        if missing:
            errors.append('{} is missing {} message IDs.'.format(language, len(missing)))
        for message, failures in catalog.check():
            for failure in failures:
                errors.append('{}: {!r}: {}'.format(language, message.id, failure))
        if args.compile_dir is not None:
            destination = args.compile_dir / language / 'LC_MESSAGES/pympress.mo'
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('wb') as stream:
                write_mo(stream, catalog, use_fuzzy=False)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    plurals = sum(isinstance(message.id, tuple) for message in template)
    print('Validated {} source messages ({} plural messages) across {} language catalogs.'.format(
        len(template), plurals, len(catalogs)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
