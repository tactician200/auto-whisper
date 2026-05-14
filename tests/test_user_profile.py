"""Tests for shared.user_profile (Slice 4.3)."""

import pytest


def test_privacy_mode_default_false(monkeypatch):
    monkeypatch.delenv("AUTO_WHISPER_PRIVACY_MODE", raising=False)
    from shared.user_profile import get_tts_preferences
    assert get_tts_preferences().privacy_mode is False


@pytest.mark.parametrize("env_value, expected", [
    ("1", True),
    ("0", False),
    ("", False),
    ("true", False),  # only literal "1" enables — explicit
    ("yes", False),
    ("on", False),
])
def test_privacy_mode_only_truthy_for_value_1(monkeypatch, env_value, expected):
    monkeypatch.setenv("AUTO_WHISPER_PRIVACY_MODE", env_value)
    from shared.user_profile import get_tts_preferences
    assert get_tts_preferences().privacy_mode is expected


def test_privacy_mode_read_at_call_time_not_import(monkeypatch):
    """Phase 5 settings UI will flip privacy_mode at runtime — the getter
    must reflect env changes between calls without re-import."""
    from shared.user_profile import get_tts_preferences

    monkeypatch.delenv("AUTO_WHISPER_PRIVACY_MODE", raising=False)
    assert get_tts_preferences().privacy_mode is False

    monkeypatch.setenv("AUTO_WHISPER_PRIVACY_MODE", "1")
    assert get_tts_preferences().privacy_mode is True

    monkeypatch.delenv("AUTO_WHISPER_PRIVACY_MODE")
    assert get_tts_preferences().privacy_mode is False
