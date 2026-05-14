"""Regression tests for shared.processing.classify_intent.

Every heuristic path is parametrized so adding a new signal means adding a
case here (and probably extending the signal table in shared.processing).

The LLM fallback path is exercised by monkeypatching _call_groq — no
network I/O. Tests focus on the contract:

- Each heuristic returns the right intent for at least one canonical input.
- The order-of-checks invariant holds (coding > writing > decision >
  research) — verified by composite inputs that match two tables.
- The LLM fallback respects _VALID_INTENTS and degrades to "raw" on garbage.

These tests do NOT validate the actual semantic quality of each intent;
that's a manual smoke-test concern. The point is to catch regressions when
someone edits a signal table or reorders the checks.
"""

from unittest.mock import patch

import pytest

from shared.processing import (
    _CODING_SIGNALS,
    _DECISION_SIGNALS,
    _RESEARCH_SIGNALS,
    _VALID_INTENTS,
    _WRITING_SIGNALS,
    classify_intent,
)


# --- raw: too short ---

@pytest.mark.parametrize("text", [
    "",
    "  ",
    "hola",
    "hola que tal",
    "uno dos tres cuatro cinco",   # exactly 5 words
])
def test_short_input_returns_raw(text):
    assert classify_intent(text) == "raw"


# --- prompt_coding ---

@pytest.mark.parametrize("text", [
    "implementa una función que valide el email del usuario antes de guardarlo",
    "implement a retry loop in the http client when status is 429",
    "fix the bug where the daemon crashes on bluetooth disconnect during recording",
    "refactor the audio routing module so the override logic is testable",
    "write a script that converts wav files to mp3 with ffmpeg in a loop",
    "crea un script para limpiar los logs viejos en var log auto-whisper",
    "build a fastapi endpoint that streams the transcription back as it lands",
    "necesito una function que devuelva el path del binario de whisper",
    "abrí el archivo main.py y revisá la función handle_event",   # .py trigger
    "tenés que cambiar el class WhisperRunner para que acepte un timeout",
])
def test_coding_signals_route_to_prompt_coding(text):
    assert classify_intent(text) == "prompt_coding"


# --- prompt_writing ---

@pytest.mark.parametrize("text", [
    "escribe un email a la gestoría avisando que mandamos las claves prontas",
    "escribe un mensaje al cliente diciéndole que el reporte está listo",
    "redacta un texto breve para anunciar el cambio de horario del lunes",
    "necesito un texto corto para el aviso de cierre por vacaciones de julio",
    "post de linkedin sobre el lanzamiento del nuevo producto de la semana",
    "post sobre la conferencia donde hablé del modelo de pricing en SaaS",
    "artículo sobre cómo migramos de heroku a fly en una tarde de viernes",
    "write an email to the legal team confirming the contract changes for friday",
    "draft a message to juan letting him know we move the meeting to four pm",
    "para publicar en el blog corporativo el lunes a primera hora del día",
    "tweet sobre el evento que hicimos en bilbao con la comunidad local",
    "comunicado interno para el equipo sobre la nueva política de viáticos",
])
def test_writing_signals_route_to_prompt_writing(text):
    assert classify_intent(text) == "prompt_writing"


# --- decision_making ---

@pytest.mark.parametrize("text", [
    "debo decidir entre quedarme con groq o moverme a deepgram pagado",
    "qué elijo para el deploy: vercel, fly, o railway en el corto plazo",
    "decisión entre py2app y pyinstaller para empacar la app de menubar",
    "should i migrate the backend from express to fastify before launch",
    "vale la pena migrar a deepgram si pago un poco mas por mejor accuracy",
    "me conviene contratar un freelance o hacer crecer el equipo interno",
    "elegir entre claude opus o sonnet para el agente de soporte",
    "decidir entre dos vendors de tts antes del lanzamiento del jueves",
    "pros y contras de empacar con pyinstaller versus py2app para esto",
    "ventajas y desventajas de seguir con groq versus llevar todo on-prem",
])
def test_decision_signals_route_to_decision_making(text):
    assert classify_intent(text) == "decision_making"


# --- research ---

