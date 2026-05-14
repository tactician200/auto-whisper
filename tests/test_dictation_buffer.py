"""Tests for auto_whisper.dictation_buffer (Slice 2.5a)."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from auto_whisper.dictation_buffer import (
    DEFAULT_BUFFER_SIZE,
    BufferEntry,
    DictationBuffer,
)


# --- construction ---

def test_default_size_constant():
    """User-facing N=10 cap matches design D7."""
    assert DEFAULT_BUFFER_SIZE == 10


def test_default_buffer_uses_default_size():
    b = DictationBuffer()
    assert b.max_size == DEFAULT_BUFFER_SIZE
    assert b.size == 0


def test_custom_size():
    b = DictationBuffer(max_size=3)
    assert b.max_size == 3


def test_zero_or_negative_size_rejected():
    with pytest.raises(ValueError):
        DictationBuffer(max_size=0)
    with pytest.raises(ValueError):
        DictationBuffer(max_size=-1)


# --- add ---

def test_add_returns_populated_entry():
    b = DictationBuffer()
    entry = b.add("hola mundo", mode_used="dictate", language="es")
    assert isinstance(entry, BufferEntry)
    assert entry.id  # uuid generated
    assert entry.timestamp  # ISO string
    assert entry.raw_text == "hola mundo"
    assert entry.mode_used == "dictate"
    assert entry.language == "es"
    assert entry.processed == {}


def test_add_strips_raw_text_whitespace():
    b = DictationBuffer()
    entry = b.add("   hola   ", mode_used="dictate")
    assert entry.raw_text == "hola"


def test_add_rejects_empty_text():
    b = DictationBuffer()
    with pytest.raises(ValueError):
        b.add("", mode_used="dictate")
    with pytest.raises(ValueError):
        b.add("   ", mode_used="dictate")


def test_add_assigns_unique_ids():
    b = DictationBuffer()
    e1 = b.add("uno", mode_used="dictate")
    e2 = b.add("dos", mode_used="dictate")
    assert e1.id != e2.id


def test_add_with_processed_variants():
    b = DictationBuffer()
    entry = b.add(
        "refactor el header",
        mode_used="optimize",
        processed={"optimize": "## Task\nrefactor the header"},
    )
    assert entry.processed == {"optimize": "## Task\nrefactor the header"}


def test_add_processed_dict_is_copied():
    """Mutating the original dict after add() must not affect the entry."""
    b = DictationBuffer()
    pre = {"optimize": "X"}
    entry = b.add("text", mode_used="optimize", processed=pre)
    pre["optimize"] = "Y"
    assert entry.processed["optimize"] == "X"


# --- ring behavior ---

def test_buffer_caps_at_max_size():
    b = DictationBuffer(max_size=3)
    for i in range(5):
        b.add(f"entry {i}", mode_used="dictate")
    assert b.size == 3


def test_ring_drops_oldest():
    b = DictationBuffer(max_size=3)
    e1 = b.add("uno", mode_used="dictate")
    e2 = b.add("dos", mode_used="dictate")
    e3 = b.add("tres", mode_used="dictate")
    e4 = b.add("cuatro", mode_used="dictate")

    assert b.get(e1.id) is None  # dropped
    assert b.get(e2.id) is not None
    assert b.get(e3.id) is not None
    assert b.get(e4.id) is not None


# --- last / recent / get ---

def test_last_returns_none_on_empty():
    assert DictationBuffer().last() is None


def test_last_returns_most_recent():
    b = DictationBuffer()
    b.add("uno", mode_used="dictate")
    b.add("dos", mode_used="dictate")
    last = b.last()
    assert last.raw_text == "dos"


def test_recent_returns_newest_first():
    b = DictationBuffer()
    b.add("uno", mode_used="dictate")
    b.add("dos", mode_used="dictate")
    b.add("tres", mode_used="dictate")
    texts = [e.raw_text for e in b.recent()]
    assert texts == ["tres", "dos", "uno"]


def test_recent_with_n_limits_result():
    b = DictationBuffer()
    for i in range(5):
        b.add(f"entry {i}", mode_used="dictate")
    assert len(b.recent(n=2)) == 2


def test_recent_with_n_larger_than_buffer_returns_all():
    b = DictationBuffer()
    b.add("uno", mode_used="dictate")
    assert len(b.recent(n=99)) == 1


def test_get_by_id_returns_entry():
    b = DictationBuffer()
    e = b.add("hola", mode_used="dictate")
    assert b.get(e.id) is e or b.get(e.id).raw_text == "hola"


def test_get_nonexistent_id_returns_none():
    b = DictationBuffer()
    b.add("hola", mode_used="dictate")
    assert b.get("nonexistent-id") is None


# --- add_processed ---

def test_add_processed_attaches_to_existing_entry():
    b = DictationBuffer()
    e = b.add("texto crudo", mode_used="dictate")
    assert b.add_processed(e.id, "optimize", "## Task\noptimized") is True
    fresh = b.get(e.id)
    assert fresh.processed["optimize"] == "## Task\noptimized"


def test_add_processed_overwrites_existing_variant():
    b = DictationBuffer()
    e = b.add("texto", mode_used="dictate")
    b.add_processed(e.id, "organize", "first")
    b.add_processed(e.id, "organize", "second")
    assert b.get(e.id).processed["organize"] == "second"


def test_add_processed_returns_false_for_unknown_id():
    b = DictationBuffer()
    assert b.add_processed("does-not-exist", "optimize", "x") is False


def test_add_processed_strips_whitespace_and_rejects_empty():
    b = DictationBuffer()
    e = b.add("texto", mode_used="dictate")
    assert b.add_processed(e.id, "optimize", "   ") is False
    assert b.add_processed(e.id, "optimize", "  hello  ") is True
    assert b.get(e.id).processed["optimize"] == "hello"


# --- clear ---

def test_clear_empties_buffer():
    b = DictationBuffer()
    b.add("uno", mode_used="dictate")
    b.add("dos", mode_used="dictate")
    b.clear()
    assert b.size == 0
    assert b.last() is None


# --- thread safety ---

def test_concurrent_adds_dont_lose_entries():
    """Smoke test under concurrency — N writers each adding one entry,
    final buffer should reflect last min(N, max_size) writes (no exceptions)."""
    b = DictationBuffer(max_size=200)

    def worker(i: int):
        b.add(f"entry-{i}", mode_used="dictate")

    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(worker, range(100)))

    assert b.size == 100


def test_concurrent_add_and_read():
    """Reads while writes occur must not raise (deque + lock)."""
    b = DictationBuffer(max_size=50)
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            b.add(f"write {i}", mode_used="dictate")
            i += 1

    def reader():
        while not stop.is_set():
            b.recent()
            b.last()

    t_w = threading.Thread(target=writer)
    t_r = threading.Thread(target=reader)
    t_w.start()
    t_r.start()
    threading.Event().wait(0.1)  # let them race a bit
    stop.set()
    t_w.join(timeout=2)
    t_r.join(timeout=2)
    assert not t_w.is_alive() and not t_r.is_alive()


# --- module singleton ---

def test_module_buffer_is_an_instance():
    """Module exposes a default singleton for the daemon."""
    from auto_whisper import dictation_buffer
    assert isinstance(dictation_buffer.buffer, DictationBuffer)
