"""Input device resolution — Bluetooth-aware fallback to built-in mic.

Why this exists: macOS Core Audio forces Bluetooth headphones into HFP/SCO
profile when an app opens the mic, capping capture at 8-16 kHz mono with
a low-bitrate codec (CVSD/mSBC). Whisper-large-v3 trains on full 16 kHz
audio and degrades hard on HFP-compressed input — users report "it only
catches half my words" with AirPods, while the built-in mic works fine
even at distance.

Behavior: when the system default input is Bluetooth AND the user hasn't
manually picked a device, we override to the built-in mic so AirPods stay
useful as output but don't poison the input path. Manual selection always
wins — if the user explicitly chose AirPods from the menu, we honor it.

Opt out with AUTO_WHISPER_PREFER_BUILTIN_MIC=0 (default ON for v5 launch).
"""

import logging
import os
from typing import Iterable

import sounddevice as sd

logger = logging.getLogger(__name__)


# Read once at module import — restart to flip. Default ON because the
# alternative (AirPods + degraded transcription) is the user-reported bug
# that motivated this module.
PREFER_BUILTIN_MIC: bool = (
    os.environ.get("AUTO_WHISPER_PREFER_BUILTIN_MIC", "1") == "1"
)


# Substrings that strongly indicate a Bluetooth input on macOS. Case-insensitive
# match against the device name. Conservative — these are brand/category names
# that would never appear on a wired/internal mic.
_BT_NAME_MARKERS = (
    "airpods",
    "bluetooth",
    "headset",
    "buds",      # Galaxy Buds, Pixel Buds, etc.
    "beats",     # Beats headphones (also Apple-owned, often go through HFP)
    "sony wf",   # Sony WF/WH series
    "sony wh",
    "bose",
    "jabra",
    "powerbeats",
)


def _is_bluetooth_input(name: str) -> bool:
    """Heuristic — return True if `name` looks like a Bluetooth input device.

    Matches by substring against a curated list of brand/category markers.
    False negatives (rare/no-name BT mics) just mean the user falls back to
    manual menu selection, which already works.
    """
    lowered = name.lower()
    return any(marker in lowered for marker in _BT_NAME_MARKERS)


# Substrings that identify an Apple internal mic. macOS names vary across
# models — "Built-in Microphone" on Intel/older, "MacBook Air Microphone" /
# "MacBook Pro Microphone" on Apple Silicon, "iMac Microphone", "Mac mini
# Microphone", "Studio Display Microphone" on connected displays. The
# internal mic is what we want as the BT-override fallback.
_BUILTIN_NAME_MARKERS = (
    "built-in",
    "macbook",
    "imac",
    "mac mini",
    "mac pro",
    "studio display",  # Apple Studio Display has a 3-mic array
)


def _find_builtin_input_index(devices: Iterable[dict]) -> int | None:
    """Return the index of the first device that looks like an internal mic."""
    for i, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) <= 0:
            continue
        name = dev.get("name", "").lower()
        if any(m in name for m in _BUILTIN_NAME_MARKERS):
            return i
    return None


def resolve_input_device(
    requested_index: int | None,
    prefer_builtin: bool = PREFER_BUILTIN_MIC,
) -> tuple[int | None, str]:
    """Pick the input device, applying the Bluetooth → built-in fallback.

    Returns (sd_device_spec, reason) where:
      - sd_device_spec is what to pass to sd.query_devices() / sd.InputStream()
        — either an int index or None to mean "system default"
      - reason is a short human-readable string describing the choice, for
        logs and the menubar tooltip

    Resolution order:
      1. User-selected (requested_index is not None) → respect it as-is.
      2. prefer_builtin OFF or default isn't Bluetooth → use system default.
      3. Default is Bluetooth + we have a built-in available → override.
      4. Default is Bluetooth + no built-in (rare; e.g. external display
         setup with no internal mic) → fall through to default with a warning.
    """
    if requested_index is not None:
        return requested_index, "user-selected"

    try:
        default = sd.query_devices(kind="input")
    except Exception as e:
        logger.warning(f"Could not inspect system default input: {e}")
        return None, "system default (inspection failed)"

    default_name = default.get("name", "?")
    if not prefer_builtin or not _is_bluetooth_input(default_name):
        return None, f"system default ({default_name})"

    # Default is Bluetooth + we want to override. Find a built-in.
    builtin = _find_builtin_input_index(sd.query_devices())
    if builtin is None:
        logger.warning(
            f"Default input is Bluetooth ({default_name}) but no built-in mic "
            "found; staying on default — Whisper accuracy may suffer."
        )
        return None, f"BT default, no built-in available ({default_name})"

    builtin_name = sd.query_devices(builtin, kind="input").get("name", "?")
    logger.info(
        f"Default input is Bluetooth ({default_name}); overriding to "
        f"built-in ({builtin_name}) for transcription quality. "
        "Set AUTO_WHISPER_PREFER_BUILTIN_MIC=0 to disable."
    )
    return builtin, f"BT override → {builtin_name}"