@pytest.mark.parametrize("text", [
    "investiga las mejores APIs de transcripción en streaming en 2026",
    "investigar el estado actual de los modelos open-weight para spanish",
    "busca info sobre las nuevas pricing tiers de groq y openai esta semana",
    "busca información sobre apple silicon y la compatibilidad de portaudio",
    "compara claude api vs openai api para mi caso de uso de dictado largo",
    "comparar los costos de fly io versus render para una app fastapi chica",
    "estado del arte de la transcripción local con apple neural engine",
    "qué se sabe de macos 26 sobre cambios en CGEvent post tahoe ya",
    "que se sabe de las restricciones nuevas de gatekeeper en sequoia",
    "alternativas a edge tts que no requieran una cuenta de microsoft activa",
    "alternativas para sounddevice en python que no necesiten portaudio raro",
])
def test_research_signals_route_to_research(text):
    assert classify_intent(text) == "research"


# --- order-of-checks invariants ---

def test_coding_wins_over_research_when_both_match():
    # "compara" → research; ".py" → coding. Coding is checked first.
    text = "compara los dos archivos legacy.py y new.py en cuanto a errores"
    assert classify_intent(text) == "prompt_coding"


def test_decision_wins_over_research_when_both_match():
    # "pros y contras" → decision; "alternativas a" → research. Decision wins.
    text = "pros y contras de las alternativas a groq para mi caso de uso"
    assert classify_intent(text) == "decision_making"


def test_writing_wins_over_decision_when_both_match():
    # "escribe un email" → writing; "pros y contras" → decision. Writing wins
    # because writing is checked before decision.
    text = "escribe un email que liste los pros y contras de cada vendor"
    assert classify_intent(text) == "prompt_writing"


# --- LLM fallback path (mocked) ---

def _mk_call_groq_mock(return_value: str | None):
    """Patch shared.processing._call_groq to return a fixed string."""
    return patch("shared.processing._call_groq", return_value=return_value)


def test_llm_returns_valid_intent_is_passed_through():
    # No heuristic matches → LLM is called → returns "organize"
    text = "estaba pensando en cómo armar el flujo, primero a y después b y al final c también"
    with _mk_call_groq_mock("organize"):
        assert classify_intent(text) == "organize"


def test_llm_returns_None_falls_back_to_raw():
    text = "estaba pensando en la idea esa que hablamos ayer en la cena tarde"
    with _mk_call_groq_mock(None):
        assert classify_intent(text) == "raw"


def test_llm_returns_garbage_falls_back_to_raw():
    text = "estaba pensando en la idea esa que hablamos ayer en la cena tarde"
    with _mk_call_groq_mock("???not_a_real_intent???"):
        assert classify_intent(text) == "raw"


def test_llm_returns_legacy_prompt_maps_to_prompt_coding():
    # Older classifier responses returned bare "prompt"; we keep that
    # backwards-compat mapping.
    text = "estaba pensando en la idea esa que hablamos ayer en la cena tarde"
    with _mk_call_groq_mock("prompt"):
        assert classify_intent(text) == "prompt_coding"


@pytest.mark.parametrize("noisy", [
    "raw.",
    "RAW",
    "  research  ",
    "decision_making\n",
    "Organize!",
])
def test_llm_output_is_normalised(noisy):
    # Trailing punctuation, casing, whitespace, etc. are all stripped.
    text = "estaba pensando en la idea esa que hablamos ayer en la cena tarde"
    with _mk_call_groq_mock(noisy):
        result = classify_intent(text)
        # Whatever it normalises to, it must be a valid intent — never garbage.
        assert result in _VALID_INTENTS


# --- signal table sanity ---

def test_signal_tables_are_non_empty_and_strings():
    for table in (_CODING_SIGNALS, _WRITING_SIGNALS, _DECISION_SIGNALS, _RESEARCH_SIGNALS):
        assert len(table) > 0
        assert all(isinstance(s, str) and s for s in table)


def test_all_intent_labels_are_valid():
    expected = {"raw", "organize", "prompt_coding", "prompt_writing", "research", "decision_making"}
    assert set(_VALID_INTENTS) == expected
