# auto-whisper · Tests

Unit tests for v5. No network I/O — all LLM calls mocked.

## Run

```bash
cd ~/src/auto-whisper-v5
PYTHONPATH=. .venv/bin/pytest tests/ -v
```

Fast: whole suite completes in <2 seconds.

## Structure

```
tests/
├── conftest.py                    # pytest fixtures (mock_groq_client, captured_prompt)
├── fixtures/
│   └── dictation_samples.py       # realistic dictation inputs (ES-CL, mixed, edge cases)
├── test_text_processor.py         # summarize, explain, organize_ideas, optimize_prompt
├── test_prompts.py                # prompt template integrity
└── test_notifications.py          # macOS notification helper
```

## Adding cases

### New dictation sample

Add to `fixtures/dictation_samples.py`:

```python
SAMPLES["my_new_case"] = "tu dictado realista aquí"
```

Then reference in any test: `SAMPLES["my_new_case"]`.

### New unit test

Follow the pattern in `test_text_processor.py`: receive `mock_groq_client`
fixture, call the function under test, then inspect `captured_prompt()` to
verify what was sent to Groq, or the mock's `call_args` to check kwargs.

### Future: golden/snapshot tests

Infra ready but not populated. Pattern: record real dictation → real Groq
response pair → store as JSON in `tests/golden/` → test replays the dictation
and asserts output matches (or is within tolerance). Requires Groq API key
and is a separate opt-in test category (`pytest -m golden`).

## What these tests DO NOT cover yet

- `dictation_daemon.py` — deferred to Phase 1+ (will be adelgazado to pure
  client; then more testable)
- Audio capture, hotkey detection — require macOS-specific integration tests
- Real Groq output quality — belongs in golden tests (opt-in)
- End-to-end recording → transcription → paste flow — manual QA until Phase 5
