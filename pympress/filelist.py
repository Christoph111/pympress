"""An ordered, local-only presentation file list, independent of GTK.

Lists contain references to files. Opening a list never extracts or executes
anything and missing files remain visible so that users can repair the list.
"""

import collections
import json
import os
import pathlib
import tempfile
import types
import uuid
from collections.abc import Mapping
from urllib.parse import urlsplit
from urllib.request import url2pathname


IMAGE_SUFFIXES = frozenset({'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.gif'})
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | {'.pdf'}
MAX_ENTRIES = 1000
MAX_LIST_BYTES = 2 * 1024 * 1024
LIST_FORMAT = 'pympress-file-list'
DEFAULT_SLIDE_SECONDS = 5.0
MAX_PAGE_OVERRIDES = 10000
MAX_PAGE_INDEX = 1000000
_UNCHANGED = object()


class FileListError(ValueError):
    """An invalid file or list that can be reported directly to the user."""


def validate_slide_seconds(value):
    """Validate a finite slide duration and return it as floating-point seconds.

    Args:
        value (int or float): Display time between 0.1 seconds and one day.

    Raises:
        FileListError: The value is not a number within the allowed range.
    """
    if type(value) not in (int, float) or not 0.1 <= value <= 86400:
        raise FileListError(_('The slide duration must be between 0.1 and 86,400 seconds.'))
    return float(value)


def _page_seconds(value):
    if not isinstance(value, Mapping) or len(value) > MAX_PAGE_OVERRIDES:
        raise FileListError(_('The per-page timing settings are invalid or too large.'))
    result = {}
    for page, seconds in value.items():
        if type(page) is not int or not 0 <= page <= MAX_PAGE_INDEX:
            raise FileListError(_('Page timing indices must be non-negative whole numbers.'))
        result[page] = validate_slide_seconds(seconds)
    return types.MappingProxyType(result)


def _validate_continuation(value):
    if value is not None and type(value) is not bool:
        raise FileListError(_('The continuation setting must be enabled, disabled, or inherited.'))
    return value


def _reject_json_constant(value):
    raise FileListError(_('Non-finite numbers are not permitted in a file list.'))


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FileListError(_('The file list contains a duplicate JSON field.'))
        result[key] = value
    return result


def _native_absolute_path(value, path_type=pathlib.Path):
    """Recognize native drive paths before treating a colon as a URI scheme.

    ``path_type`` also allows portable tests with ``PureWindowsPath``. UNC and
    Windows device paths remain outside the local-file trust boundary.
    """
    path = path_type(value)
    if str(path).startswith(('\\\\', '//')) or path.drive.startswith(('\\\\', '//')):
        raise FileListError(_('Only local files can be added to a presentation.'))
    return path if path.is_absolute() else None


def local_path(value, base=None, must_exist=True):
    """Resolve a local filename or ``file:`` URI, excluding remote handlers.

    ``base`` applies only to relative filenames, for relocatable saved lists.
    Percent escapes are decoded only for URIs, never for ordinary filenames.
    """
    if not isinstance(value, (str, pathlib.Path)):
        raise FileListError(_('A file path must be text.'))
    text = str(value)
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise FileListError(_('The file path is empty or contains control characters.'))
    try:
        native = _native_absolute_path(text)
        if isinstance(value, pathlib.Path):
            path = value
        elif native is not None:
            path = native
        else:
            parts = urlsplit(text)
            if parts.scheme:
                if (parts.scheme.lower() != 'file' or parts.netloc.lower() not in {'', 'localhost'} or
                        not parts.path or parts.path.startswith('//') or parts.query or parts.fragment):
                    raise FileListError(_('Only local files can be added to a presentation.'))
                path = pathlib.Path(url2pathname(parts.path))
                if _native_absolute_path(path) is None:
                    raise FileListError(_('A file URI must contain an absolute path.'))
            else:
                path = pathlib.Path(text)
        if any(ord(char) < 32 or ord(char) == 127 for char in str(path)):
            raise FileListError(_('The file path contains control characters.'))
        if not path.is_absolute() and base is not None:
            path = pathlib.Path(base) / path
        path = pathlib.Path(os.path.realpath(str(path)))
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise FileListError(_('Unsupported file type: {}').format(path.name))
        if (must_exist and not path.is_file()) or (path.exists() and not path.is_file()):
            raise FileListError(_('File not found or not a regular file: {}').format(path))
        return path
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, FileListError):
            raise
        raise FileListError(_('Cannot use this file path: {}').format(error)) from error


