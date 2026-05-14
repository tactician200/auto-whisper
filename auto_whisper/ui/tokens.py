"""Design tokens for the floating HUD UI. Single source of truth."""
from AppKit import (
    NSColor,
    NSFont,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectMaterialPopover,
)

ACCENT          = NSColor.colorWithRed_green_blue_alpha_(0.486, 0.361, 1.0, 1.0)  # #7C5CFF violet
PRIVACY_ACCENT  = NSColor.colorWithRed_green_blue_alpha_(0.20, 0.70, 0.45, 1.0)  # privacy teal-green
BG_MATERIAL     = NSVisualEffectMaterialPopover  # kept for backward compat; new HUD uses GLASS_MATERIAL

# Liquid-glass tokens — extracted 1:1 from the codepen liquid-glass-ui-kit
# .glass base class (blur 18px, bg 12%, border 25%). See:
#   ~/Claude/projects/auto-whisper/.design/UI samples/codepen-liquid-glass/css.md
# Treat these as the single source of truth for the HUD's glass treatment.
GLASS_MATERIAL              = NSVisualEffectMaterialHUDWindow  # denser frost than Popover
# 2026-05-14 iteration C — halfway between the codepen .glass base (A: 0.60 /
# 0.12 / 0.25) and the .glass--liquid variant (B: 0.40 / 0.06 / 0.18). User
# feedback: B too transparent over colourful wallpapers (waveform contrast
# suffered) but A's border looked dark. Final: backdrop halfway, tint halfway,
# border kept bright. Tweak any of these in isolation; the chip/waveform code
# all reads from here.
GLASS_ALPHA                 = 0.50   # halfway between .glass (0.60) and --liquid (0.40)
GLASS_BG_TINT_ALPHA         = 0.09   # halfway between .glass (0.12) and --liquid (0.06)
GLASS_BORDER_ALPHA          = 0.40   # user-preferred bright border (was 0.25 in .glass)
GLASS_TOP_HIGHLIGHT_ALPHA   = 0.30   # inset 0 1px 0 rgba(255,255,255,.3)
GLASS_REFLECTION_ALPHA      = 0.40   # 135deg gradient start — rgba(255,255,255,.4)
GLASS_DROP_SHADOW_ALPHA     = 0.28   # 0 8px 32px rgba(0,0,0,.28)
GLASS_DROP_SHADOW_RADIUS    = 32.0
GLASS_DROP_SHADOW_OFFSET_Y  = -8.0   # AppKit y axis is inverted; CSS 8px down = -8 in CG

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
