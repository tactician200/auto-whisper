"""TitlePulseController — violet pulse on the menubar title during recording.

Wraps the NSStatusItem.button() with an NSAttributedString so we can tint the
Unicode glyph (◉) violet and animate its alpha. Plain-title path is preserved
for all non-recording states.

The button() is lazy: rumps creates the NSStatusItem only in App.run(), so we
fetch it on first apply() and cache it. If it's not ready yet we fall back to
the rumps title-string path, which the app already uses.
"""
import math
import objc
from AppKit import (
    NSAttributedString,
    NSColor,
    NSFont,
    NSForegroundColorAttributeName,
    NSFontAttributeName,
)
from Foundation import NSTimer

from auto_whisper.ui.tokens import ACCENT

_PULSE_FPS = 30
_PULSE_PERIOD_S = 1.2  # one full cycle every 1.2s
_ALPHA_MIN = 0.55
_ALPHA_MAX = 1.0


class TitlePulseController:
    """Apply icon to menubar; pulse violet when in recording state."""

    def __init__(self, rumps_app):
        self._rumps_app = rumps_app
        self._button = None
        self._timer = None
        self._icon = ""
        self._phase = 0.0  # 0..1, advanced per tick

    def _ensure_button(self):
        if self._button is not None:
            return self._button
        nsapp = getattr(self._rumps_app, "_nsapp", None)
        if nsapp is None:
            return None
        try:
            self._button = nsapp.nsstatusitem.button()
        except Exception:
            self._button = None
        return self._button

    @objc.python_method
    def apply(self, icon: str, recording: bool) -> None:
        self._icon = icon
        button = self._ensure_button()
        if button is None:
            # Fallback: let rumps own the title until the button is ready.
            try:
                self._rumps_app.title = icon
            except Exception:
                pass
            return

        if recording:
            self._start_pulse()
            self._render_pulse_frame()
        else:
            self._stop_pulse()
            # Plain (system-colored) title — same as before Phase E.
            self._rumps_app.title = icon

    def _start_pulse(self) -> None:
        if self._timer is not None:
            return
        self._phase = 0.0
        interval = 1.0 / _PULSE_FPS
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            interval, self, b"_tick:", None, True
        )

    def _stop_pulse(self) -> None:
        if self._timer is None:
            return
        try:
            self._timer.invalidate()
        except Exception:
            pass
        self._timer = None

    def _tick_(self, _timer):
        # Advance phase. NSTimer is firing on main thread already.
        self._phase = (self._phase + (1.0 / (_PULSE_FPS * _PULSE_PERIOD_S))) % 1.0
        self._render_pulse_frame()

    @objc.python_method
    def _render_pulse_frame(self) -> None:
        if self._button is None:
            return
        # Smooth sine pulse from MIN→MAX→MIN.
        # sin(2πx) ∈ [-1,1] → mapped to [_ALPHA_MIN, _ALPHA_MAX].
        s = (math.sin(2.0 * math.pi * self._phase) + 1.0) * 0.5  # [0,1]
        alpha = _ALPHA_MIN + (_ALPHA_MAX - _ALPHA_MIN) * s
        color = ACCENT.colorWithAlphaComponent_(alpha)

        font = NSFont.menuBarFontOfSize_(0)
        attrs = {
            NSForegroundColorAttributeName: color,
            NSFontAttributeName: font,
        }
        attr_str = NSAttributedString.alloc().initWithString_attributes_(self._icon, attrs)
        try:
            self._button.setAttributedTitle_(attr_str)
        except Exception:
            pass
