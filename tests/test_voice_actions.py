"""Tests for the voice action registry and canonical-verb fast-path."""

import shared.voice_actions as va


def test_get_known_and_unknown():
    assert va.get("translate").id == "translate"
    assert va.get("nope") is None


def test_exhibited_excludes_advanced_summarize_and_dictate():
    ids = {a.id for a in va.exhibited_actions()}
    # SUMMARIZE cut from MVP surface; advanced + dictate never exhibited.
    assert ids == {"translate", "tone", "organize", "reply"}
    assert "summarize" not in ids
    assert "prompt_coding" not in ids
    assert "dictate" not in ids


def test_match_translate():
    a = va.match_canonical_verb("traduce a inglés que tengo una reunión mañana")
    assert a is not None and a.id == "translate"


def test_match_tone():
    a = va.match_canonical_verb("hazlo más formal por favor")
    assert a is not None and a.id == "tone"


def test_match_reply_longest_wins_over_responde():
    # "responde a esto" must beat the shorter "responde" verb on the same action,
    # and both belong to reply anyway.
    a = va.match_canonical_verb("responde a esto que me mandó el cliente")
    assert a is not None and a.id == "reply"


def test_match_prompt_coding_on_explicit_verb():
    # exhibited=False, but an explicit "crea un prompt" must still route to it.
    a = va.match_canonical_verb("crea un prompt para refactorizar el módulo de pagos")
    assert a is not None and a.id == "prompt_coding"
    b = va.match_canonical_verb("optimiza en prompt esto que voy a dictar ahora")
    assert b is not None and b.id == "prompt_coding"


def test_summarize_is_inert_no_fastpath():
    # SUMMARIZE was cut: no verbs → "resume esto" must NOT fast-path to it.
    assert va.match_canonical_verb("resume esto que es muy largo para leer") is None


def test_no_match_returns_none():
    assert va.match_canonical_verb("hola qué tal cómo estás hoy") is None
    assert va.match_canonical_verb("") is None


def test_match_is_anchored_at_start():
    # A verb appearing mid-sentence must NOT trigger the fast-path.
    assert va.match_canonical_verb("ayer fui a la oficina y traduje un texto") is None


def test_translate_and_reply_need_payload():
    assert va.get("translate").needs_payload is True
    assert va.get("reply").needs_payload is True
    assert va.get("organize").needs_payload is False


def test_engine_assignment_matches_hybrid_plan():
    # Research-curated: only REPLY justifies Claude; the rest run on Groq.
    assert va.get("translate").engine == "groq"
    assert va.get("tone").engine == "groq"
    assert va.get("organize").engine == "groq"
    assert va.get("reply").engine == "claude"


def test_reused_modes_already_in_MODES():
    # organize/summarize map to existing wrappers; the new claude modes
    # (translate/adjust_tone/reply_message) are registered in Fase 2.
    from shared.processing import MODES
    assert va.get("organize").mode in MODES
    assert va.get("summarize").mode in MODES
