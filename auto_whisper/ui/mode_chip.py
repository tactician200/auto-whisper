"""ModeChip — pill label showing active recording mode in accent color."""
import objc
from AppKit import NSView, NSTextField, NSFont, NSColor, NSMakeRect
from Foundation import NSString
from Quartz import CALayer, CAGradientLayer

from auto_whisper.ui.tokens import ACCENT, TOP_ROW_HEIGHT


_PADDING_H = 6   # horizontal padding inside chip
_PADDING_V = 2   # vertical padding inside chip
_CHIP_RADIUS = 6.0  # capsule feel (h/2 - 4) for a 20px row


class ModeChip(NSView):
    """Container NSView with accent background + rounded corners + NSTextField label."""

    @classmethod
    def chipWithMode_(cls, mode):
        chip = cls.alloc().initWithFrame_(NSMakeRect(0, 0, 64, TOP_ROW_HEIGHT))
        if chip is None:
            return None
        chip._setup(mode, ACCENT)
        return chip

    @classmethod
    def chipWithMode_color_(cls, mode, color):
        chip = cls.alloc().initWithFrame_(NSMakeRect(0, 0, 64, TOP_ROW_HEIGHT))
        if chip is None:
            return None
        chip._setup(mode, color)
        return chip

    @objc.python_method
    def _setup(self, mode: str, color) -> None:
        self.setWantsLayer_(True)
        layer = self.layer()
        layer.setBackgroundColor_(color.colorWithAlphaComponent_(0.95).CGColor())
        layer.setCornerRadius_(_CHIP_RADIUS)

        self._label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(_PADDING_H, _PADDING_V,
                       64 - 2 * _PADDING_H, TOP_ROW_HEIGHT - 2 * _PADDING_V)
        )
        self._label.setEditable_(False)
        self._label.setBezeled_(False)
        self._label.setDrawsBackground_(False)
        self._label.setSelectable_(False)
        self._label.setFont_(NSFont.boldSystemFontOfSize_(10))
        self._label.setTextColor_(NSColor.whiteColor())
        self._label.setStringValue_(mode.upper())
        self._label.sizeToFit()

        # Resize chip to fit label + padding
        label_w = self._label.frame().size.width
        chip_w = label_w + 2 * _PADDING_H
        chip_frame = self.frame()
        self.setFrame_(NSMakeRect(
            chip_frame.origin.x, chip_frame.origin.y,
            chip_w, TOP_ROW_HEIGHT
        ))
        self._label.setFrame_(
            NSMakeRect(_PADDING_H, _PADDING_V, label_w, TOP_ROW_HEIGHT - 2 * _PADDING_V)
        )

        self.addSubview_(self._label)

        # Rim light — horizontal gradient (faded → bright → faded) reads as
        # a lit curved surface, not just a top border.
        self._rim = CAGradientLayer.layer()
        c_edge   = NSColor.whiteColor().colorWithAlphaComponent_(0.15).CGColor()
        c_center = NSColor.whiteColor().colorWithAlphaComponent_(0.70).CGColor()
        self._rim.setColors_([c_edge, c_center, c_edge])
        self._rim.setLocations_([0.0, 0.5, 1.0])
        self._rim.setStartPoint_((0.0, 0.5))
        self._rim.setEndPoint_((1.0, 0.5))
        self._rim.setFrame_(NSMakeRect(0, TOP_ROW_HEIGHT - 0.5, chip_w, 0.5))
        layer.addSublayer_(self._rim)

    @objc.python_method
    def set_mode(self, mode: str) -> None:
        if self._label is not None:
            self._label.setStringValue_(mode.upper())
            self._label.sizeToFit()
            label_w = self._label.frame().size.width
            chip_w = label_w + 2 * _PADDING_H
            origin = self.frame().origin
            self.setFrame_(NSMakeRect(
                origin.x, origin.y, chip_w, TOP_ROW_HEIGHT
            ))
            self._label.setFrame_(
                NSMakeRect(_PADDING_H, _PADDING_V, label_w, TOP_ROW_HEIGHT - 2 * _PADDING_V)
            )
            # Keep rim light spanning the new width
            if getattr(self, "_rim", None) is not None:
                self._rim.setFrame_(NSMakeRect(0, TOP_ROW_HEIGHT - 0.5, chip_w, 0.5))

    @objc.python_method
    def width(self) -> float:
        return self.frame().size.width
