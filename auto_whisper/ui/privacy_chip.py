"""PrivacyChip — persistent "LOCAL" badge shown when AUTO_WHISPER_PRIVACY_MODE=1.

Visually distinct from ModeChip: muted teal/green background (privacy = safe)
instead of the violet accent, paired with a lock SF Symbol. Lives top-left of
the HUD when privacy is active.
"""
import objc
from AppKit import (
    NSView,
    NSTextField,
    NSImageView,
    NSImage,
    NSImageSymbolConfiguration,
    NSFont,
    NSColor,
    NSMakeRect,
)
from Quartz import CALayer, CAGradientLayer

from auto_whisper.ui.tokens import TOP_ROW_HEIGHT, PRIVACY_ACCENT


_PADDING_H = 6
_PADDING_V = 2
_ICON_GAP = 4
_CHIP_RADIUS = 6.0
_BG = PRIVACY_ACCENT.colorWithAlphaComponent_(0.92)


class PrivacyChip(NSView):
    """Container NSView with lock icon + 'LOCAL' label."""

    @classmethod
    def chip(cls):
        chip = cls.alloc().initWithFrame_(NSMakeRect(0, 0, 64, TOP_ROW_HEIGHT))
        if chip is None:
            return None
        chip._setup()
        return chip

    @objc.python_method
    def _setup(self) -> None:
        self.setWantsLayer_(True)
        layer = self.layer()
        layer.setBackgroundColor_(_BG.CGColor())
        layer.setCornerRadius_(_CHIP_RADIUS)

        # Lock SF Symbol (10pt, white). Fall back to "🔒" emoji label if the
        # symbol API isn't available (pre-macOS 11) — defensive but should
        # always succeed on supported versions.
        icon_view = None
        icon_w = 0.0
        try:
            symbol = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "lock.fill", "Privacy mode"
            )
            if symbol is not None:
                try:
                    cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(10, 5)
                    symbol = symbol.imageWithSymbolConfiguration_(cfg)
                except Exception:
                    pass
                icon_view = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(_PADDING_H, _PADDING_V, 11, TOP_ROW_HEIGHT - 2 * _PADDING_V)
                )
                icon_view.setImage_(symbol)
                icon_view.setContentTintColor_(NSColor.whiteColor())
                icon_w = 11.0
        except Exception:
            icon_view = None

        label_x = _PADDING_H + icon_w + (_ICON_GAP if icon_w else 0)
        self._label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(label_x, _PADDING_V, 40, TOP_ROW_HEIGHT - 2 * _PADDING_V)
        )
        self._label.setEditable_(False)
        self._label.setBezeled_(False)
        self._label.setDrawsBackground_(False)
        self._label.setSelectable_(False)
        self._label.setFont_(NSFont.boldSystemFontOfSize_(10))
        self._label.setTextColor_(NSColor.whiteColor())
        self._label.setStringValue_("PRIVACY · LOCAL")
        self._label.sizeToFit()

        label_w = self._label.frame().size.width
        chip_w = label_x + label_w + _PADDING_H
        self.setFrame_(NSMakeRect(
            self.frame().origin.x, self.frame().origin.y, chip_w, TOP_ROW_HEIGHT
        ))
        self._label.setFrame_(
            NSMakeRect(label_x, _PADDING_V, label_w, TOP_ROW_HEIGHT - 2 * _PADDING_V)
        )

        if icon_view is not None:
            self.addSubview_(icon_view)
        self.addSubview_(self._label)

        # Rim light — horizontal gradient reads as a lit curved surface.
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
    def width(self) -> float:
        return self.frame().size.width
