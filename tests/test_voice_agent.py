"""Tests for auto_whisper.voice_agent.synthesize() — Slice 4.1 refactor.

speak() is intentionally not unit-tested here — afplay subprocess + thread
playback is integration territory. Slice 4.1 only changed synthesize()'s
shape (extracted from speak), so we cover that.
"""

from pathlib import Path

import pytest


@pytest.fixture
def patched_backends(monkeypatch):
    """Stub all three backend writers so synthesize() never hits the network
    or invokes `say`. Each stub writes deterministic bytes to the path it's
    handed; `state["raise"]` lets a test force a backend to fail so we can
    exercise the fallback chain.
    """
    state: dict = {"raise": {}, "calls": []}

    def _make_writer(name: str, payload: bytes):
        def _writer(text: str, output: Path, voice: str = ""):
            state["calls"].append({"backend": name, "text": text, "voice": voice})
            if name in state["raise"]:
                raise state["raise"][name]
            output.write_bytes(payload)
        return _writer

    monkeypatch.setattr(
        "auto_whisper.voice_agent._speak_google", _make_writer("google", b"GOOGLE_MP3")
    )
    monkeypatch.setattr(
        "auto_whisper.voice_agent._speak_edge", _make_writer("edge", b"EDGE_MP3")
    )
    monkeypatch.setattr(
        "auto_whisper.voice_agent._speak_macos", _make_writer("macos", b"MACOS_AIFF")
    )
    return state


def test_synthesize_returns_bytes_and_ext(patched_backends):
    from auto_whisper.voice_agent import synthesize

    result = synthesize("hola", backend="edge")
    assert result == (b"EDGE_MP3", "mp3")


def test_synthesize_macos_returns_aiff(patched_backends):
    from auto_whisper.voice_agent import synthesize

    result = synthesize("hola", backend="macos")
    assert result == (b"MACOS_AIFF", "aiff")


def test_synthesize_returns_none_for_empty_text(patched_backends):
    from auto_whisper.voice_agent import synthesize

    assert synthesize("") is None
    assert synthesize("   ") is None


def test_synthesize_falls_back_to_edge_when_google_fails(patched_backends):
    from auto_whisper.voice_agent import synthesize

    patched_backends["raise"]["google"] = RuntimeError("no GCP creds")
    result = synthesize("hola", backend="google")
    assert result == (b"EDGE_MP3", "mp3")
    backends_tried = [c["backend"] for c in patched_backends["calls"]]
    assert backends_tried == ["google", "edge"]


def test_synthesize_falls_back_to_macos_when_edge_fails(patched_backends):
    from auto_whisper.voice_agent import synthesize

    patched_backends["raise"]["edge"] = RuntimeError("network")
    result = synthesize("hola", backend="edge")
    assert result == (b"MACOS_AIFF", "aiff")
    backends_tried = [c["backend"] for c in patched_backends["calls"]]
    assert backends_tried == ["edge", "macos"]


def test_synthesize_returns_none_when_all_backends_fail(patched_backends):
    from auto_whisper.voice_agent import synthesize

    patched_backends["raise"]["google"] = RuntimeError("g")
    patched_backends["raise"]["edge"] = RuntimeError("e")
    patched_backends["raise"]["macos"] = RuntimeError("m")
    assert synthesize("hola", backend="google") is None


def test_synthesize_unknown_backend_falls_back(patched_backends):
    """Unknown backend raises ValueError inside the try block, but no
    fallback exists for it (not in the fallbacks dict) so synthesize
    returns None."""
    from auto_whisper.voice_agent import synthesize

    assert synthesize("hola", backend="bogus") is None


def test_synthesize_passes_voice_to_backend(patched_backends):
    from auto_whisper.voice_agent import synthesize

    synthesize("hola", backend="edge", voice="es-ES-ElviraNeural")
    assert patched_backends["calls"][-1]["voice"] == "es-ES-ElviraNeural"


def test_synthesize_strips_markdown_before_calling_backend(patched_backends):
    """Markdown should be cleaned before reaching the backend writer —
    otherwise TTS reads asterisks and hashes literally."""
    from auto_whisper.voice_agent import synthesize

    synthesize("# Title\n\n**bold** text", backend="edge")
    text_passed = patched_backends["calls"][-1]["text"]
    assert "#" not in text_passed
    assert "**" not in text_passed
    assert "Title" in text_passed
    assert "bold" in text_passed
