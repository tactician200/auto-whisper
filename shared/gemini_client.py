"""Thread-safe lazy Gemini client singleton.

Mirrors anthropic_client.py / groq_client.py. Used by
shared.processing._call_gemini as the cheap primary engine for the reply
voice action; Claude is the quality fallback if Gemini has no key or fails.
"""

import threading
from shared.config import GEMINI_API_KEY_DICTATION

_gemini_client = None
_gemini_lock = threading.Lock()


def get_gemini_client():
    """Return a shared Gemini client instance (thread-safe)."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    with _gemini_lock:
        if _gemini_client is None:
            from google import genai
            _gemini_client = genai.Client(api_key=GEMINI_API_KEY_DICTATION)
    return _gemini_client
