"""Make macOS dialogs say 'auto-whisper' instead of 'Python'.

Without an Info.plist (we'll get one in Phase 6 with py2app), macOS reads
CFBundleName from the Python framework's own bundle. Result: rumps.Window,
rumps.alert, the dock tile, and Cmd-Tab all show 'Python'. We patch
NSBundle.mainBundle().infoDictionary in place at startup so every later
AppKit read sees our values instead.

Must be imported and applied BEFORE rumps / AppKit do anything that
triggers process activation. main.py calls apply() as its first action.

When py2app ships in Phase 6, the bundled Info.plist takes over and this
module becomes a no-op (the values it writes match what the plist would
provide, so behavior stays consistent).
"""

import logging

from auto_whisper import __version__

logger = logging.getLogger(__name__)

APP_NAME = "auto-whisper"
APP_BUNDLE_ID = "com.auto-whisper.v5"

# Strip the leading "v" — Apple's bundle version fields expect bare numerics
# like "5.0.0" or short strings like "5.0-dev". Either is accepted.
_VERSION_STRING = __version__.lstrip("v")


def apply() -> None:
    """Override the running process's bundle metadata. Idempotent.

    No-op (with a debug log, never raises) if PyObjC is missing or the
    bundle's infoDictionary is not mutable — both happen on non-macOS
    test runners and we never want branding to gate startup.
    """
    try:
        from Foundation import NSBundle
    except ImportError:
        logger.debug("PyObjC not available — skipping bundle branding override")
        return

    bundle = NSBundle.mainBundle()
    if bundle is None:
        logger.debug("NSBundle.mainBundle() returned None — skipping branding")
        return

    # localizedInfoDictionary is what AppKit prefers when a localized variant
    # exists; fall back to infoDictionary for the unlocalized base. Both are
    # NSMutableDictionary on a regular Python launch — we mutate in place so
    # everything reading from the bundle later sees our values.
    #
    # Use explicit `is None` instead of `or` because an empty dict is falsy
    # but still the right target — `{} or unlocalized` would write to the
    # wrong dictionary in that edge case.
    info = bundle.localizedInfoDictionary()
    if info is None:
        info = bundle.infoDictionary()
    if info is None:
        logger.debug("Bundle infoDictionary unavailable — skipping branding")
        return

    info["CFBundleName"] = APP_NAME
    info["CFBundleDisplayName"] = APP_NAME
    info["CFBundleIdentifier"] = APP_BUNDLE_ID
    info["CFBundleShortVersionString"] = _VERSION_STRING
    info["CFBundleVersion"] = _VERSION_STRING
    # Mark as agent (menubar-only). Without this, launchd sessions fail
    # to register the NSStatusItem reliably even though terminal launches
    # happen to work without it.
    info["LSUIElement"] = True

    # Force the activation policy to Accessory (menubar-only) BEFORE rumps
    # builds its NSStatusItem. Required when launched via LaunchAgent;
    # terminal launches work without it because the parent shell pulls in
    # different process attributes.
    try:
        from AppKit import NSApplication
        # 1 = NSApplicationActivationPolicyAccessory (no Dock icon, menubar only)
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception as exc:
        logger.debug("Failed to set NSApplicationActivationPolicyAccessory: %s", exc)
