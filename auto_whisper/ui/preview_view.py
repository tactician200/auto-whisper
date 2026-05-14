"""PreviewTextView — read-only text view shown in place of waveform during preview."""
import objc
from AppKit import NSTextView, NSColor

from auto_whisper.ui.tokens import FONT_MONO

_MAX_CHARS = 80


class PreviewTextView(NSTextView):
    """Read-only mono text view. Replaces waveform area during the 800ms preview."""

    def initWithFrame_(self, frame):
        self = objc.super(PreviewTextView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setEditable_(False)
        self.setSelectable_(False)
        self.setDrawsBackground_(False)
        self.setFont_(FONT_MONO)
        self.setTextColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.95))
        self.textContainer().setLineFragmentPadding_(0)
        self.textContainer().setWidthTracksTextView_(True)
        self.setHorizontallyResizable_(False)
        self.setVerticallyResizable_(False)
        return self

    @objc.python_method
    def set_text(self, text: str, processed: bool = False) -> None:
        if len(text) > _MAX_CHARS:
            text = text[: _MAX_CHARS - 1] + "…"
        self.setString_(text)
        color = (
            NSColor.colorWithRed_green_blue_alpha_(0.7, 0.9, 1.0, 0.95)
            if processed
            else NSColor.whiteColor().colorWithAlphaComponent_(0.95)
        )
        self.setTextColor_(color)
