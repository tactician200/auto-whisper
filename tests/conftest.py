"""
pytest fixtures for auto-whisper test suite.

Core principles:
- Tests MUST NOT hit the network. All Groq API calls are mocked.
- Tests MUST NOT touch the user's real Application Support directory.
  The autouse fixture below redirects any module that opens a vocab.db
  via get_default_db_path to a per-test tmp_path location.

The fixtures here patch `get_groq_client` so any call into text_processor
routes through a configurable mock instead of the real Groq SDK.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest


# --- Auto-isolate vocabulary storage on EVERY test ---
#
# Modules that lazily build a VocabManager (auto_whisper.transcription,
# auto_whisper_service.routes.transcribe) will pick up this redirected
# path before any other fixture or test code runs. Tests that want to
# pre-populate vocab can request the isolated_vocab_db fixture explicitly
# to get the same path back as a Path object.

@pytest.fixture(autouse=True)
def isolated_vocab_db(tmp_path: Path, monkeypatch) -> Path:
    """Redirect every entry point to vocab.db at a tmp_path-scoped location.

    Critically: `from shared.vocab import get_default_db_path` in consumer
    modules creates a SEPARATE binding from `shared.vocab.get_default_db_path`,
    so each consumer must be patched independently.

    Also resets the lazy module-level singletons in consumer modules so each
    test gets a fresh empty database — the singleton would otherwise cache
    a VocabManager from a previous test.
    """
    db_path = tmp_path / "vocab.db"
    fake_get_path = lambda: db_path

    # Patch the source.
    monkeypatch.setattr("shared.vocab.get_default_db_path", fake_get_path)

    # Patch every name imported FROM shared.vocab into a consumer module.
    import auto_whisper.transcription as _t
    monkeypatch.setattr(_t, "get_default_db_path", fake_get_path)
    monkeypatch.setattr(_t, "_vocab_manager", None)

    try:
        import auto_whisper_service.routes.transcribe as _tr
        monkeypatch.setattr(_tr, "get_default_db_path", fake_get_path)
        monkeypatch.setattr(_tr, "_vocab_manager", None)
    except ImportError:
        # Service routes module not always loaded — fine.
        pass

    return db_path


@pytest.fixture
def mock_groq_response():
    """Build a mock Groq chat.completions.create() response object.

    Usage:
        resp = mock_groq_response("hello world")
        client.chat.completions.create.return_value = resp
    """

    def _build(text: str) -> MagicMock:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = text
        return response

    return _build


@pytest.fixture
def mock_groq_client(monkeypatch, mock_groq_response):
    """Patch shared.groq_client.get_groq_client to return a MagicMock.

    The returned mock's chat.completions.create is pre-configured to return
    a valid response with text "MOCK_RESPONSE". Individual tests can override
    this by reassigning `mock.chat.completions.create.return_value`.

    Also sets GROQ_API_KEY_DICTATION to a sentinel so _call_groq does not
    early-return None due to missing key.
    """
    client = MagicMock()
    client.chat.completions.create.return_value = mock_groq_response("MOCK_RESPONSE")

    # Patch in shared.processing — the actual home of _call_groq after Slice 3.1.
    # auto_whisper.text_processor now just re-exports, so the old daemon path
    # tests still find the same functions via the re-exports.
    monkeypatch.setattr("shared.processing.get_groq_client", lambda: client)
    monkeypatch.setattr("shared.processing.GROQ_API_KEY_DICTATION", "test-key-sentinel")
    return client


@pytest.fixture
def captured_prompt(mock_groq_client):
    """Helper that returns the prompt string sent to Groq on last call.

    Call the text_processor function first, then read this fixture.
    """

    def _get():
        calls = mock_groq_client.chat.completions.create.call_args_list
        if not calls:
            return None
        return calls[-1].kwargs["messages"][0]["content"]

    return _get
