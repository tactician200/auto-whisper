"""Tests for the Gemini client singleton and _call_gemini contract.

No network: the google-genai client is mocked. Mirrors test_anthropic_client.
"""

from unittest.mock import MagicMock

import pytest

import shared.gemini_client as gc
import shared.processing as processing


@pytest.fixture(autouse=True)
def _reset_singleton():
    gc._gemini_client = None
    yield
    gc._gemini_client = None


def _mock_gemini_response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    return response


def test_singleton_returns_same_instance(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(gc, "GEMINI_API_KEY_DICTATION", "test-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **kw: fake)

    first = gc.get_gemini_client()
    second = gc.get_gemini_client()
    assert first is second is fake


def test_call_gemini_returns_none_without_key(monkeypatch):
    monkeypatch.setattr("shared.config.GEMINI_API_KEY_DICTATION", "")
    assert processing._call_gemini("hola", 100) is None


def test_call_gemini_extracts_text_and_passes_config(monkeypatch):
    monkeypatch.setattr("shared.config.GEMINI_API_KEY_DICTATION", "test-key")
    monkeypatch.setattr("shared.config.GEMINI_MODEL", "gemini-2.5-flash")
    client = MagicMock()
    client.models.generate_content.return_value = _mock_gemini_response("hello back")
    monkeypatch.setattr("shared.gemini_client.get_gemini_client", lambda: client)

    out = processing._call_gemini("responde esto", 200, system="You reply.", temperature=0.3)
    assert out == "hello back"
    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-2.5-flash"
    assert kwargs["contents"] == "responde esto"
    cfg = kwargs["config"]
    assert cfg.system_instruction == "You reply."
    assert cfg.max_output_tokens == 200
    assert cfg.temperature == 0.3


def test_call_gemini_none_on_exception(monkeypatch):
    monkeypatch.setattr("shared.config.GEMINI_API_KEY_DICTATION", "test-key")
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError("boom")
    monkeypatch.setattr("shared.gemini_client.get_gemini_client", lambda: client)
    assert processing._call_gemini("x", 100) is None


# --- reply engine chain: gemini → claude ---

def test_reply_chain_uses_gemini_first(monkeypatch):
    monkeypatch.setattr(processing, "_call_gemini",
                        lambda p, m, system=None, temperature=None: "GEMINI")
    monkeypatch.setattr(processing, "_call_claude",
                        lambda p, m, system=None, temperature=None: "CLAUDE")
    assert processing._call_llm("hi", 100, engine=("gemini", "claude")) == "GEMINI"


def test_reply_chain_falls_back_to_claude(monkeypatch):
    monkeypatch.setattr(processing, "_call_gemini",
                        lambda p, m, system=None, temperature=None: None)  # gemini down
    monkeypatch.setattr(processing, "_call_claude",
                        lambda p, m, system=None, temperature=None: "CLAUDE")
    assert processing._call_llm("hi", 100, engine=("gemini", "claude")) == "CLAUDE"


def test_reply_chain_none_when_all_fail(monkeypatch):
    monkeypatch.setattr(processing, "_call_gemini",
                        lambda p, m, system=None, temperature=None: None)
    monkeypatch.setattr(processing, "_call_claude",
                        lambda p, m, system=None, temperature=None: None)
    assert processing._call_llm("hi", 100, engine=("gemini", "claude")) is None


def test_reply_message_uses_gemini_claude_chain():
    assert processing.REPLY_ENGINE_CHAIN == ("gemini", "claude")
