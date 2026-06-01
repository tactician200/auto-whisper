"""Tests for shared.intent_router — fast-path, classifier, fallback.

The Groq call (_call_llm) is mocked; the fast-path must never touch it.
"""

import shared.intent_router as ir


def test_short_circuit_under_6_words(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(ir, "_call_llm", lambda *a, **k: called.update(n=called["n"] + 1) or "{}")
    d = ir.route_intent("hola qué tal")
    assert d.action_id == "dictate" and d.source == "short_circuit"
    assert called["n"] == 0  # no LLM for short notes


def test_fastpath_translate_extracts_lang_no_llm(monkeypatch):
    monkeypatch.setattr(ir, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))
    d = ir.route_intent("traduce a inglés que mañana tengo una reunión importante")
    assert d.action_id == "translate"
    assert d.source == "verb_fastpath"
    assert d.confidence == 1.0
    assert d.params == {"target_lang": "English"}


def test_fastpath_translate_french(monkeypatch):
    monkeypatch.setattr(ir, "_call_llm", lambda *a, **k: "")
    d = ir.route_intent("tradúcelo al francés por favor para el cliente nuevo")
    assert d.action_id == "translate" and d.params["target_lang"] == "French"


def test_fastpath_tone_extracts_tone(monkeypatch):
    monkeypatch.setattr(ir, "_call_llm", lambda *a, **k: "")
    d = ir.route_intent("hazlo más formal que voy a mandárselo a mi jefe")
    assert d.action_id == "tone"
    assert d.params == {"tone": "formal"}


def test_fastpath_organize_no_params(monkeypatch):
    monkeypatch.setattr(ir, "_call_llm", lambda *a, **k: "")
    d = ir.route_intent("organiza estas ideas que tengo sueltas en la cabeza")
    assert d.action_id == "organize" and d.params == {}


def test_classifier_high_confidence_routes(monkeypatch):
    monkeypatch.setattr(ir, "_call_llm",
                        lambda *a, **k: '{"intent": "organize", "confidence": 0.9}')
    # No canonical verb at start, ≥6 words → goes to classifier.
    d = ir.route_intent("tengo estas ideas sueltas dando vueltas y quiero dejarlas presentables")
    assert d.action_id == "organize" and d.source == "classifier"
    assert d.confidence == 0.9


def test_classifier_summarize_now_hidden_falls_back(monkeypatch):
    # SUMMARIZE was cut from the exhibited surface → router must not produce it.
    monkeypatch.setattr(ir, "_call_llm",
                        lambda *a, **k: '{"intent": "summarize", "confidence": 0.95}')
    d = ir.route_intent("dame la versión corta de este correo larguísimo del proveedor externo")
    assert d.action_id == "dictate" and d.source == "fallback"


def test_classifier_low_confidence_falls_back_to_dictate(monkeypatch):
    monkeypatch.setattr(ir, "_call_llm",
                        lambda *a, **k: '{"intent": "summarize", "confidence": 0.3}')
    d = ir.route_intent("estaba pensando en muchas cosas distintas esta mañana temprano")
    assert d.action_id == "dictate" and d.source == "fallback"


def test_classifier_unknown_intent_falls_back(monkeypatch):
    monkeypatch.setattr(ir, "_call_llm",
                        lambda *a, **k: '{"intent": "frobnicate", "confidence": 0.99}')
    d = ir.route_intent("haz algo raro con este texto que no existe como acción")
    assert d.action_id == "dictate" and d.source == "fallback"


def test_classifier_non_exhibited_intent_falls_back(monkeypatch):
    # prompt_coding exists but is hidden — router must not produce it.
    monkeypatch.setattr(ir, "_call_llm",
                        lambda *a, **k: '{"intent": "prompt_coding", "confidence": 0.99}')
    d = ir.route_intent("implementa una función que haga el parseo del archivo csv")
    assert d.action_id == "dictate" and d.source == "fallback"


def test_classifier_malformed_json_falls_back(monkeypatch):
    monkeypatch.setattr(ir, "_call_llm", lambda *a, **k: "not json at all, sorry")
    d = ir.route_intent("este es un texto cualquiera de más de seis palabras hoy")
    assert d.action_id == "dictate" and d.source == "fallback"


def test_classifier_none_response_falls_back(monkeypatch):
    monkeypatch.setattr(ir, "_call_llm", lambda *a, **k: None)
    d = ir.route_intent("otro texto de relleno con suficientes palabras para pasar")
    assert d.action_id == "dictate" and d.source == "fallback"


def test_use_llm_false_fastpath_still_works(monkeypatch):
    # Smart-dictation hotkey path: canonical verb must still route without LLM.
    monkeypatch.setattr(ir, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))
    d = ir.route_intent("traduce a inglés que mañana tengo una reunión importante", use_llm=False)
    assert d.action_id == "translate" and d.source == "verb_fastpath"


def test_prompt_coding_via_explicit_verb_no_llm(monkeypatch):
    # "crea un prompt…" → prompt_coding by fast-path, even with use_llm=False
    # and even though prompt_coding is exhibited=False.
    monkeypatch.setattr(ir, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))
    d = ir.route_intent("crea un prompt para implementar el parser de csv del proyecto", use_llm=False)
    assert d.action_id == "prompt_coding" and d.source == "verb_fastpath"


def test_use_llm_false_no_verb_falls_back_without_llm(monkeypatch):
    # No canonical verb + classifier disabled → fallback, zero LLM round-trips.
    # (The daemon then hands this to classify_intent for the prompt path.)
    called = {"n": 0}
    monkeypatch.setattr(ir, "_call_llm", lambda *a, **k: called.update(n=called["n"] + 1) or "{}")
    d = ir.route_intent("necesito refactorizar el módulo de pagos para que maneje reintentos", use_llm=False)
    assert d.action_id == "dictate" and d.source == "fallback"
    assert called["n"] == 0


def test_parse_classifier_clamps_confidence():
    assert ir._parse_classifier('{"intent":"x","confidence":1.7}')[1] == 1.0
    assert ir._parse_classifier('{"intent":"x","confidence":-2}')[1] == 0.0
    assert ir._parse_classifier("garbage") is None
