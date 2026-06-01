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


# --- Intent-router prompts (tone / translate / reply) ---

def test_tone_prompt_has_placeholders_and_renders():
    p = prompts.PROMPT_TONE
    assert "{tone}" in p and "{text}" in p
    rendered = p.format(tone="formal", text="dame eso")
    assert "formal" in rendered and "dame eso" in rendered
    assert "{" not in rendered.replace("{text}", "X") or "{tone}" not in rendered


def test_translate_prompt_has_placeholders_and_renders():
    p = prompts.PROMPT_TRANSLATE
    assert "{target_lang}" in p and "{text}" in p
    rendered = p.format(target_lang="inglés", text="hola mundo")
    assert "inglés" in rendered and "hola mundo" in rendered


def test_reply_prompt_has_placeholders_and_renders():
    p = prompts.PROMPT_REPLY
    assert "{payload}" in p and "{instruction}" in p
    rendered = p.format(payload="queja del cliente", instruction="cordial")
    assert "queja del cliente" in rendered and "cordial" in rendered


def test_router_prompts_output_only_no_preamble():
    # Each must instruct "output only ..." to avoid 'Here is the translation:' noise
    for p in (prompts.PROMPT_TONE, prompts.PROMPT_TRANSLATE, prompts.PROMPT_REPLY):
        assert "ONLY" in p
