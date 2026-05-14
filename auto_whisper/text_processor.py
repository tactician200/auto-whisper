#!/usr/bin/env python3
"""
Text processing — daemon-side facade.

Slice 3.1: extracted logic to shared.processing.
Slice 3.3: daemon now goes through processing_routing dispatcher.

With AUTO_WHISPER_USE_SERVICE_PROCESSING=0 (default), behavior is identical
to v4.2 (direct shared.processing calls).
With AUTO_WHISPER_USE_SERVICE_PROCESSING=1, calls route via the local service
(POST /process). Useful for testing the service path without restarting
production.

Existing daemon callers (dictation_daemon.py, speak.py) keep importing from
here unchanged.
"""

# Public API — daemon-friendly dispatchers (flag-aware).
from auto_whisper.processing_routing import (  # noqa: F401
    USE_SERVICE_PROCESSING,
    classify_intent,
    decision_brief,
    explain,
    optimize_prompt,
    optimize_writing,
    organize_ideas,
    research_brief,
    summarize,
)

# Constants — re-exported for any caller that referenced them.
from shared.processing import (  # noqa: F401
    DEFAULT_MAX_COMPLETION_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
    MAX_INPUT_CHARS,
    MODES,
    OPTIMIZE_MAX_COMPLETION_TOKENS,
)
