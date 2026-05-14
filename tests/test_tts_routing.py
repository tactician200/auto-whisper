"""Tests for auto_whisper.tts_routing — flag-driven TTS dispatcher (Slice 4.2)."""

import importlib

import pytest


# --- USE_SERVICE_TTS env parsing ---

def _reload_routing_and_voice_agent() -> None:
    """Re-import tts_routing AND voice_agent so the latter's lazy `speak`
    binding stays in sync after env changes that force a reload."""
    import auto_whisper.tts_routing
    import auto_whisper.voice_agent
    importlib.reload(auto_whisper.tts_routing)
    importlib.reload(auto_whisper.voice_agent)


@pytest.mark.parametrize("env_value, expected", [
    ("1", True),
    ("0", False),
    ("", False),
    ("true", False),  # only literal "1" enables — explicit
    ("yes", False),
    ("on", False),
])
def test_flag_only_truthy_for_value_1(monkeypatch, env_value, expected):
    monkeypatch.setenv("AUTO_WHISPER_USE_SERVICE_TTS", env_value)
    _reload_routing_and_voice_agent()
    try:
        import auto_whisper.tts_routing
        assert auto_whisper.tts_routing.USE_SERVICE_TTS is expected
    finally:
        monkeypatch.delenv("AUTO_WHISPER_USE_SERVICE_TTS", raising=False)
        _reload_routing_and_voice_agent()


def test_flag_default_false_when_unset(monkeypatch):
    monkeypatch.delenv("AUTO_WHISPER_USE_SERVICE_TTS", raising=False)
    _reload_routing_and_voice_agent()
    import auto_whisper.tts_routing
    assert auto_whisper.tts_routing.USE_SERVICE_TTS is False


# --- Direct path (flag OFF) ---

def test_speak_uses_local_when_flag_off(monkeypatch):
    from auto_whisper import tts_routing, voice_agent

    monkeypatch.setattr(tts_routing, "USE_SERVICE_TTS", False)

    captured = {"called": False, "args": None}

    def fake_local(text, backend, voice, block):
        captured["called"] = True
        captured["args"] = (text, backend, voice, block)

    monkeypatch.setattr(voice_agent, "_speak_local", fake_local)

    tts_routing.speak("hola", backend="edge", voice="es-ES-X", block=True)

    assert captured["called"] is True
    assert captured["args"] == ("hola", "edge", "es-ES-X", True)


def test_speak_local_resolves_default_backend_when_none(monkeypatch):
    """When caller passes backend=None and flag is OFF, the dispatcher should
    pass voice_agent's DEFAULT_BACKEND to _speak_local (which has it as a
    default but the dispatcher resolves explicitly to keep behavior stable
    if the local default ever changes)."""
    from auto_whisper import tts_routing, voice_agent

    monkeypatch.setattr(tts_routing, "USE_SERVICE_TTS", False)

    captured = {}

    def fake_local(text, backend, voice, block):
        captured["backend"] = backend

    monkeypatch.setattr(voice_agent, "_speak_local", fake_local)

    tts_routing.speak("hola")
    assert captured["backend"] == voice_agent.DEFAULT_BACKEND


def test_speak_does_not_call_service_client_when_flag_off(monkeypatch):
    from auto_whisper import tts_routing, transcription, voice_agent

    monkeypatch.setattr(tts_routing, "USE_SERVICE_TTS", False)
    monkeypatch.setattr(voice_agent, "_speak_local", lambda *a, **kw: None)

    def boom():
        raise AssertionError("service client must not be touched on flag OFF")

    monkeypatch.setattr(transcription, "get_service_client", boom)

    tts_routing.speak("hola")  # must not raise


# --- Service path (flag ON) ---

def test_speak_uses_service_when_flag_on(monkeypatch):
    from unittest.mock import MagicMock

    from auto_whisper import tts_routing, transcription, voice_agent

    monkeypatch.setattr(tts_routing, "USE_SERVICE_TTS", True)

    fake_client = MagicMock()
    fake_client.tts.return_value = (b"AUDIO_BYTES", "mp3")
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    play_calls = []
    monkeypatch.setattr(
        voice_agent, "_play_bytes",
        lambda audio, ext, block=True: play_calls.append((audio, ext, block)),
    )

    tts_routing.speak("hola", backend="macos", voice="Mónica", block=False)

    fake_client.tts.assert_called_once_with("hola", backend="macos", voice="Mónica")
    assert play_calls == [(b"AUDIO_BYTES", "mp3", False)]


