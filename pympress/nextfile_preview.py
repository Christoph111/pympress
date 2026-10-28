"""Render one following local file without activating a presentation document.

Only Poppler's passive page rendering is used. This preview never constructs a
Pympress document, inspects actions or attachments, or changes presentation state.
Native bindings are loaded lazily, and no additional raster surfaces are retained.
"""

import logging
import math

from pympress.filelist import IMAGE_SUFFIXES, local_path
from pympress.image_document import prepare_image
from pympress.transitions import fit_page
from pympress import pdf_source


logger = logging.getLogger(__name__)


def _load_document(uri):
    """Open a local PDF without a password prompt or any URI dispatcher."""
    import gi
    gi.require_version('Poppler', '0.18')
    from gi.repository import Poppler
    return Poppler.Document.new_from_file(uri, None)


def _region(value):
    """Validate a normalized page crop without depending on notes-mode state."""
    if value is None:
        return (0.0, 0.0, 1.0, 1.0)
    if len(value) != 4 or any(type(part) not in (int, float) or not math.isfinite(part) for part in value):
        raise ValueError('Invalid preview page region.')
    x, y, width, height = value
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise ValueError('Invalid preview page region.')
    return tuple(value)


class NextFilePreview:
    """Own at most one passive preview document and its temporary image owner.

    Failed opens are cached until the selected entry or its source stat changes.
    Callers can draw several pages of this single document, but opening a different
    entry always releases the previous document before creating its replacement.
    """

    def __init__(self, loader=None, image_preparer=None, snapshot_preparer=None):
        self._loader = _load_document if loader is None else loader
        self._image_preparer = prepare_image if image_preparer is None else image_preparer
        self._snapshot_preparer = pdf_source.prepare if snapshot_preparer is None else snapshot_preparer
        self._document = None
        self._snapshot = None
        self._image = None
        self._key = None
        self.page_count = 0
        self.error = None

    def _release(self):
        """Release Poppler before removing a converted image's private PDF."""
        self._document = None
        self.page_count = 0
        snapshot, self._snapshot = self._snapshot, None
        if snapshot is not None:
            try:
                snapshot.cleanup()
            except Exception:
                logger.warning('Could not clean up a next-file PDF snapshot', exc_info=True)
        image, self._image = self._image, None
        if image is not None:
            try:
                image.cleanup()
            except Exception:
                logger.warning('Could not clean up a next-file image preview', exc_info=True)

    def _failed(self):
        """Discard incomplete resources while retaining the failed cache key."""
        self._release()
        self.error = _('The next file cannot be previewed.')

    def get(self, entry):
        """Return this provider after selecting a validated, stat-keyed entry.

        Args:
            entry (FileEntry): The following local PDF or image, or None to clear.

        Returns:
            NextFilePreview: This provider, with zero pages and an error on failure.
        """
        if entry is None:
            self.cleanup()
            return self
        try:
            path = local_path(entry.path, must_exist=True)
            stat = path.stat()
            key = (entry.id, str(path), stat.st_dev, stat.st_ino, stat.st_size,
                   stat.st_mtime_ns, stat.st_ctime_ns)
        except Exception:
            # Validate again on future calls so a repaired or reappearing file is
            # detected, but never call a native parser for this unavailable key.
            key = (getattr(entry, 'id', None), str(getattr(entry, 'path', '')), 'unavailable')
            if key != self._key:
                self._release()
                self._key = key
                self._failed()
            return self
        if key == self._key:
            return self
        self._release()
        self._key = key
        self.error = None
        try:
            if path.suffix.lower() in IMAGE_SUFFIXES:
                self._image = self._image_preparer(path)
            uri = path.as_uri() if self._image is None else self._image.uri
            self._snapshot = self._snapshot_preparer(uri)
            self._document = self._loader(uri if self._snapshot is None else self._snapshot.uri)
            self.page_count = self._document.get_n_pages()
            if type(self.page_count) is not int or self.page_count < 1:
                raise ValueError('The preview document has no pages.')
        except Exception:
            logger.debug('Could not open the next-file preview', exc_info=True)
        else:
            return self
        self._failed()
        return self

    def _page(self, number, region):
        """Get one passive native page and its validated crop dimensions."""
        if self._document is None or type(number) is not int or not 0 <= number < self.page_count:
            return None
        page = self._document.get_page(number)
        width, height = page.get_size()
        if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
            raise ValueError('The preview page has invalid dimensions.')
        x, y, crop_width, crop_height = _region(region)
        crop = (width * x, height * y, width * crop_width, height * crop_height)
        if crop[2] <= 0 or crop[3] <= 0 or not math.isfinite(crop[2] / crop[3]) or crop[2] / crop[3] <= 0:
            raise ValueError('The preview crop has invalid dimensions.')
        return page, crop

    def aspect(self, page, region=None):
        """Return a page crop's aspect, or a neutral aspect when unavailable."""
        try:
            result = self._page(page, region)
            return 1.0 if result is None else result[1][2] / result[1][3]
        except Exception:
            logger.debug('Could not measure the next-file preview', exc_info=True)
        # Leave the exception handler first, releasing any failed _page() frame
        # and native page reference before deleting an image's temporary PDF.
        self._failed()
        return 1.0

    def draw(self, context, page, width, height, region=None):
        """Fit and clip a passive page crop into an opaque black viewport.

        Args:
            context (cairo.Context): Existing caller-owned rendering target.
            page (int): Zero-based page in the preview document.
            width (float): Available viewport width.
            height (float): Available viewport height.
            region (tuple or None): Normalized x, y, width and height, or the full page.

        Returns:
            bool: Whether the requested page was rendered successfully.
        """
        saved = False
        succeeded = False
        result = native_page = None
        try:
            if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
                return False
            context.save()
            saved = True
            context.set_source_rgb(0, 0, 0)
            context.rectangle(0, 0, width, height)
            context.fill()
            result = self._page(page, region)
            if result is None:
                return False
            native_page, (crop_x, crop_y, crop_width, crop_height) = result
            x, y, slide_width, slide_height = fit_page(width, height, crop_width / crop_height)
            context.rectangle(x, y, slide_width, slide_height)
            context.clip()
            context.set_source_rgb(1, 1, 1)
            context.paint()
            context.translate(x, y)
            context.scale(slide_width / crop_width, slide_height / crop_height)
            context.translate(-crop_x, -crop_y)
            native_page.render(context)
            succeeded = True
        except Exception:
            logger.debug('Could not render the next-file preview', exc_info=True)
        finally:
            if saved:
                try:
                    context.restore()
                except Exception:
                    logger.debug('Could not restore the preview drawing context', exc_info=True)
        if not succeeded:
            result = native_page = None
            self._failed()
        return succeeded

    def cleanup(self):
        """Release the preview and clear its cached source identity and error."""
        self._release()
        self._key = None
        self.error = None
