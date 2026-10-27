"""Bounded, frame-clock driven crossfades on the audience window only."""
import math
import time
import logging

logger = logging.getLogger(__name__)


class FadeTimeline:
    """Track bounded crossfade progress using a pausable monotonic clock."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = None
        self.duration = 0.0
        self.paused_at = None

    def start(self, seconds):
        """Begin a fade lasting between zero and five seconds."""
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 <= seconds <= 5:
            raise ValueError('Transition duration must be between 0 and 5 seconds.')
        self.duration = float(seconds)
        self.started = self.clock() if seconds else None
        self.paused_at = None

    @property
    def progress(self):
        """Return normalized progress, retaining its value while paused."""
        now = self.clock() if self.paused_at is None else self.paused_at
        return 1.0 if self.started is None else min(1.0, max(0.0, (now - self.started) / self.duration))

    @property
    def remaining(self):
        """Return the remaining fade duration in seconds."""
        return self.duration * (1.0 - self.progress)

    @property
    def active(self):
        """Report whether a fade still has unrendered progress."""
        return self.started is not None and self.progress < 1.0

    def stop(self):
        """Clear the active timeline and paused state."""
        self.started = None
        self.paused_at = None

    def pause(self):
        """Freeze the current fade without changing its progress."""
        if self.active and self.paused_at is None:
            self.paused_at = self.clock()

    def resume(self):
        """Continue the fade from its frozen progress."""
        if self.paused_at is not None:
            self.started += self.clock() - self.paused_at
            self.paused_at = None


def fit_page(width, height, aspect, xalign=0.5, yalign=0.5):
    """Fit a slide into a viewport without stretching its content."""
    if width <= 0 or height <= 0 or not math.isfinite(aspect) or aspect <= 0:
        raise ValueError('Invalid slide or viewport dimensions.')
    slide_width = min(width, height * aspect)
    slide_height = slide_width / aspect
    return ((width - slide_width) * xalign, (height - slide_height) * yalign, slide_width, slide_height)


def bounded_size(width, height, scale=1):
    """At most 8 MP per snapshot, including Retina output."""
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValueError('A transition needs a visible viewport.')
    scale = min(max(1, scale), 2)
    factor = min(scale, math.sqrt(8000000 / (width * height)))
    pixels_wide = max(1, min(8000000, int(width * factor)))
    return pixels_wide, max(1, min(8000000 // pixels_wide, int(height * factor)))


def rasterize_page(page, page_type, width, height, xalign=0.5, yalign=0.5):
    """Render a slide over an opaque black background at the requested size."""
    import cairo
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
    context = cairo.Context(surface)
    context.set_source_rgb(0, 0, 0)
    context.paint()
    x, y, sw, sh = fit_page(width, height, page.get_aspect_ratio(page_type), xalign, yalign)
    context.translate(x, y)
    page.render_cairo(context, sw, sh, page_type)
    surface.flush()
    return surface


def paint_mix(context, old, new, progress, width, height):
    """Compose opaque slide snapshots; separate alpha never darkens the mix."""
    context.save()
    context.scale(width / old.get_width(), height / old.get_height())
    context.set_source_surface(old, 0, 0)
    context.paint()
    context.set_source_surface(new, 0, 0)
    context.paint_with_alpha(min(1.0, max(0.0, progress)))
    context.restore()


class Crossfade:
    """Display bounded slide snapshots in an audience-window overlay."""
    def __init__(self, ui):
        from gi.repository import Gtk
        self.ui = ui
        self.timeline = FadeTimeline()
        self.old = self.new = self.pending = None
        self.tick_id = None
        self.overlay = Gtk.Overlay()
        ui.c_win.remove(ui.c_frame)
        self.overlay.add(ui.c_frame)
        ui.c_win.add(self.overlay)
        self.layer = Gtk.DrawingArea()
        self.layer.set_hexpand(True)
        self.layer.set_vexpand(True)
        self.layer.set_no_show_all(True)
        self.layer.connect('draw', self._draw)
        self.overlay.add_overlay(self.layer)
        self.overlay.set_overlay_pass_through(self.layer, True)
        self.overlay.show_all()
        self.layer.hide()

    @property
    def remaining(self):
        """Return the remaining fade duration in seconds."""
        return self.timeline.remaining if self.old is not None else 0.0

    def _snapshot(self):
        """Capture the current slide using the audience viewport alignment."""
        width, height = bounded_size(self.overlay.get_allocated_width(), self.overlay.get_allocated_height(),
                                     self.overlay.get_scale_factor())
        page = self.ui.doc.page(self.ui.current_page)
        if page is None or not page.can_render():
            return None
        return rasterize_page(page, self.ui.notes_mode.complement(), width, height,
                              self.ui.c_frame.get_property('xalign'), self.ui.c_frame.get_property('yalign'))

    def prepare(self, seconds):
        """Freeze the visible old slide before changing document/cache ownership."""
        if not seconds or self.ui.blanked or self.ui.doc.doc is None:
            self.cancel()
            return False
        try:
            if self.old is not None and self.new is not None and self.timeline.active:
                import cairo
                snapshot = cairo.ImageSurface(cairo.FORMAT_RGB24, self.old.get_width(), self.old.get_height())
                paint_mix(cairo.Context(snapshot), self.old, self.new, self.timeline.progress,
                          snapshot.get_width(), snapshot.get_height())
            else:
                snapshot = self._snapshot()
        except Exception:
            logger.warning('Could not prepare transition; using a cut', exc_info=True)
            self.cancel()
            return False
        self.cancel()
        self.pending = snapshot
        self.seconds = seconds
        return snapshot is not None

    def begin(self):
        """Render the incoming slide and begin the prepared audience crossfade."""
        if self.pending is None:
            return
        self.old, self.pending = self.pending, None
        try:
            self.new = self._snapshot()
            if self.new is None:
                self.cancel()
                return
            # Keep both buffers at the same dimensions, including on rapid resize.
            if (self.new.get_width(), self.new.get_height()) != (self.old.get_width(), self.old.get_height()):
                self.cancel()
                return
            self.timeline.start(self.seconds)
            self.layer.show()
            self.layer.queue_draw()
            self.tick_id = self.layer.add_tick_callback(self._tick)
        except Exception:
            logger.warning('Could not render transition; using a cut', exc_info=True)
            self.cancel()

    def _tick(self, widget, frame_clock):
        """Request another frame until the crossfade has completed."""
        self.layer.queue_draw()
        if not self.timeline.active:
            self.tick_id = None
            self.cancel()
            return False
        return True

    def _draw(self, widget, context):
        """Paint the current mix and the live laser pointer into the overlay."""
        if self.old is None or self.new is None:
            return False
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        paint_mix(context, self.old, self.new, self.timeline.progress, width, height)
        page = self.ui.doc.page(self.ui.current_page)
        if page is not None and self.ui.laser.show_pointer:
            x, y, sw, sh = fit_page(width, height, page.get_aspect_ratio(self.ui.notes_mode.complement()),
                                    self.ui.c_frame.get_property('xalign'), self.ui.c_frame.get_property('yalign'))
            context.save()
            context.translate(x, y)
            self.ui.laser.render_pointer(context, sw, sh)
            context.restore()
        return True

    def cancel(self):
        """Remove pending frames and release both slide snapshots."""
        if self.tick_id is not None:
            self.layer.remove_tick_callback(self.tick_id)
            self.tick_id = None
        self.timeline.stop()
        self.old = self.new = self.pending = None
        self.layer.hide()

    def pause(self):
        """Freeze the current fade without changing its progress."""
        self.timeline.pause()
        if self.tick_id is not None:
            self.layer.remove_tick_callback(self.tick_id)
            self.tick_id = None

    def resume(self):
        """Continue the fade from its frozen progress."""
        self.timeline.resume()
        if self.old is not None and self.timeline.active and self.tick_id is None:
            self.tick_id = self.layer.add_tick_callback(self._tick)
