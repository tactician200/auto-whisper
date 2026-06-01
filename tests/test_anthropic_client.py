"""Tests for the Anthropic client singleton and _call_claude contract.

No network: the Anthropic SDK client is mocked. Mirrors the Groq mocking
style in conftest.py.
"""

from unittest.mock import MagicMock

import pytest

import shared.anthropic_client as ac
import shared.processing as processing


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test starts with a fresh (un-instantiated) client singleton."""
    ac._anthropic_client = None
    yield
    ac._anthropic_client = None


def _mock_claude_response(text: str) -> MagicMock:
    """Build a mock messages.create() response: content=[TextBlock(text=...)]."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def test_singleton_returns_same_instance(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(ac, "ANTHROPIC_API_KEY_DICTATION", "test-key")
    # Patch the lazily-imported Anthropic constructor.
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: fake)

    first = ac.get_anthropic_client()
    second = ac.get_anthropic_client()
    assert first is second is fake


def test_call_claude_returns_none_without_key(monkeypatch):
    monkeypatch.setattr("shared.config.ANTHROPIC_API_KEY_DICTATION", "")
    assert processing._call_claude("hola", 100) is None


def test_call_claude_extracts_text(monkeypatch):
    monkeypatch.setattr("shared.config.ANTHROPIC_API_KEY_DICTATION", "test-key")
    monkeypatch.setattr("shared.config.CLAUDE_MODEL", "claude-haiku-4-5")
    client = MagicMock()
    client.messages.create.return_value = _mock_claude_response("hello world")
    monkeypatch.setattr("shared.anthropic_client.get_anthropic_client", lambda: client)

    out = processing._call_claude("traduce esto", 200, system="You translate.")
    assert out == "hello world"
    # system passed through; user prompt in messages
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"] == "You translate."
    assert kwargs["messages"][0]["content"] == "traduce esto"
    assert kwargs["model"] == "claude-haiku-4-5"


def test_call_claude_none_on_exception(monkeypatch):
    monkeypatch.setattr("shared.config.ANTHROPIC_API_KEY_DICTATION", "test-key")
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("boom")
    monkeypatch.setattr("shared.anthropic_client.get_anthropic_client", lambda: client)
    assert processing._call_claude("x", 100) is None


def test_call_llm_routes_to_groq_by_default(monkeypatch):
    monkeypatch.setattr(processing, "_call_groq", lambda p, m, temperature=None: "GROQ")
    monkeypatch.setattr(processing, "_call_claude",
                        lambda p, m, system=None, temperature=None: "CLAUDE")
    assert processing._call_llm("hi", 100) == "GROQ"
    assert processing._call_llm("hi", 100, engine="claude") == "CLAUDE"
    assert processing._call_llm("hi", 100, temperature=0.3) == "GROQ"
