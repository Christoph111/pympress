"""Monotonic slideshow timing independent of GTK and document navigation.

The owner supplies navigation via ``advance(mode, loop)`` and returns the next
slide's duration, or ``None`` at the end. At file boundaries, the incoming file's
``transition_enabled`` flag determines whether the owner's visual effect runs.
"""

import time

from pympress.filelist import validate_slide_seconds


class PlaybackController:
    """One cancellable timer with pause/resume and stale-callback protection.

    ``schedule(seconds, callback)`` returns a handle for ``cancel(handle)``.
    Callbacks return False so a GLib timeout wrapper can use them directly.
    Scheduling must be asynchronous, as with a normal UI event loop.

    Args:
        schedule (callable): Schedule a one-shot callback and return its handle.
        cancel (callable): Cancel a previously scheduled handle.
        advance (callable): Advance a slide and return its duration, or None to stop.
        clock (callable): Return monotonic time in seconds.
        on_change (callable): Receive this controller after its state changes.
    """

    def __init__(self, schedule, cancel, advance, clock=time.monotonic, on_change=None):
        """Initialize a stopped controller with the owner's event-loop callbacks."""
        self._schedule = schedule
        self._cancel = cancel
        self._advance = advance
        self._clock = clock
        self._on_change = on_change
        self._handle = None
        self._deadline = None
        self._remaining = 0.0
        self._generation = 0
        self.state = 'stopped'
        self.mode = 'current'
        self.loop = False

    @property
    def running(self):
        """Return whether the slide timer is running."""
        return self.state == 'running'

    @property
    def paused(self):
        """Return whether a slideshow has a preserved paused remainder."""
        return self.state == 'paused'

    @property
    def is_active(self):
        """Return whether the slideshow is running or paused."""
        return self.state != 'stopped'

    @property
    def remaining(self):
        """Return seconds until the deadline, preserving the value while paused."""
        if self.running and self._deadline is not None:
            return max(0.0, self._deadline - self._clock())
        return self._remaining if self.paused else 0.0

    def _notify(self):
        """Notify the owner after changing timer state."""
        if self._on_change is not None:
            self._on_change(self)

    def _disarm(self):
        """Cancel the current timer and invalidate its pending callbacks."""
        self._generation += 1
        handle, self._handle = self._handle, None
        self._deadline = None
        if handle is not None:
            self._cancel(handle)

    def _arm(self, seconds):
        """Schedule a fresh deadline after invalidating the previous timer."""
        self._disarm()
        self._remaining = seconds
        self._deadline = self._clock() + seconds
        generation = self._generation
        try:
            self._handle = self._schedule(seconds, lambda: self._fire(generation))
        except Exception:
            self._deadline = None
            self._remaining = 0.0
            self.state = 'stopped'
            self._notify()
            raise

    def start(self, seconds, mode='current', loop=False):
        """Start a slideshow after validating its duration and navigation policy.

        Args:
            seconds (float): Display duration for the first slide.
            mode (str): Either ``current`` for one file or ``list`` for the file list.
            loop (bool): Whether to restart at the end of the selected scope.

        Raises:
            ValueError: The duration, mode, or loop setting is invalid.
        """
        seconds = validate_slide_seconds(seconds)
        if mode not in ('current', 'list') or type(loop) is not bool:
            raise ValueError(_('The slideshow mode or loop setting is invalid.'))
        self.mode, self.loop = mode, loop
        self.state = 'running'
        self._arm(seconds)
        self._notify()

    def pause(self):
        """Preserve the remaining display time and stop scheduling slides."""
        if not self.running:
            return
        remaining = self.remaining
        self._disarm()
        self._remaining = remaining
        self.state = 'paused'
        self._notify()

    def resume(self):
        """Resume a paused slideshow with its preserved remaining time."""
        if not self.paused:
            return
        self.state = 'running'
        self._arm(self._remaining)
        self._notify()

    def stop(self):
        """Cancel scheduling and discard any paused remainder."""
        was_active = self.is_active
        self._disarm()
        self._remaining = 0.0
        self.state = 'stopped'
        if was_active:
            self._notify()

    def reset(self, seconds):
        """Give a manually selected slide its full duration without resuming."""
        seconds = validate_slide_seconds(seconds)
        if self.running:
            self._arm(seconds)
        elif self.paused:
            self._disarm()
            self._remaining = seconds
        else:
            return
        self._notify()

    def _fire(self, generation):
        """Advance at an expired deadline while ignoring stale timer callbacks.

        Args:
            generation (int): Timer generation captured when scheduling the callback.

        Returns:
            bool: Always False so the event loop removes this one-shot callback.
        """
        if generation != self._generation or not self.running:
            return False
        # Some schedulers round timeouts down; a premature callback must never
        # advance the slide before its monotonic deadline.
        remaining = self.remaining
        self._handle = None
        if remaining > 0:
            self._arm(remaining)
            return False
        self._deadline = None
        try:
            seconds = self._advance(self.mode, self.loop)
            # Navigation may synchronously reset, pause, stop or restart us.
            if generation != self._generation or not self.running:
                return False
            if seconds is None:
                self.stop()
            else:
                self._arm(validate_slide_seconds(seconds))
                self._notify()
        except Exception:
            self.stop()
            raise
        return False