def decode_drop(data, uri_list=False):
    """Decode multiple local files from a GTK URI-list or newline-separated text."""
    if not isinstance(data, str):
        raise FileListError(_('The dropped data is not a list of filenames.'))
    if any((ord(char) < 32 and char not in '\r\n') or ord(char) == 127 for char in data):
        raise FileListError(_('The dropped filenames contain control characters.'))
    try:
        byte_count = len(data.encode('utf-8'))
    except UnicodeError as error:
        raise FileListError(_('The dropped filenames contain invalid text.')) from error
    if byte_count > MAX_LIST_BYTES:
        raise FileListError(_('The dropped file list is too large.'))
    lines = data.replace('\r\n', '\n').split('\n')
    if len(lines) > MAX_ENTRIES * 2:
        raise FileListError(_('A presentation can contain at most {} files.').format(MAX_ENTRIES))
    uri_mode = uri_list or all(
        not line.strip() or line.lstrip().startswith('#') or line.strip().lower().startswith('file:')
        for line in lines
    )
    paths = []
    for line in lines:
        line = line.strip()
        if not line or (line.startswith('#') and uri_mode):
            continue
        if uri_mode and not line.lower().startswith('file:'):
            raise FileListError(_('Only local file URIs can be dropped here.'))
        paths.append(local_path(line))
        if len(paths) > MAX_ENTRIES:
            raise FileListError(_('A presentation can contain at most {} files.').format(MAX_ENTRIES))
    return paths


_FileEntryBase = collections.namedtuple(
    'FileEntry', 'id path slide_seconds page_seconds transition_enabled continue_after')


class FileEntry(_FileEntryBase):
    """Immutable validated settings without requiring the dataclasses module."""
    __slots__ = ()

    def __new__(cls, id, path, slide_seconds=DEFAULT_SLIDE_SECONDS, page_seconds=None, transition_enabled=True,
                continue_after=None):
        """Create an entry with validated, immutable slide timing settings."""
        slide_seconds = validate_slide_seconds(slide_seconds)
        page_seconds = _page_seconds({} if page_seconds is None else page_seconds)
        if type(transition_enabled) is not bool:
            raise FileListError(_('The transition setting must be enabled or disabled.'))
        continue_after = _validate_continuation(continue_after)
        return super(FileEntry, cls).__new__(
            cls, id, path, slide_seconds, page_seconds, transition_enabled, continue_after)

    def with_settings(self, slide_seconds=None, page_seconds=None, transition_enabled=None, continue_after=_UNCHANGED):
        """Return a validated replacement while retaining the path and identity.

        Args:
            slide_seconds (float or None): New default duration, or keep it unchanged.
            page_seconds (Mapping or None): Zero-based overrides, or keep them unchanged.
            transition_enabled (bool or None): New transition flag, or keep it unchanged.
            continue_after (bool or None): Override continuation, or None to inherit; omitted keeps it unchanged.
        """
        return type(self)(self.id, self.path,
                          self.slide_seconds if slide_seconds is None else slide_seconds,
                          self.page_seconds if page_seconds is None else page_seconds,
                          self.transition_enabled if transition_enabled is None else transition_enabled,
                          self.continue_after if continue_after is _UNCHANGED else continue_after)

    def _replace(self, **settings):
        values = self._asdict()
        if set(settings) - set(self._fields):
            raise FileListError(_('The file entry contains an unknown setting.'))
        values.update(settings)
        return type(self)(**values)

    @classmethod
    def _make(cls, values):
        return cls(*values)

    def __hash__(self):
        """Hash all immutable settings, including per-page overrides."""
        return hash((self.id, self.path, self.slide_seconds,
                     tuple(sorted(self.page_seconds.items())), self.transition_enabled, self.continue_after))

    def duration_for_page(self, page):
        """Return a zero-based page's override or the file's default duration."""
        if type(page) is not int or page < 0:
            raise FileListError(_('The page number is invalid.'))
        return self.page_seconds.get(page, self.slide_seconds)

    @property
    def uri(self):
        """Return the presentation file's absolute local URI."""
        return self.path.as_uri()

    @property
    def name(self):
        """Return the presentation filename without its parent directory."""
        return self.path.name

    @property
    def is_image(self):
        """Return whether the entry names a supported raster image."""
        return self.path.suffix.lower() in IMAGE_SUFFIXES

    @property
    def exists(self):
        """Return whether the reference currently points to a regular file."""
        try:
            return self.path.is_file()
        except OSError:
            return False