def test_speak_via_service_passes_none_backend_to_let_service_pick(monkeypatch):
    """When flag is ON and caller didn't specify backend, we send None on the
    wire so the service uses its own default (currently 'edge')."""
    from unittest.mock import MagicMock

    from auto_whisper import tts_routing, transcription, voice_agent

    monkeypatch.setattr(tts_routing, "USE_SERVICE_TTS", True)
    fake_client = MagicMock()
    fake_client.tts.return_value = (b"x", "mp3")
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)
    monkeypatch.setattr(voice_agent, "_play_bytes", lambda *a, **kw: None)

    tts_routing.speak("hola")
    fake_client.tts.assert_called_once_with("hola", backend=None, voice=None)


def test_speak_silently_returns_when_service_returns_none(monkeypatch):
    """Service unreachable / auth fail / 5xx → ServiceClient.tts returns None.
    Dispatcher must not invoke playback in that case (no audio + no exception)."""
    from unittest.mock import MagicMock

    from auto_whisper import tts_routing, transcription, voice_agent

    monkeypatch.setattr(tts_routing, "USE_SERVICE_TTS", True)
    fake_client = MagicMock()
    fake_client.tts.return_value = None
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    play_calls = []
    monkeypatch.setattr(
        voice_agent, "_play_bytes",
        lambda *a, **kw: play_calls.append(a),
    )

    tts_routing.speak("hola")
    assert play_calls == []


# --- Privacy mode override (Slice 4.3) ---

def test_privacy_mode_forces_backend_macos_local_path(monkeypatch):
    """Flag OFF + privacy mode ON: caller asks for edge, dispatcher hands
    'macos' to _speak_local."""
    from auto_whisper import tts_routing, voice_agent

    monkeypatch.setenv("AUTO_WHISPER_PRIVACY_MODE", "1")
    monkeypatch.setattr(tts_routing, "USE_SERVICE_TTS", False)

    captured = {}

    def fake_local(text, backend, voice, block):
        captured["backend"] = backend

    monkeypatch.setattr(voice_agent, "_speak_local", fake_local)

    tts_routing.speak("hola", backend="edge")
    assert captured["backend"] == "macos"


def test_privacy_mode_forces_backend_macos_service_path(monkeypatch):
    """Flag ON + privacy mode ON: dispatcher sends backend='macos' on the
    wire so the service synthesizes via offline `say`."""
    from unittest.mock import MagicMock

    from auto_whisper import tts_routing, transcription, voice_agent

    monkeypatch.setenv("AUTO_WHISPER_PRIVACY_MODE", "1")
    monkeypatch.setattr(tts_routing, "USE_SERVICE_TTS", True)

    fake_client = MagicMock()
    fake_client.tts.return_value = (b"x", "aiff")
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)
    monkeypatch.setattr(voice_agent, "_play_bytes", lambda *a, **kw: None)

    tts_routing.speak("hola", backend="edge")
    fake_client.tts.assert_called_once_with("hola", backend="macos", voice=None)


def test_privacy_mode_off_keeps_caller_backend(monkeypatch):
    """Sanity: when privacy is off, caller's backend choice is preserved."""
    from auto_whisper import tts_routing, voice_agent

    monkeypatch.delenv("AUTO_WHISPER_PRIVACY_MODE", raising=False)
    monkeypatch.setattr(tts_routing, "USE_SERVICE_TTS", False)

    captured = {}
    monkeypatch.setattr(
        voice_agent, "_speak_local",
        lambda text, backend, voice, block: captured.update(backend=backend),
    )

    tts_routing.speak("hola", backend="edge")
    assert captured["backend"] == "edge"


# --- voice_agent re-export wiring ---

def test_voice_agent_speak_routes_through_dispatcher(monkeypatch):
    """Existing daemon callers do `from auto_whisper.voice_agent import speak`.
    That entry point must dispatch through tts_routing so the flag is honored."""
    from auto_whisper import tts_routing, voice_agent

    captured = {"called": False}

    def fake_route(text, backend, voice, block):
        captured["called"] = True
        captured["args"] = (text, backend, voice, block)

    monkeypatch.setattr(tts_routing, "speak", fake_route)

    voice_agent.speak("hola", backend="edge", voice=None, block=True)
    assert captured["called"] is True
    assert captured["args"] == ("hola", "edge", None, True)
