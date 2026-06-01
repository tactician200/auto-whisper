"""Thread-safe lazy Anthropic client singleton.

Mirrors groq_client.py. Used by shared.processing._call_claude for the
reasoning-heavy voice actions (tone, translate, reply) routed to Claude
under the hybrid engine. Groq still handles the simple transformations.
"""

import threading
from shared.config import ANTHROPIC_API_KEY_DICTATION

_anthropic_client = None
_anthropic_lock = threading.Lock()


def get_anthropic_client():
    """Return a shared Anthropic client instance (thread-safe)."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    with _anthropic_lock:
        if _anthropic_client is None:
            from anthropic import Anthropic
            _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY_DICTATION, timeout=60.0)
    return _anthropic_client
