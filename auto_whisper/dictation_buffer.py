"""Recent-dictations buffer — in-memory ring of last N transcriptions.

Solves the "I didn't mean to optimize this — give me back the raw text" use
case + the "what did I just dictate?" recall use case. Each entry holds the
canonical raw transcription plus any post-processed variants the daemon
generated for it (e.g. the optimized prompt produced when the user dictated
in optimize mode).

Phase 2.5 scope:
- In-memory only (no SQLite persistence yet — that's Privacy Mode toggle territory)
- Ring buffer via collections.deque(maxlen=N)
- Add: invoked by daemon after each successful transcription
- Read: recent(n), last(), get(id)
- IDs are uuid4 strings, stable per entry, used by the Recent Dictations
  submenu so callbacks know which entry to paste

Future (Slice 2.5x or beyond):
- POST /history/{id}/process — re-run a different LLM mode on a stored entry
- SQLite persist toggle for non-Privacy-Mode users
- Audio retention (separate concern; transcription audio is currently
  discarded after the Groq response)
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_BUFFER_SIZE = 10


@dataclass
class BufferEntry:
    """One dictation slot in the buffer.

    raw_text is the canonical transcription as returned by Groq + cleanup +
    vocab corrections. processed is a mapping from mode name (e.g. "optimize",
    "organize") to whatever the LLM emitted for that mode on this same input.

    mode_used records which path produced the dictation initially:
    - "dictate"  — raw transcription pasted directly
    - "optimize" — transcription was passed through optimize_prompt()
    - "organize" — transcription was passed through organize_ideas()
    """

    id: str
    timestamp: str  # ISO 8601 UTC
    raw_text: str
    mode_used: str
    language: str | None
    processed: dict[str, str] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DictationBuffer:
    """Thread-safe ring buffer of recent dictations.

    Threadsafety matters: dictation_daemon dispatches transcription on a
    background thread but menu callbacks read on the main thread. A simple
    Lock around mutations is enough — operations are O(1).
    """

    def __init__(self, max_size: int = DEFAULT_BUFFER_SIZE):
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._entries: deque[BufferEntry] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def add(
        self,
        raw_text: str,
        mode_used: str,
        language: str | None = None,
        processed: dict[str, str] | None = None,
    ) -> BufferEntry:
        """Append a new entry. Returns the entry (with generated id and ts).

        Empty raw_text is rejected — callers should filter None/empty before
        calling. This keeps the "Recent" submenu free of empty rows.
        """
        if not raw_text or not raw_text.strip():
            raise ValueError("raw_text must be non-empty")

        entry = BufferEntry(
            id=uuid.uuid4().hex,
            timestamp=_now_iso(),
            raw_text=raw_text.strip(),
            mode_used=mode_used,
            language=language,
            processed=dict(processed) if processed else {},
        )
        with self._lock:
            self._entries.append(entry)
        return entry

    def add_processed(self, entry_id: str, mode: str, text: str) -> bool:
        """Attach a processed variant to an existing entry.

        Returns True if the entry was found, False otherwise. Existing
        variants under the same mode are overwritten (most recent wins).
        """
        if not text or not text.strip():
            return False
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    e.processed[mode] = text.strip()
                    return True
        return False

    def recent(self, n: int | None = None) -> list[BufferEntry]:
        """Return the most-recent-first list of up to n entries.

        n=None → return all (up to max_size). n>max_size also returns all.
        """
        with self._lock:
            snapshot = list(self._entries)
        snapshot.reverse()  # newest first
        if n is None:
            return snapshot
        return snapshot[:n]

    def last(self) -> BufferEntry | None:
        """Return the most recently added entry, or None if buffer is empty."""
        with self._lock:
            if not self._entries:
                return None
            return self._entries[-1]

    def get(self, entry_id: str) -> BufferEntry | None:
        """Look up an entry by id."""
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    return e
        return None

    def clear(self) -> None:
        """Empty the buffer. Used by Privacy Mode toggle and by tests."""
        with self._lock:
            self._entries.clear()


# Module-level singleton — daemon code reaches in to add/read.
# Tests should monkeypatch this attribute or instantiate their own buffer.
buffer: DictationBuffer = DictationBuffer()
