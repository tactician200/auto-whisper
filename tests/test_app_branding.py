"""Tests for auto_whisper.app_branding (UI polish)."""

import sys
from unittest.mock import MagicMock

import pytest


def test_apply_writes_expected_keys_to_info_dictionary(monkeypatch):
    """The whole point: dialogs read CFBundleName from the bundle's
    infoDictionary; we mutate that dict in place at startup so they show
    'auto-whisper' instead of 'Python'."""
    from auto_whisper import app_branding

    info = {}
    bundle = MagicMock()
    bundle.localizedInfoDictionary.return_value = None
    bundle.infoDictionary.return_value = info

    fake_foundation = MagicMock()
    fake_foundation.NSBundle.mainBundle.return_value = bundle
    monkeypatch.setitem(sys.modules, "Foundation", fake_foundation)

    app_branding.apply()

    assert info["CFBundleName"] == "auto-whisper"
    assert info["CFBundleDisplayName"] == "auto-whisper"
    assert info["CFBundleIdentifier"] == "com.auto-whisper.v5"
    assert "CFBundleShortVersionString" in info
    assert "CFBundleVersion" in info


def test_apply_prefers_localized_info_dictionary_when_present(monkeypatch):
    """AppKit reads localizedInfoDictionary first when a localized variant
    exists. Mutating the unlocalized dict would have no effect, so we have
    to target the localized one when it's available."""
    from auto_whisper import app_branding

    localized = {}
    unlocalized = {}
    bundle = MagicMock()
    bundle.localizedInfoDictionary.return_value = localized
    bundle.infoDictionary.return_value = unlocalized

    fake_foundation = MagicMock()
    fake_foundation.NSBundle.mainBundle.return_value = bundle
    monkeypatch.setitem(sys.modules, "Foundation", fake_foundation)

    app_branding.apply()

    assert localized["CFBundleName"] == "auto-whisper"
    assert "CFBundleName" not in unlocalized  # didn't touch the wrong dict


def test_apply_is_no_op_when_pyobjc_missing(monkeypatch):
    """On non-macOS test runners (CI Linux, GitHub Actions matrix) PyObjC
    isn't installed. Branding must never gate startup — it's pure polish."""
    from auto_whisper import app_branding

    # Simulate ImportError by stubbing the import
    monkeypatch.setitem(sys.modules, "Foundation", None)

    # Must not raise.
    app_branding.apply()


def test_apply_is_no_op_when_main_bundle_returns_none(monkeypatch):
    from auto_whisper import app_branding

    fake_foundation = MagicMock()
    fake_foundation.NSBundle.mainBundle.return_value = None
    monkeypatch.setitem(sys.modules, "Foundation", fake_foundation)

    # Must not raise.
    app_branding.apply()


def test_apply_is_idempotent(monkeypatch):
    """Multiple calls to apply() should leave the same final state — no
    duplicated entries, no crashes. This matters because the LaunchAgent
    can re-import auto_whisper.main after a crash-restart."""
    from auto_whisper import app_branding

    info = {}
    bundle = MagicMock()
    bundle.localizedInfoDictionary.return_value = None
    bundle.infoDictionary.return_value = info

    fake_foundation = MagicMock()
    fake_foundation.NSBundle.mainBundle.return_value = bundle
    monkeypatch.setitem(sys.modules, "Foundation", fake_foundation)

    app_branding.apply()
    snapshot = dict(info)
    app_branding.apply()
    assert info == snapshot


def test_version_string_strips_leading_v():
    """Apple's bundle version fields prefer bare version strings — we strip
    the 'v' prefix so the dock/Cmd-Tab don't show 'auto-whisper vv5.0-dev'."""
    from auto_whisper import app_branding

    assert not app_branding._VERSION_STRING.startswith("v")
