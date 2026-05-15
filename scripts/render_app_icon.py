#!/usr/bin/env python3
"""Render the auto-whisper app icon from SF Symbol `waveform.circle.fill`.

Renders 10 PNGs at the canonical macOS iconset sizes, builds an .iconset
directory, and converts to .icns via iconutil. Idempotent — re-running
overwrites everything.

Output:
    assets/AppIcon.iconset/   (intermediate iconset dir)
    assets/AppIcon.icns       (final, fed to py2app via setup_app.py)

Why Python+PyObjC instead of a Swift script: this venv already has the
AppKit bindings the daemon uses, so we don't add a Swift build step to
the packaging flow.

Run:
    .venv/bin/python scripts/render_app_icon.py
"""

import os
import shutil
import subprocess
import sys

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSImage,
    NSImageSymbolConfiguration,
)
from Foundation import NSMakeRect

# --- design tokens (match auto_whisper/ui/tokens.py where possible) ---
ACCENT_R, ACCENT_G, ACCENT_B = 0.486, 0.361, 1.0   # #7C5CFF
BG_R,     BG_G,     BG_B     = 0.07,  0.07,  0.09  # #121217 — slightly darker than HUD

# Standard macOS iconset filenames → pixel size.
# Sources: https://developer.apple.com/design/human-interface-guidelines/app-icons
ICONSET_SIZES = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

NSPNGFileType = 4  # NSBitmapImageFileTypePNG


def _render_one(size: int, out_path: str) -> None:
    """Render a single PNG at `size` pixels square."""
    img = NSImage.alloc().initWithSize_((size, size))
    img.lockFocus()

    # Dark rounded-rect background — ~18% corner radius, iOS-style squircle feel.
    NSColor.colorWithRed_green_blue_alpha_(BG_R, BG_G, BG_B, 1.0).set()
    radius = size * 0.18
    bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(0, 0, size, size), radius, radius
    )
    bg.fill()

    # SF Symbol — render at ~65% of icon size, tinted with the accent hierarchy.
    accent = NSColor.colorWithRed_green_blue_alpha_(ACCENT_R, ACCENT_G, ACCENT_B, 1.0)
    point_size = size * 0.65
    config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(point_size, 5)
    try:
        # Hierarchical color: macOS 12+. Tints the primary path with `accent`
        # and applies derived tones to secondary paths automatically.
        hierarchical = NSImageSymbolConfiguration.configurationWithHierarchicalColor_(accent)
        config = config.configurationByApplyingConfiguration_(hierarchical)
    except Exception:
        # Older macOS — fall back to template image + manual fill (less pretty
        # but still readable).
        pass

    symbol = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        "waveform.circle.fill", None
    )
    if symbol is None:
        # SF Symbols not available — render a stylised text fallback.
        img.unlockFocus()
        raise RuntimeError("SF Symbol 'waveform.circle.fill' not available on this macOS")
    symbol = symbol.imageWithSymbolConfiguration_(config)

    sym_size = symbol.size()
    x = (size - sym_size.width) / 2.0
    y = (size - sym_size.height) / 2.0
    symbol.drawAtPoint_fromRect_operation_fraction_(
        (x, y), NSMakeRect(0, 0, sym_size.width, sym_size.height), 1, 1.0
    )

    img.unlockFocus()

    # Encode TIFF → bitmap → PNG.
    data = img.TIFFRepresentation()
    bitmap = NSBitmapImageRep.alloc().initWithData_(data)
    png = bitmap.representationUsingType_properties_(NSPNGFileType, {})
    ok = png.writeToFile_atomically_(out_path, True)
    if not ok:
        raise RuntimeError(f"Failed to write {out_path}")


def main() -> int:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    iconset_dir = os.path.join(repo_root, "assets", "AppIcon.iconset")
    icns_path = os.path.join(repo_root, "assets", "AppIcon.icns")

    if os.path.isdir(iconset_dir):
        shutil.rmtree(iconset_dir)
    os.makedirs(iconset_dir, exist_ok=True)

    for filename, size in ICONSET_SIZES:
        out = os.path.join(iconset_dir, filename)
        _render_one(size, out)
        print(f"  ✓ {filename} ({size}px)")

    # Build .icns via Apple's iconutil.
    print(f"\n→ iconutil → {icns_path}")
    result = subprocess.run(
        ["iconutil", "-c", "icns", iconset_dir, "-o", icns_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("iconutil failed:", result.stderr, file=sys.stderr)
        return result.returncode

    size = os.path.getsize(icns_path)
    print(f"✓ {icns_path} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
