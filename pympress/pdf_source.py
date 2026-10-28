"""Own private Windows PDF copies without holding the source file open.

Poppler's Windows file handles prevent replacing or deleting an opened PDF.
Copying in bounded chunks keeps source saves atomic without loading entire PDFs
into memory. Callers must release their native documents before cleanup.
"""

import os
import pathlib
import stat
import tempfile

from pympress.filelist import local_path


COPY_CHUNK_BYTES = 1024 * 1024


class PdfSnapshot:
    """Own one private PDF copy until its native document is released."""

    def __init__(self, directory):
        self._directory = directory
        self.path = pathlib.Path(directory.name) / 'document.pdf'

    @property
    def uri(self):
        """Return the local URI passed to the passive native PDF loader."""
        return self.path.as_uri()

    def cleanup(self):
        """Delete only the owned private copy, retaining ownership on failure."""
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None


def prepare(uri, enabled=None):
    """Snapshot a local PDF on Windows, or return None on other platforms.

    Args:
        uri (str or pathlib.Path): The original local PDF filename or URI.
        enabled (bool or None): Override platform selection for native tests.

    Returns:
        PdfSnapshot or None: The private copy's owner, with all copy handles closed.

    Raises:
        OSError: A source read or private-copy write failed.
        ValueError: The reference is invalid, or the source changed while copying.
    """
    if enabled is not None and type(enabled) is not bool:
        raise ValueError(_('The PDF snapshot setting is invalid.'))
    if not (os.name == 'nt' if enabled is None else enabled):
        return None
    source = local_path(uri)
    if source.suffix.lower() != '.pdf':
        raise ValueError(_('Only local PDF files can be copied for rendering.'))
    snapshot = PdfSnapshot(tempfile.TemporaryDirectory(prefix='pympress-pdf-'))
    complete = False
    try:
        with source.open('rb') as input_file:
            before = os.fstat(input_file.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size < 0:
                raise ValueError(_('The PDF source is not a regular file.'))
            remaining = before.st_size
            with snapshot.path.open('wb') as output_file:
                while remaining:
                    block = input_file.read(min(COPY_CHUNK_BYTES, remaining))
                    if not block:
                        raise ValueError(_('The PDF changed while it was being copied.'))
                    output_file.write(block)
                    remaining -= len(block)
            after = os.fstat(input_file.fileno())
            if (input_file.read(1) or before.st_size != after.st_size or
                    before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns):
                raise ValueError(_('The PDF changed while it was being copied.'))
        complete = True
        return snapshot
    finally:
        if not complete:
            snapshot.cleanup()
