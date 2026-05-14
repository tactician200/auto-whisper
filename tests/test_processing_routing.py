"""Tests for auto_whisper.processing_routing — flag-driven dispatcher."""

import importlib
from unittest.mock import MagicMock

import pytest


# --- USE_SERVICE_PROCESSING env parsing ---

def _reload_routing_and_facade() -> None:
    """Re-import processing_routing AND text_processor so the latter's
    re-export bindings stay in sync after env changes that force a reload."""
    import auto_whisper.processing_routing
    import auto_whisper.text_processor
    importlib.reload(auto_whisper.processing_routing)
    importlib.reload(auto_whisper.text_processor)


@pytest.mark.parametrize("env_value, expected", [
    ("1", True),
    ("0", False),
    ("", False),
    ("true", False),  # only "1" enables — explicit
    ("yes", False),
    ("on", False),
])
def test_flag_only_truthy_for_value_1(monkeypatch, env_value, expected):
    monkeypatch.setenv("AUTO_WHISPER_USE_SERVICE_PROCESSING", env_value)
    _reload_routing_and_facade()
    try:
        import auto_whisper.processing_routing
        assert auto_whisper.processing_routing.USE_SERVICE_PROCESSING is expected
    finally:
        monkeypatch.delenv("AUTO_WHISPER_USE_SERVICE_PROCESSING", raising=False)
        _reload_routing_and_facade()


def test_flag_default_false_when_unset(monkeypatch):
    monkeypatch.delenv("AUTO_WHISPER_USE_SERVICE_PROCESSING", raising=False)
    _reload_routing_and_facade()
    import auto_whisper.processing_routing
    assert auto_whisper.processing_routing.USE_SERVICE_PROCESSING is False


# --- Direct path (flag OFF) ---

def test_summarize_uses_direct_when_flag_off(monkeypatch):
    from auto_whisper import processing_routing

    monkeypatch.setattr(processing_routing, "USE_SERVICE_PROCESSING", False)

    direct_called = {"count": 0, "args": None}

    def fake_direct(text):
        direct_called["count"] += 1
        direct_called["args"] = text
        return "from-direct"

    monkeypatch.setattr(processing_routing._direct, "summarize", fake_direct)

    result = processing_routing.summarize("hello world")

    assert result == "from-direct"
    assert direct_called["count"] == 1
    assert direct_called["args"] == "hello world"


def test_explain_passes_for_voice_through_direct(monkeypatch):
    from auto_whisper import processing_routing

    monkeypatch.setattr(processing_routing, "USE_SERVICE_PROCESSING", False)

    captured = {}

    def fake_explain(text, for_voice=True):
        captured["text"] = text
        captured["for_voice"] = for_voice
        return "explained"

    monkeypatch.setattr(processing_routing._direct, "explain", fake_explain)

    processing_routing.explain("input", for_voice=False)
    assert captured["for_voice"] is False


@pytest.mark.parametrize("fn_name, mode_name", [
    ("summarize", "summarize"),
    ("organize_ideas", "organize_ideas"),
    ("optimize_prompt", "optimize_prompt"),
])
def test_each_mode_dispatches_through_direct(monkeypatch, fn_name, mode_name):
    from auto_whisper import processing_routing

    monkeypatch.setattr(processing_routing, "USE_SERVICE_PROCESSING", False)

    direct_called = {"yes": False}

    def fake(text, **kwargs):
        # **kwargs absorbs optional params like `emphasis` on optimize_prompt.
        direct_called["yes"] = True
        return f"direct-{mode_name}"

    monkeypatch.setattr(processing_routing._direct, fn_name, fake)
    fn = getattr(processing_routing, fn_name)
    result = fn("input")
    assert direct_called["yes"] is True
    assert result == f"direct-{mode_name}"


# --- Service path (flag ON) ---

def test_summarize_uses_service_when_flag_on(monkeypatch):
    from auto_whisper import processing_routing
    from auto_whisper import transcription

    monkeypatch.setattr(processing_routing, "USE_SERVICE_PROCESSING", True)

    fake_client = MagicMock()
    fake_client.process.return_value = {
        "result": "from-service", "mode": "summarize", "duration_s": 0.1
    }
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    result = processing_routing.summarize("input")
    assert result == "from-service"

    call_args = fake_client.process.call_args.args
    assert call_args == ("summarize", "input")


def test_explain_for_voice_routes_to_explain_mode_in_service(monkeypatch):
    from auto_whisper import processing_routing
    from auto_whisper import transcription

    monkeypatch.setattr(processing_routing, "USE_SERVICE_PROCESSING", True)

    fake_client = MagicMock()
    fake_client.process.return_value = {
        "result": "x", "mode": "explain", "duration_s": 0.0
    }
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    processing_routing.explain("input", for_voice=True)
    assert fake_client.process.call_args.args[0] == "explain"


def test_explain_paste_routes_to_explain_paste_mode_in_service(monkeypatch):
    from auto_whisper import processing_routing
    from auto_whisper import transcription

    monkeypatch.setattr(processing_routing, "USE_SERVICE_PROCESSING", True)

    fake_client = MagicMock()
    fake_client.process.return_value = {
        "result": "x", "mode": "explain_paste", "duration_s": 0.0
    }
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    processing_routing.explain("input", for_voice=False)
    assert fake_client.process.call_args.args[0] == "explain_paste"


def test_service_path_returns_none_when_client_returns_none(monkeypatch):
    from auto_whisper import processing_routing
    from auto_whisper import transcription

    monkeypatch.setattr(processing_routing, "USE_SERVICE_PROCESSING", True)

    fake_client = MagicMock()
    fake_client.process.return_value = None  # network/auth failure
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    assert processing_routing.summarize("x") is None


def test_service_path_returns_none_when_response_result_is_none(monkeypatch):
    """Underlying LLM returned None — service propagates result=None;
    dispatcher must propagate the same."""
    from auto_whisper import processing_routing
    from auto_whisper import transcription

    monkeypatch.setattr(processing_routing, "USE_SERVICE_PROCESSING", True)

    fake_client = MagicMock()
    fake_client.process.return_value = {
        "result": None, "mode": "summarize", "duration_s": 0.0
    }
    monkeypatch.setattr(transcription, "get_service_client", lambda: fake_client)

    assert processing_routing.summarize("x") is None


# --- text_processor backward-compat re-exports ---

def test_text_processor_reexports_dispatcher_functions():
    from auto_whisper import processing_routing, text_processor

    # Same function objects: the daemon's existing imports point at the
    # dispatcher, not at shared.processing directly.
    assert text_processor.summarize is processing_routing.summarize
    assert text_processor.explain is processing_routing.explain
    assert text_processor.organize_ideas is processing_routing.organize_ideas
    assert text_processor.optimize_prompt is processing_routing.optimize_prompt


def test_text_processor_reexports_constants():
    from auto_whisper import text_processor
    from shared import processing

    assert text_processor.MAX_INPUT_CHARS == processing.MAX_INPUT_CHARS
    assert text_processor.LLM_MODEL == processing.LLM_MODEL
    assert text_processor.MODES is processing.MODES
