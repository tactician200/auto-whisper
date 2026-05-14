"""Tests for auto_whisper.audio_routing — Bluetooth → built-in override."""

import importlib

import pytest


# --- _is_bluetooth_input heuristic ---

@pytest.mark.parametrize("name", [
    "AirPods Pro",
    "AirPods",
    "Mantra's AirPods Max",
    "Bluetooth Headset",
    "Sony WH-1000XM5",
    "Sony WF-1000XM4",
    "Galaxy Buds Pro",
    "Beats Studio Pro",
    "Powerbeats Pro",
    "Bose QuietComfort",
    "Jabra Elite 7",
    "AIRPODS PRO",  # case-insensitive
])
def test_bluetooth_inputs_detected(name):
    from auto_whisper.audio_routing import _is_bluetooth_input
    assert _is_bluetooth_input(name) is True


@pytest.mark.parametrize("name", [
    "Built-in Microphone",
    "MacBook Pro Microphone",
    "External USB Mic",
    "Blue Yeti",
    "Shure SM7B",
    "iPhone Microphone",  # not Bluetooth even though it's a phone name
])
def test_non_bluetooth_inputs_pass_through(name):
    from auto_whisper.audio_routing import _is_bluetooth_input
    assert _is_bluetooth_input(name) is False


# --- PREFER_BUILTIN_MIC env parsing ---

def _reload_audio_routing():
    import auto_whisper.audio_routing
    importlib.reload(auto_whisper.audio_routing)


@pytest.mark.parametrize("env_value, expected", [
    ("1", True),
    ("0", False),
    ("", False),
    ("true", False),  # only literal "1" disables/enables
])
def test_prefer_builtin_flag_parsing(monkeypatch, env_value, expected):
    monkeypatch.setenv("AUTO_WHISPER_PREFER_BUILTIN_MIC", env_value)
    _reload_audio_routing()
    try:
        from auto_whisper.audio_routing import PREFER_BUILTIN_MIC
        assert PREFER_BUILTIN_MIC is expected
    finally:
        monkeypatch.delenv("AUTO_WHISPER_PREFER_BUILTIN_MIC", raising=False)
        _reload_audio_routing()


def test_prefer_builtin_default_true_when_unset(monkeypatch):
    """Critical: this must default ON. v5 launches with this fix active so
    AirPods users don't hit the HFP-degraded transcription path silently."""
    monkeypatch.delenv("AUTO_WHISPER_PREFER_BUILTIN_MIC", raising=False)
    _reload_audio_routing()
    from auto_whisper.audio_routing import PREFER_BUILTIN_MIC
    assert PREFER_BUILTIN_MIC is True


# --- resolve_input_device ---

def _patch_devices(monkeypatch, default_input: dict, all_devices: list[dict]):
    """Stub sd.query_devices(kind='input') and sd.query_devices() (no kind)."""
    import sounddevice as sd

    def fake_query(arg=None, kind=None):
        if kind == "input" and arg is None:
            return default_input
        if kind == "input" and isinstance(arg, int):
            return all_devices[arg]
        if arg is None and kind is None:
            return all_devices
        if isinstance(arg, int) and kind is None:
            return all_devices[arg]
        raise ValueError(f"unexpected query: arg={arg!r} kind={kind!r}")

    monkeypatch.setattr(sd, "query_devices", fake_query)


def test_user_selected_index_always_wins(monkeypatch):
    from auto_whisper.audio_routing import resolve_input_device

    # Even with prefer_builtin=True and an AirPods default, user-selected wins.
    _patch_devices(monkeypatch,
        default_input={"name": "AirPods Pro", "max_input_channels": 1},
        all_devices=[
            {"name": "Built-in Microphone", "max_input_channels": 1},
            {"name": "AirPods Pro", "max_input_channels": 1},
        ],
    )

    spec, reason = resolve_input_device(requested_index=1, prefer_builtin=True)
    assert spec == 1
    assert reason == "user-selected"


