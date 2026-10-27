"""Convert a bounded, local raster image into one temporary PDF slide.

The source image is never modified. Keeping the temporary owner alive keeps its
PDF available to Poppler; callers clean it up after replacing the document.
"""

import pathlib
import tempfile

from pympress.filelist import IMAGE_SUFFIXES, local_path


MAX_IMAGE_PIXELS = 40000000
MAX_IMAGE_EDGE = 20000
MAX_IMAGE_BYTES = 128 * 1024 * 1024


class ImageError(ValueError):
    """An unsupported, invalid or oversized image."""


class PreparedImage:
    """Own the temporary PDF and its source-image identity."""
    def __init__(self, source_path, directory):
        self.source_path = source_path
        self._directory = directory
        self.pdf_path = pathlib.Path(directory.name) / 'slide.pdf'

    @property
    def uri(self):
        """Return the local file URI consumed by the PDF renderer."""
        return self.pdf_path.as_uri()

    def cleanup(self):
        """Remove the owned temporary conversion files."""
        self._directory.cleanup()


def _graphics_modules():
    # Import lazily so the file-list model and its tests do not require GTK.
    """Load native image and Cairo bindings only when conversion is needed."""
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import GdkPixbuf, Gdk
    import cairo
    return GdkPixbuf, Gdk, cairo


def _check_dimensions(width, height):
    """Reject invalid or oversized image dimensions before decoding."""
    if (width <= 0 or height <= 0 or width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE or
            width * height > MAX_IMAGE_PIXELS):
        raise ImageError(_('The image is too large (maximum 40 megapixels and 20,000 pixels per edge).'))


def _first_gif_frame(source, destination):
    """Copy just the first GIF image block without decoding animation frames."""
    with source.open('rb') as input_file:
        data = input_file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(_('The image file is too large (maximum 128 MiB).'))
    if len(data) < 13 or data[:6] not in {b'GIF87a', b'GIF89a'}:
        raise ImageError(_('The GIF header is invalid.'))
    position = 13 + (3 * (2 ** ((data[10] & 7) + 1)) if data[10] & 128 else 0)

    def skip_subblocks(offset):
        """Return the offset following a terminated sequence of GIF data blocks."""
        while offset < len(data):
            size = data[offset]
            offset += 1
            if not size:
                return offset
            offset += size
        raise ImageError(_('The GIF is incomplete.'))

    while position < len(data):
        marker = data[position]
        position += 1
        if marker == 0x21:  # Extension label followed by length-prefixed data.
            position = skip_subblocks(position + 1)
        elif marker == 0x2c:
            if position + 9 >= len(data):
                break
            width = int.from_bytes(data[position + 4:position + 6], 'little')
            height = int.from_bytes(data[position + 6:position + 8], 'little')
            _check_dimensions(width, height)
            flags = data[position + 8]
            position += 9 + (3 * (2 ** ((flags & 7) + 1)) if flags & 128 else 0)
            end = skip_subblocks(position + 1)  # LZW minimum code size, then data blocks.
            with destination.open('wb') as output_file:
                output_file.write(data[:end] + b'\x3b')
            return
        else:
            break
    raise ImageError(_('The GIF does not contain a readable first frame.'))


def prepare_image(path_or_uri):
    """Return a private, one-page PDF with the image's aspect and orientation."""
    prepared = None
    surface = None
    try:
        source = local_path(path_or_uri)
        if source.suffix.lower() not in IMAGE_SUFFIXES:
            raise ImageError(_('This file is not a supported raster image.'))
        if source.stat().st_size > MAX_IMAGE_BYTES:
            raise ImageError(_('The image file is too large (maximum 128 MiB).'))
        prepared = PreparedImage(source, tempfile.TemporaryDirectory(prefix='pympress-image-'))
        # Decode a bounded private snapshot. A concurrently modified source
        # cannot change its dimensions or grow after the header was checked.
        snapshot = pathlib.Path(prepared._directory.name) / ('source' + source.suffix.lower())
        copied = 0
        with source.open('rb') as input_file, snapshot.open('wb') as output_file:
            while True:
                block = input_file.read(1024 * 1024)
                if not block:
                    break
                copied += len(block)
                if copied > MAX_IMAGE_BYTES:
                    raise ImageError(_('The image file is too large (maximum 128 MiB).'))
                output_file.write(block)
        GdkPixbuf, Gdk, cairo = _graphics_modules()
        image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(str(snapshot))
        if image_format is None:
            raise ImageError(_('The image format is not supported by this build.'))
        _check_dimensions(width, height)
        decode_source = snapshot
        if image_format.get_name() == 'gif':
            decode_source = pathlib.Path(prepared._directory.name) / 'first-frame.gif'
            _first_gif_frame(snapshot, decode_source)
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(decode_source))
        oriented = pixbuf.apply_embedded_orientation()
        if oriented is not None:
            pixbuf = oriented
        width, height = pixbuf.get_width(), pixbuf.get_height()
        _check_dimensions(width, height)
        # PDF units are points; use a practical maximum page edge while keeping
        # the original aspect and full image resolution embedded in the PDF.
        scale = min(1.0, 1440.0 / max(width, height))
        surface = cairo.PDFSurface(str(prepared.pdf_path), width * scale, height * scale)
        context = cairo.Context(surface)
        context.set_source_rgb(1, 1, 1)
        context.paint()
        context.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
        context.paint()
        context.show_page()
        surface.finish()
        surface = None
        snapshot.unlink()
        if decode_source != snapshot:
            decode_source.unlink()
        return prepared
    except Exception as error:
        if surface is not None:
            try:
                surface.finish()
            except Exception:
                pass
        if prepared is not None:
            prepared.cleanup()
        if isinstance(error, ImageError):
            raise
        raise ImageError(_('Could not open the image: {}').format(error)) from error
