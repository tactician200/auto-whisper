"""
Unit tests for auto_whisper.text_processor.

Verify: prompt construction, input truncation, max_tokens routing,
failure handling. All Groq calls mocked — zero network I/O.
"""

from unittest.mock import MagicMock

import pytest

from auto_whisper.text_processor import (
    DEFAULT_MAX_COMPLETION_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
    MAX_INPUT_CHARS,
    OPTIMIZE_MAX_COMPLETION_TOKENS,
    explain,
    optimize_prompt,
    organize_ideas,
    summarize,
)
from tests.fixtures.dictation_samples import SAMPLES


# --- summarize ---

def test_summarize_calls_groq_with_summarize_prompt(mock_groq_client, captured_prompt):
    summarize(SAMPLES["summarize_long_es"])
    prompt = captured_prompt()
    assert "Resume en 2-3 frases" in prompt
    assert SAMPLES["summarize_long_es"] in prompt


def test_summarize_returns_response_text(mock_groq_client, mock_groq_response):
    mock_groq_client.chat.completions.create.return_value = mock_groq_response("Resumen corto.")
    result = summarize(SAMPLES["summarize_long_es"])
    assert result == "Resumen corto."


def test_summarize_truncates_input_to_max_chars(mock_groq_client, captured_prompt):
    summarize(SAMPLES["truncation_edge"])  # 5000 chars of "a"
    prompt = captured_prompt()
    # The raw 5000-char input should NOT appear; only first MAX_INPUT_CHARS.
    assert "a" * MAX_INPUT_CHARS in prompt
    assert "a" * (MAX_INPUT_CHARS + 1) not in prompt


def test_summarize_uses_default_max_tokens(mock_groq_client):
    summarize(SAMPLES["short_dictation"])
    call = mock_groq_client.chat.completions.create.call_args
    assert call.kwargs["max_completion_tokens"] == DEFAULT_MAX_COMPLETION_TOKENS


def test_summarize_returns_none_when_no_api_key(monkeypatch):
    monkeypatch.setattr("shared.processing.GROQ_API_KEY_DICTATION", "")
    assert summarize("any text") is None


def test_summarize_returns_none_on_groq_exception(mock_groq_client):
    mock_groq_client.chat.completions.create.side_effect = RuntimeError("boom")
    assert summarize(SAMPLES["short_dictation"]) is None


# --- explain ---

def test_explain_defaults_to_voice_prompt(mock_groq_client, captured_prompt):
    explain(SAMPLES["explain_target_technical"])
    prompt = captured_prompt()
    assert "en voz alta" in prompt  # PROMPT_EXPLAIN_VOICE marker
    assert "bullets" not in prompt  # paste prompt would have this


def test_explain_paste_uses_paste_prompt(mock_groq_client, captured_prompt):
    explain(SAMPLES["explain_target_technical"], for_voice=False)
    prompt = captured_prompt()
    assert "colega senior" in prompt  # PROMPT_EXPLAIN_PASTE marker
    assert "voz alta" not in prompt


# --- organize_ideas ---

def test_organize_ideas_calls_groq_with_organize_prompt(mock_groq_client, captured_prompt):
    organize_ideas(SAMPLES["organize_ideas_mixed"])
    prompt = captured_prompt()
    assert "Limpia texto dictado" in prompt
    assert SAMPLES["organize_ideas_mixed"] in prompt


def test_organize_ideas_preserves_chilean_input(mock_groq_client, captured_prompt):
    organize_ideas(SAMPLES["chilean_casual"])
    prompt = captured_prompt()
    # Chilean idioms pass through to the LLM as-is; prompt doesn't neutralize them.
    assert "cachai" in prompt
    assert "po" in prompt


# --- optimize_prompt ---

def test_optimize_prompt_uses_optimize_prompt_template(mock_groq_client, captured_prompt):
    optimize_prompt(SAMPLES["code_refactor_es"])
    prompt = captured_prompt()
    assert "prompt engineer" in prompt
    assert "Restructure" in prompt
    assert "## Context" in prompt
    assert "## Task" in prompt


def test_optimize_prompt_uses_smaller_max_tokens(mock_groq_client):
    optimize_prompt(SAMPLES["code_refactor_es"])
    call = mock_groq_client.chat.completions.create.call_args
    assert call.kwargs["max_completion_tokens"] == OPTIMIZE_MAX_COMPLETION_TOKENS
    assert OPTIMIZE_MAX_COMPLETION_TOKENS < DEFAULT_MAX_COMPLETION_TOKENS


def test_optimize_prompt_preserves_full_dictation(mock_groq_client, captured_prompt):
    optimize_prompt(SAMPLES["code_refactor_es"])
    prompt = captured_prompt()
    # Optimize restructures but preserves ALL input content; verify input reaches LLM.
    assert SAMPLES["code_refactor_es"] in prompt


# --- shared LLM call parameters ---

@pytest.mark.parametrize("fn", [summarize, lambda t: explain(t), organize_ideas, optimize_prompt])
def test_all_functions_use_correct_model_and_temperature(mock_groq_client, fn):
    fn(SAMPLES["short_dictation"])
    call = mock_groq_client.chat.completions.create.call_args
    assert call.kwargs["model"] == LLM_MODEL
    assert call.kwargs["temperature"] == LLM_TEMPERATURE


@pytest.mark.parametrize("fn", [summarize, lambda t: explain(t), organize_ideas, optimize_prompt])
def test_all_functions_strip_whitespace_from_response(mock_groq_client, mock_groq_response, fn):
    mock_groq_client.chat.completions.create.return_value = mock_groq_response("  hello  \n")
    assert fn(SAMPLES["short_dictation"]) == "hello"