def test_default_used_when_not_bluetooth(monkeypatch):
    from auto_whisper.audio_routing import resolve_input_device

    _patch_devices(monkeypatch,
        default_input={"name": "Built-in Microphone", "max_input_channels": 1},
        all_devices=[{"name": "Built-in Microphone", "max_input_channels": 1}],
    )

    spec, reason = resolve_input_device(requested_index=None, prefer_builtin=True)
    assert spec is None
    assert "Built-in" in reason


def test_bluetooth_default_overridden_to_builtin(monkeypatch):
    """The whole point of this module: AirPods default → built-in override."""
    from auto_whisper.audio_routing import resolve_input_device

    _patch_devices(monkeypatch,
        default_input={"name": "AirPods Pro", "max_input_channels": 1},
        all_devices=[
            {"name": "AirPods Pro", "max_input_channels": 1},
            {"name": "Built-in Microphone", "max_input_channels": 1},
            {"name": "External Speaker", "max_input_channels": 0},  # output-only
        ],
    )

    spec, reason = resolve_input_device(requested_index=None, prefer_builtin=True)
    assert spec == 1  # index of built-in
    assert "BT override" in reason
    assert "Built-in" in reason


@pytest.mark.parametrize("internal_name", [
    "Built-in Microphone",
    "MacBook Air Microphone",  # ← real name on user's Mac (M-series)
    "MacBook Pro Microphone",
    "iMac Microphone",
    "Mac mini Microphone",
    "Mac Pro Microphone",
    "Studio Display Microphone",
])
def test_internal_mic_detected_across_models(monkeypatch, internal_name):
    """macOS names internal mics differently per model. The override must
    find any of them as the BT fallback."""
    from auto_whisper.audio_routing import resolve_input_device

    _patch_devices(monkeypatch,
        default_input={"name": "AirPods Pro", "max_input_channels": 1},
        all_devices=[
            {"name": "AirPods Pro", "max_input_channels": 1},
            {"name": internal_name, "max_input_channels": 1},
        ],
    )

    spec, reason = resolve_input_device(requested_index=None, prefer_builtin=True)
    assert spec == 1
    assert internal_name in reason


def test_bluetooth_default_kept_when_no_builtin(monkeypatch):
    """Edge case: external display / docked Mac with no built-in mic. We
    log a warning and stay on the Bluetooth default rather than fail to
    record at all."""
    from auto_whisper.audio_routing import resolve_input_device

    _patch_devices(monkeypatch,
        default_input={"name": "AirPods Pro", "max_input_channels": 1},
        all_devices=[
            {"name": "AirPods Pro", "max_input_channels": 1},
            {"name": "External Speaker", "max_input_channels": 0},
        ],
    )

    spec, reason = resolve_input_device(requested_index=None, prefer_builtin=True)
    assert spec is None
    assert "no built-in available" in reason


def test_prefer_builtin_off_keeps_bluetooth_default(monkeypatch):
    """User opted out — honor whatever the system default is."""
    from auto_whisper.audio_routing import resolve_input_device

    _patch_devices(monkeypatch,
        default_input={"name": "AirPods Pro", "max_input_channels": 1},
        all_devices=[
            {"name": "AirPods Pro", "max_input_channels": 1},
            {"name": "Built-in Microphone", "max_input_channels": 1},
        ],
    )

    spec, reason = resolve_input_device(requested_index=None, prefer_builtin=False)
    assert spec is None
    assert "AirPods" in reason


def test_resolve_handles_query_failure(monkeypatch):
    """sd.query_devices can raise (CoreAudio transient failure). Resolve
    should fall back to system default with a warning, not crash."""
    import sounddevice as sd

    from auto_whisper.audio_routing import resolve_input_device

    def boom(*a, **kw):
        raise RuntimeError("PortAudio not initialized")

    monkeypatch.setattr(sd, "query_devices", boom)
    spec, reason = resolve_input_device(requested_index=None, prefer_builtin=True)
    assert spec is None
    assert "inspection failed" in reason
