"""
Tests for shared.prompts — ensure templates are well-formed.

These guard against accidental edits that would break `.format(text=...)`
calls in text_processor.py / shared.processing.
"""

import pytest

from shared import prompts


ALL_PROMPTS = [
    prompts.PROMPT_SUMMARIZE,
    prompts.PROMPT_EXPLAIN_VOICE,
    prompts.PROMPT_EXPLAIN_PASTE,
    prompts.PROMPT_ORGANIZE,
    prompts.PROMPT_OPTIMIZE,
]


@pytest.mark.parametrize("prompt", ALL_PROMPTS)
def test_all_prompts_contain_text_placeholder(prompt):
    assert "{text}" in prompt, "prompt must have {text} slot for user input"


@pytest.mark.parametrize("prompt", ALL_PROMPTS)
def test_all_prompts_are_non_empty(prompt):
    assert prompt.strip(), "prompt must not be empty"
    assert len(prompt) > 50, "prompt suspiciously short — likely corrupted"


@pytest.mark.parametrize("prompt", ALL_PROMPTS)
def test_all_prompts_format_renders_cleanly(prompt):
    # PROMPT_OPTIMIZE accepts both {text} and {emphasis}; the rest only use
    # {text}. Pass emphasis="" so the universal-shape test covers both.
    rendered = prompt.format(text="USER_INPUT_HERE", emphasis="")
    assert "USER_INPUT_HERE" in rendered
    assert "{text}" not in rendered  # no unreplaced placeholders
    assert "{emphasis}" not in rendered
    assert "{" not in rendered or "}" not in rendered or not any(
        c in rendered for c in ("{text}", "{ text }", "{emphasis}")
    ), "stray format placeholder in rendered prompt"


def test_optimize_prompt_targets_claude_code_in_english():
    """PROMPT_OPTIMIZE is the only prompt that enforces English output."""
    assert "English" in prompts.PROMPT_OPTIMIZE
    assert "Claude Code" in prompts.PROMPT_OPTIMIZE
    # All 4 canonical sections are declared
    for section in ("## Context", "## Task", "## Details", "## Constraints"):
        assert section in prompts.PROMPT_OPTIMIZE


def test_summarize_prompt_targets_voice_output():
    assert "escuchar" in prompts.PROMPT_SUMMARIZE or "voz" in prompts.PROMPT_SUMMARIZE
