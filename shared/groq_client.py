"""Thread-safe lazy Groq client singleton."""

import threading
from shared.config import GROQ_API_KEY_DICTATION

_groq_client = None
_groq_lock = threading.Lock()


def get_groq_client():
    """Return a shared Groq client instance (thread-safe)."""
    global _groq_client
    if _groq_client is not None:
        return _groq_client
    with _groq_lock:
        if _groq_client is None:
            from groq import Groq
            _groq_client = Groq(api_key=GROQ_API_KEY_DICTATION, timeout=30.0)
    return _groq_client