class FileList:
    """Stable entry identities allow duplicate paths and safe reordering."""

    def __init__(self):
        """Create an empty list with conservative playback defaults."""
        self._entries = []
        self._current_index = -1
        self._transition_style = 'cut'
        self._transition_seconds = 0.5
        self._playback_mode = 'current'
        self._playback_loop = False
        self._continue_by_default = True

    @property
    def transition_style(self):
        """Return the global transition style, either cut or fade."""
        return self._transition_style

    @property
    def transition_seconds(self):
        """Return the configured crossfade duration in seconds."""
        return self._transition_seconds

    @property
    def playback_mode(self):
        """Return whether playback covers the current file or the whole list."""
        return self._playback_mode

    @property
    def playback_loop(self):
        """Return whether playback restarts after reaching its scope's end."""
        return self._playback_loop

    @property
    def continue_by_default(self):
        """Return the continuation policy for entries without an override."""
        return self._continue_by_default

    def set_continuation(self, default_bool):
        """Validate and update whether files normally continue to the next file."""
        if type(default_bool) is not bool:
            raise FileListError(_('The default continuation setting must be enabled or disabled.'))
        self._continue_by_default = default_bool

    def can_advance(self, entry=None):
        """Return an entry's continuation policy, using the active entry if omitted.

        An empty selection cannot advance. This policy does not indicate whether
        the list contains a following entry.
        """
        if entry is None:
            entry = self.current
        if entry is None:
            return False
        if not isinstance(entry, FileEntry):
            raise FileListError(_('The file entry is invalid.'))
        return self._continue_by_default if entry.continue_after is None else entry.continue_after

    def set_entry_continuation(self, indices, value):
        """Atomically set selected entries' overrides, using None to inherit.

        Args:
            indices (iterable): Positions to update; repeated positions update once.
            value (bool or None): Continue, stop, or inherit the global default.

        Returns:
            tuple: Updated entries in the first occurrence order of their indices.

        Raises:
            FileListError: A setting or position is invalid, or the batch is too large.
        """
        _validate_continuation(value)
        try:
            iterator = iter(indices)
        except TypeError as error:
            raise FileListError(_('No file is selected.')) from error
        positions = []
        seen = set()
        for count, index in enumerate(iterator):
            if count >= MAX_ENTRIES:
                raise FileListError(_('A presentation can contain at most {} files.').format(MAX_ENTRIES))
            self._check_index(index)
            if index not in seen:
                positions.append(index)
                seen.add(index)
        updated = tuple(self._entries[index].with_settings(continue_after=value) for index in positions)
        for index, entry in zip(positions, updated):
            self._entries[index] = entry
        return updated

    def set_transition(self, style, seconds):
        """Validate and update the global cut/fade settings atomically."""
        if style not in ('cut', 'fade'):
            raise FileListError(_('The transition must be cut or fade.'))
        if type(seconds) not in (int, float) or not 0 <= seconds <= 5:
            raise FileListError(_('The transition duration must be between 0 and 5 seconds.'))
        self._transition_style = style
        self._transition_seconds = float(seconds)

    def set_playback(self, mode, loop):
        """Validate and update slideshow scope and looping atomically."""
        if mode not in ('current', 'list') or type(loop) is not bool:
            raise FileListError(_('The slideshow mode or loop setting is invalid.'))
        self._playback_mode = mode
        self._playback_loop = loop

    @property
    def entries(self):
        """Return an immutable snapshot of the ordered entries."""
        return tuple(self._entries)

    @property
    def current_index(self):
        """Return the active position, or -1 when no entry is active."""
        return self._current_index

    @property
    def current(self):
        """Return the active entry, or None when no entry is active."""
        return self._entries[self._current_index] if self._current_index >= 0 else None

    def __len__(self):
        """Return the number of entries, including missing file references."""
        return len(self._entries)

    def append(self, paths):
        """Append a complete validated batch, allowing deliberate duplicates.

        Args:
            paths (iterable or str or pathlib.Path): Local filenames or file URIs.

        Returns:
            list: Newly created entries, each with an independent identity.

        Raises:
            FileListError: A reference is invalid or the list would exceed its limit.
        """
        if isinstance(paths, (str, pathlib.Path)):
            paths = [paths]
        # Validate the complete addition before changing the existing list.
        additions = []
        for path in paths:
            if len(self._entries) + len(additions) >= MAX_ENTRIES:
                raise FileListError(_('A presentation can contain at most {} files.').format(MAX_ENTRIES))
            additions.append(FileEntry(uuid.uuid4().hex, local_path(path)))
        self._entries.extend(additions)
        return additions

    def update_settings(self, index, slide_seconds=None, page_seconds=None, transition_enabled=None,
                        continue_after=_UNCHANGED):
        """Replace timing settings while retaining this entry's stable identity."""
        self._check_index(index)
        settings = {}
        if slide_seconds is not None:
            settings['slide_seconds'] = slide_seconds
        if page_seconds is not None:
            settings['page_seconds'] = page_seconds
        if transition_enabled is not None:
            settings['transition_enabled'] = transition_enabled
        if continue_after is not _UNCHANGED:
            settings['continue_after'] = continue_after
        updated = self._entries[index].with_settings(**settings)
        self._entries[index] = updated
        return updated

    def _check_index(self, index):
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(self._entries):
            raise FileListError(_('No file is selected.'))

    def set_current(self, index):
        """Select an existing position, or pass -1 to clear the selection."""
        if type(index) is int and index == -1:
            self._current_index = -1
            return
        self._check_index(index)
        self._current_index = index

    def remove(self, index):
        """Remove and return an entry without deleting its presentation file.

        Removing the active entry clears the selection; other removals preserve
        the active entry's identity at its new position.
        """
        self._check_index(index)
        removed = self._entries.pop(index)
        if index == self._current_index:
            self._current_index = -1
        elif index < self._current_index:
            self._current_index -= 1
        return removed

    def move(self, index, new_index):
        """Move an entry to another position while retaining active identity."""
        self._check_index(index)
        self._check_index(new_index)
        current = self.current
        self._entries.insert(new_index, self._entries.pop(index))
        if current is not None:
            self._current_index = self._entries.index(current)

    def clear(self):
        """Forget all entries and selection without changing any source file."""
        self._entries.clear()
        self._current_index = -1

    def save(self, path):
        """Atomically write a versioned JSON list using relative local filenames."""
        _native_absolute_path(path)
        target = pathlib.Path(os.path.realpath(str(path)))
        if any(target == entry.path for entry in self._entries):
            raise FileListError(_('The file list must not overwrite a presentation file. Choose a JSON filename.'))
        entries = []
        for entry in self._entries:
            try:
                reference = pathlib.Path(os.path.relpath(str(entry.path), str(target.parent))).as_posix()
                # A legal native filename such as "Talk: Intro.pdf" otherwise
                # resembles a URI scheme when the list is loaded again.
                if urlsplit(reference).scheme:
                    reference = './' + reference
            except ValueError:
                # Windows files on another drive need an absolute local URI.
                reference = entry.path.as_uri()
            page_seconds = {str(page): seconds for page, seconds in sorted(entry.page_seconds.items())}
            entries.append(dict(id=entry.id, path=reference, slide_seconds=entry.slide_seconds,
                                page_seconds=page_seconds,
                                transition_enabled=entry.transition_enabled, continue_after=entry.continue_after))
        document = {
            'format': LIST_FORMAT,
            'version': 3,
            'current': self._current_index,
            'entries': entries,
            'transition_style': self._transition_style,
            'transition_seconds': self._transition_seconds,
            'playback_mode': self._playback_mode,
            'playback_loop': self._playback_loop,
            'continue_by_default': self._continue_by_default,
        }
        payload = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        if len(payload) > MAX_LIST_BYTES:
            raise FileListError(_('The file list is too large to save.'))
        temp_path = None
        try:
            fd, name = tempfile.mkstemp(prefix='.pympress-list-', suffix='.json', dir=str(target.parent))
            temp_path = pathlib.Path(name)
            with os.fdopen(fd, 'wb') as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            temp_path.replace(str(target))
        except OSError as error:
            raise FileListError(_('Could not save the file list: {}').format(error)) from error
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

    @classmethod
    def load(cls, path):
        """Read a bounded list; missing files retain their position and identity."""
        _native_absolute_path(path)
        source = pathlib.Path(os.path.realpath(str(path)))
        try:
            if not source.is_file():
                raise FileListError(_('The file list was not found or is not a regular file.'))
            with source.open('rb') as input_file:
                payload = input_file.read(MAX_LIST_BYTES + 1)
            if len(payload) > MAX_LIST_BYTES:
                raise FileListError(_('The file list is too large.'))
            document = json.loads(payload.decode('utf-8'), parse_constant=_reject_json_constant,
                                  object_pairs_hook=_unique_json_object)
            version = document.get('version') if isinstance(document, dict) else None
            expected = {'format', 'version', 'current', 'entries'}
            if type(version) is int and version in (2, 3):
                expected |= {'transition_style', 'transition_seconds', 'playback_mode', 'playback_loop'}
            if type(version) is int and version == 3:
                expected.add('continue_by_default')
            if (not isinstance(document, dict) or
                    set(document) != expected or
                    document['format'] != LIST_FORMAT or
                    type(version) is not int or version not in (1, 2, 3) or
                    not isinstance(document['entries'], list) or
                    len(document['entries']) > MAX_ENTRIES):
                raise FileListError(_('This is not a supported Pympress file list.'))
            result = cls()
            if version in (2, 3):
                result.set_transition(document['transition_style'], document['transition_seconds'])
                result.set_playback(document['playback_mode'], document['playback_loop'])
            if version == 3:
                result.set_continuation(document['continue_by_default'])
            ids = set()
            for entry in document['entries']:
                entry_keys = {'id', 'path'} if version == 1 else {
                    'id', 'path', 'slide_seconds', 'page_seconds', 'transition_enabled'}
                if version == 3:
                    entry_keys.add('continue_after')
                if (not isinstance(entry, dict) or set(entry) != entry_keys or
                        not isinstance(entry['id'], str) or len(entry['id']) != 32 or
                        any(char not in '0123456789abcdef' for char in entry['id']) or
                        entry['id'] in ids):
                    raise FileListError(_('The file list contains an invalid entry.'))
                ids.add(entry['id'])
                settings = {}
                if version in (2, 3):
                    raw_page_seconds = entry['page_seconds']
                    if not isinstance(raw_page_seconds, dict) or len(raw_page_seconds) > MAX_PAGE_OVERRIDES:
                        raise FileListError(_('The per-page timing settings are invalid or too large.'))
                    pages = {}
                    for key, seconds in raw_page_seconds.items():
                        if (not key or len(key) > 7 or any(ord(char) > 127 for char in key) or not key.isdecimal() or
                                str(int(key)) != key):
                            raise FileListError(_('The file list contains an invalid page timing index.'))
                        pages[int(key)] = seconds
                    settings = dict(slide_seconds=entry['slide_seconds'], page_seconds=pages,
                                    transition_enabled=entry['transition_enabled'])
                if version == 3:
                    settings['continue_after'] = entry['continue_after']
                result._entries.append(FileEntry(entry['id'], local_path(entry['path'], source.parent, False),
                                                 **settings))
            current = document['current']
            if type(current) is not int or current < -1 or current >= len(result):
                raise FileListError(_('The file list contains an invalid selection.'))
            result.set_current(current)
            return result
        except (OSError, UnicodeError, ValueError, RuntimeError) as error:
            if isinstance(error, FileListError):
                raise
            raise FileListError(_('Could not read the file list: {}').format(error)) from error
