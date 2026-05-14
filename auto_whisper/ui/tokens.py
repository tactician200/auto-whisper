"""Design tokens for the floating HUD UI. Single source of truth."""
from AppKit import NSColor, NSFont, NSVisualEffectMaterialPopover

ACCENT          = NSColor.colorWithRed_green_blue_alpha_(0.486, 0.361, 1.0, 1.0)  # #7C5CFF violet
PRIVACY_ACCENT  = NSColor.colorWithRed_green_blue_alpha_(0.20, 0.70, 0.45, 1.0)  # privacy teal-green
BG_MATERIAL     = NSVisualEffectMaterialPopover  # frosted glass used by Control Center & menubar popovers — more see-through than HUDWindow

HUD_WIDTH       = 300
HUD_HEIGHT      = 110
HUD_TOP_INSET   = 60         # gap below menubar; notch insets added at runtime
HUD_RIGHT_INSET = 20         # gap from right edge of screen
HUD_ALPHA       = 1.00       # panel fully opaque; translucency lives in the material itself
                             # (otherwise alpha bleeds into chip/REC/waveform)

BAR_COUNT       = 48
BAR_WIDTH       = 3
BAR_GAP         = 2
PREVIEW_MS      = 800
FPS             = 60
FONT_UI         = NSFont.systemFontOfSize_(11)
FONT_MONO       = NSFont.monospacedSystemFontOfSize_weight_(11, 0)

FADE_IN_MS      = 180
FADE_OUT_MS     = 180
ARRIVAL_SLIDE   = 8       # px of vertical slide-down on show (from menubar)

# Layout for HUD subviews (used in Phase B+)
PADDING_LR       = 12     # left/right padding inside HUD
PADDING_TB       = 6      # top/bottom padding (smaller — gives waveform more room)
TOP_ROW_HEIGHT   = 20     # chip + REC row
WAVEFORM_TOP_GAP = 16     # gap between top row and waveform (legacy, kept for compat)
WAVEFORM_HEIGHT  = 32     # fixed height — gives more visual range for bars
INNER_GAP        = 18     # vertical gap between top row and waveform — clear visual separation
SCALE_RMS_TO_BAR = 40.0   # multiplier from RMS to visual bar height fraction
