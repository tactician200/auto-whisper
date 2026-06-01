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


# --- regression: filler between verb and language/tone word (the "pega con la
# instrucción" bug). The connector/filler regexes alone left "al inglés" stuck. ---

def test_strip_translate_filler_between_verb_and_language():
    a = va.get("translate")
    # "esto" sits between the verb and "al inglés" — must not survive.
    assert d._strip_leading_instruction(
        "tradúcelo esto al inglés tengo una reunión mañana", a
    ) == "tengo una reunión mañana"


def test_strip_translate_glued_pronoun_before_language():
    a = va.get("translate")
    # "tradúcemelo" → verb "tradúceme" + "lo"; the "lo al inglés" must go.
    assert d._strip_leading_instruction(
        "tradúcemelo al inglés que viene el cliente nuevo", a
    ) == "que viene el cliente nuevo"


def test_strip_translate_leaves_late_language_mention_intact():
    a = va.get("translate")
    # "inglés" appears late as real content → don't eat the preceding words.
    out = d._strip_leading_instruction("traduce no me gusta el inglés británico", a)
    assert "no me gusta el" in out


def test_strip_prompt_coding_drops_arma_un_prompt_para():
    a = va.get("prompt_coding")
    # The reported bug: "arma un prompt para hacer X" optimized the whole phrase.
    assert d._strip_leading_instruction(
        "arma un prompt para hacer una imagen tipo scandinava de santiago", a
    ) == "hacer una imagen tipo scandinava de santiago"


def test_strip_prompt_coding_crea_un_prompt_para():
    a = va.get("prompt_coding")
    assert d._strip_leading_instruction(
        "crea un prompt para implementar un parser de csv en python", a
    ) == "implementar un parser de csv en python"


def test_strip_tone_drops_tone_word_and_keeps_content():
    a = va.get("tone")
    assert d._strip_leading_instruction(
        "ponlo más amable este mensaje al proveedor", a
    ) == "este mensaje al proveedor"


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
