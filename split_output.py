#!/usr/bin/env python3
"""
Split text into voice (narrative) and display (data) channels.

Voice gets: paragraphs, explanations, summaries, short bullets.
Display gets: code blocks, tables, JSON, numbers, URLs, long lists.
"""

import re


def split_response(text: str) -> tuple[str, str]:
    """
    Split text into (voice_text, display_text).
    Returns both channels — voice for TTS, display for screen.
    """
    voice_parts = []
    display_parts = []

    in_code_block = False
    for line in text.split("\n"):
        stripped = line.strip()

        # Code blocks → display only
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            display_parts.append(line)
            continue
        if in_code_block:
            display_parts.append(line)
            continue

        # Tables → display only
        if "|" in stripped and stripped.startswith("|"):
            display_parts.append(line)
            continue

        # Table separator lines
        if re.match(r'^[\s|:-]+$', stripped):
            display_parts.append(line)
            continue

        # URLs → display only
        if re.search(r'https?://', stripped):
            display_parts.append(line)
            continue

        # JSON-like lines → display only
        if stripped.startswith("{") or stripped.startswith("[") or stripped.startswith('"'):
            display_parts.append(line)
            continue

        # Empty lines → both
        if not stripped:
            voice_parts.append("")
            display_parts.append("")
            continue

        # Everything else → both channels
        voice_parts.append(line)
        display_parts.append(line)

    voice_text = "\n".join(voice_parts).strip()
    display_text = "\n".join(display_parts).strip()

    # Clean up voice text for speech
    voice_text = _prepare_for_speech(voice_text)

    return voice_text, display_text


def _prepare_for_speech(text: str) -> str:
    """Clean text for natural speech output."""
    # Remove markdown headers (##)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    # Remove bullet markers
    text = re.sub(r'^[\s]*[-*]\s+', '', text, flags=re.MULTILINE)
    # Remove checkbox markers
    text = re.sub(r'\[[ x]\]\s*', '', text)
    # Remove emoji markers
    text = re.sub(r'[✅❓⚠️📌🔴◎◉⟳◠]', '', text)
    # Collapse multiple newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
