"""WaveformBarsView — 48-bar discrete waveform driven by RMS levels."""
import queue
import objc
from AppKit import NSView, NSColor, NSBezierPath, NSRectFill
from Foundation import NSMakeRect

from auto_whisper.ui.tokens import ACCENT, BAR_COUNT, BAR_WIDTH, BAR_GAP, SCALE_RMS_TO_BAR


class WaveformBarsView(NSView):
    @objc.python_method
    def initWithFrame_(self, frame):
        self = objc.super(WaveformBarsView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._q = queue.Queue(maxsize=64)
        self._raw = [0.0] * BAR_COUNT
        self._display = [0.0] * BAR_COUNT
        self._color = ACCENT
        return self

    @objc.python_method
    def set_color(self, color) -> None:
        """Override bar color (e.g. privacy green instead of violet accent)."""
        self._color = color
        self.setNeedsDisplay_(True)

    @objc.python_method
    def push_level(self, rms: float) -> None:
        """Thread-safe — callable from audio callback."""
        try:
            self._q.put_nowait(rms)
        except queue.Full:
            pass

    @objc.python_method
    def drain_queue(self) -> None:
        """Drain pending levels + interpolate display every frame.

        Decoupling ingest from interpolation is what makes motion feel
        fluid: at 60fps we always step display toward raw, so bars keep
        sliding even between audio callbacks (~10/sec). Old code only
        redrew when new audio arrived → bars looked stuck between bursts.
        """
        while True:
            try:
                level = self._q.get_nowait()
            except queue.Empty:
                break
            # Shift ring buffer left, append new level at right.
            self._raw = self._raw[1:] + [level]

        # Per-frame ease — at 60fps, 0.35 per step reaches ~75% of target
        # in 33ms (similar to the old 0.80@30fps response) but with twice
        # as many in-between frames, removing the "step" feel.
        for i in range(BAR_COUNT):
            self._display[i] = self._display[i] * 0.65 + self._raw[i] * 0.35
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        # CRITICAL: use self.bounds(), NOT rect (the dirty rect, which can
        # be larger than the view's bounds when macOS asks for a wider
        # redraw — that was making bars draw way outside the view's
        # frame, overflowing onto the chip area above).
        view_w = self.bounds().size.width
        view_h = self.bounds().size.height

        # Clear with transparent background — only within our bounds.
        NSColor.clearColor().set()
        NSRectFill(self.bounds())

        total_bar_width = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        padding_left = (view_w - total_bar_width) / 2.0
        half_count = BAR_COUNT / 2.0

        for i in range(BAR_COUNT):
            level = self._display[i]
            bar_h = min(level * SCALE_RMS_TO_BAR, 1.0) * (view_h - 4)
            bar_h = max(bar_h, 1.5)  # whisper-thin baseline at edges

            # Gradient opacity falloff — center bars pop, edges legible but
            # quieter. Floor raised from 0.05 → 0.18 and exponent softened
            # 2.2 → 1.7 so the waveform has enough body to stay readable
            # over colourful wallpapers (user feedback 2026-05-14). The
            # centre peak is still ~0.98 so audio-reactive movement reads.
            dist_from_center = 1.0 - abs(i - half_count + 0.5) / half_count
            alpha = 0.18 + 0.80 * (dist_from_center ** 1.7)
            self._color.colorWithAlphaComponent_(alpha).set()

            # Classic centered bars — grow up and down equally from the
            # view's vertical center.
            x = padding_left + i * (BAR_WIDTH + BAR_GAP)
            y = (view_h - bar_h) / 2.0
            bar_rect = NSMakeRect(x, y, BAR_WIDTH, bar_h)

            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar_rect, 1.5, 1.5
            )
            path.fill()

    @objc.python_method
    def pulse_indeterminate(self, phase: float) -> None:
        """Indeterminate 'thinking' sweep — used while processing (no live audio).

        A low-amplitude gaussian bump travels left↔right across the bars so the
        HUD reads as working rather than frozen. `phase` is seconds elapsed
        since processing began; the bump position ping-pongs with it.
        """
        import math
        # Ping-pong the bump centre across the bar range, ~1.4s per sweep.
        pos = (math.sin(phase * 2.0 * math.pi / 1.4) * 0.5 + 0.5) * (BAR_COUNT - 1)
        sigma = BAR_COUNT / 7.0
        for i in range(BAR_COUNT):
            d = i - pos
            # Peak ≈ 0.012 → 0.012 * SCALE_RMS_TO_BAR(40) ≈ 0.48 of bar height.
            self._raw[i] = 0.012 * math.exp(-(d * d) / (2.0 * sigma * sigma))
        for i in range(BAR_COUNT):
            self._display[i] = self._display[i] * 0.65 + self._raw[i] * 0.35
        self.setNeedsDisplay_(True)

    @objc.python_method
    def reset(self) -> None:
        self._raw = [0.0] * BAR_COUNT
        self._display = [0.0] * BAR_COUNT
        self.setNeedsDisplay_(True)

    @objc.python_method
    def recent_level(self) -> float:
        """Max RMS over the last ~6 frames — used by HUD audio-reactive pulse."""
        if not self._raw:
            return 0.0
        return max(self._raw[-6:])
