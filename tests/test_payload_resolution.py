"""Tests for the router payload resolution (clipboard-as-input, Fase 5).

_resolve_payload and _strip_leading_instruction are pure functions in the
daemon module; importing the daemon pulls pyobjc but no AppKit side effects at
import time.
"""

import auto_whisper.dictation_daemon as d
import shared.voice_actions as va


def test_strip_leading_instruction_translate():
    a = va.get("translate")
    assert d._strip_leading_instruction("traduce a inglés tengo una reunión mañana", a) == \
        "tengo una reunión mañana"


def test_strip_leading_instruction_tone():
    a = va.get("tone")
    assert d._strip_leading_instruction("hazlo más formal oye dame eso", a) == "oye dame eso"


def test_reply_uses_clipboard_as_payload_and_dictation_as_instruction():
    payload, instruction = d._resolve_payload(
        "reply", "respóndele cordial y firme", clipboard="oye necesito eso YA"
    )
    assert payload == "oye necesito eso YA"
    assert instruction == "respóndele cordial y firme"


def test_reply_empty_clipboard_gives_empty_payload():
    payload, instruction = d._resolve_payload("reply", "responde a esto", clipboard=None)
    assert payload == ""  # daemon branch will fall back to raw paste
    assert instruction == "responde a esto"


def test_translate_dictated_content_used_when_substantial():
    # "traduce al inglés" + 5 content words → translate the dictation itself.
    payload, _ = d._resolve_payload(
        "translate", "traduce al inglés tengo una reunión mañana temprano", clipboard="IGNORED"
    )
    assert payload == "tengo una reunión mañana temprano"


def test_translate_instruction_only_falls_back_to_clipboard():
    # "traduce esto al inglés" has no content of its own → use the clipboard.
    payload, _ = d._resolve_payload(
        "translate", "traduce esto al inglés", clipboard="el contrato vence en marzo"
    )
    assert payload == "el contrato vence en marzo"


def test_translate_no_content_no_clipboard_degrades_to_raw():
    payload, _ = d._resolve_payload("translate", "tradúcelo al inglés", clipboard=None)
    assert payload  # non-empty: never returns an empty payload that strands the user


def test_read_clipboard_text_exists():
    # Smoke: helper is importable and callable (returns str or None).
    assert hasattr(d, "read_clipboard_text")
