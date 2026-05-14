"""Tests for shared.transcription_cleanup."""

import pytest

from shared.transcription_cleanup import (
    EXACT_ARTIFACTS,
    clean_transcription,
    normalize_text,
)


# --- normalize_text ---

def test_normalize_strips_diacritics():
    assert normalize_text("Subtítulos") == "subtitulos"
    assert normalize_text("también") == "tambien"


def test_normalize_lowercases():
    assert normalize_text("HOLA") == "hola"


def test_normalize_strips_whitespace():
    assert normalize_text("  hello  ") == "hello"


# --- clean_transcription · whitespace ---

def test_clean_strips_leading_trailing_dashes():
    assert clean_transcription("— hello world —") == "hello world"
    assert clean_transcription("-- hello --") == "hello"


def test_clean_collapses_internal_whitespace():
    assert clean_transcription("hello    world") == "hello world"
    assert clean_transcription("foo  bar   baz") == "foo bar baz"


def test_clean_preserves_normal_text():
    assert clean_transcription("hello world") == "hello world"


# --- clean_transcription · artifacts ---

@pytest.mark.parametrize("artifact", [
    # Match v4.2 EXACT_ARTIFACTS exactly — these are the forms Whisper
    # typically emits on near-silent audio, sans punctuation.
    "Subtítulos realizados por la comunidad de Amara org",
    "Subtítulos por la comunidad de Amara org",
    "Gracias por ver el video",
    "Suscríbete al canal",
    "Hola buenos días",
])
def test_clean_drops_known_whisper_artifacts(artifact):
    assert clean_transcription(artifact) == ""


@pytest.mark.parametrize("artifact_with_punct", [
    "Hola, buenos días.",
    "Subtítulos por la comunidad de Amara.org",
    "Subtítulos realizados por la comunidad de Amara.org!",
    "Gracias por ver el video!",
    "¡Suscríbete al canal!",
])
def test_clean_drops_artifacts_with_punctuation(artifact_with_punct):
    """normalize_text strips punctuation before comparison, so artifacts
    with trailing/embedded punctuation still match the canonical forms in
    EXACT_ARTIFACTS. Matches v4.2 daemon behavior exactly."""
    assert clean_transcription(artifact_with_punct) == ""


def test_normalize_strips_punctuation():
    assert normalize_text("Hola, mundo!") == "hola mundo"
    assert normalize_text("foo.bar.baz") == "foo bar baz"
    assert normalize_text("¡Qué bueno!") == "que bueno"


def test_clean_drops_empty_or_whitespace_only():
    assert clean_transcription("") == ""
    assert clean_transcription("   ") == ""
    assert clean_transcription("\n\n\n") == ""
    assert clean_transcription("---") == ""


def test_clean_does_not_drop_legitimate_content_resembling_artifacts():
    """E.g. someone dictating 'hola buenos dias [name]' should NOT match."""
    assert clean_transcription("hola buenos dias Juan") == "hola buenos dias Juan"
    assert clean_transcription("gracias por ver el video del proyecto") == "gracias por ver el video del proyecto"


# --- EXACT_ARTIFACTS contract ---

def test_exact_artifacts_are_already_normalized():
    """All entries in EXACT_ARTIFACTS must equal their own normalize_text() —
    otherwise the lookup in clean_transcription will never match them."""
    for artifact in EXACT_ARTIFACTS:
        assert artifact == normalize_text(artifact), \
            f"Artifact {artifact!r} is not pre-normalized — would never match"
