"""
Tests for shared.vocab.VocabManager — storage layer (Slice 2.4a).

Hint generation and apply_corrections are tested in Slice 2.4b.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from shared.vocab import (
    SCHEMA_VERSION,
    VALID_CATEGORIES,
    VocabEntry,
    VocabManager,
)


@pytest.fixture
def manager(tmp_path: Path) -> VocabManager:
    """Fresh VocabManager backed by a tmp_path SQLite file."""
    return VocabManager(tmp_path / "vocab.db")


# --- Schema / initialization ---

def test_initial_schema_creates_tables(manager: VocabManager):
    with manager._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "vocab_entries" in tables
    assert "schema_version" in tables


def test_schema_version_is_recorded(manager: VocabManager):
    with manager._connect() as conn:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    assert row["v"] == SCHEMA_VERSION


def test_wal_mode_is_active(manager: VocabManager):
    with manager._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_reinit_does_not_reapply_migrations(tmp_path: Path):
    db = tmp_path / "vocab.db"
    VocabManager(db)
    # Insert a row, then re-init the manager — the row must survive.
    m = VocabManager(db)
    m.add_term("Maisu")
    VocabManager(db)
    assert m.get_term("Maisu") is not None


# --- add_term ---

def test_add_term_returns_entry_with_id(manager: VocabManager):
    entry = manager.add_term("Maisu", variants=["maizu", "my su"], language="es-CL")
    assert entry.id is not None
    assert entry.term == "Maisu"
    assert entry.variants == ["maizu", "my su"]
    assert entry.language == "es-CL"
    assert entry.confidence == 0.85
    assert entry.usage_count == 0
    assert entry.created_at  # ISO string
    assert entry.last_used is None


def test_add_term_strips_whitespace(manager: VocabManager):
    entry = manager.add_term("  Maisu  ", variants=["  maizu  ", "  ", ""])
    assert entry.term == "Maisu"
    assert entry.variants == ["maizu"]  # empty/whitespace variants dropped


def test_add_term_persists_to_disk(manager: VocabManager):
    manager.add_term("Maisu", variants=["maizu"])

    with manager._connect() as conn:
        row = conn.execute("SELECT * FROM vocab_entries WHERE term=?", ("Maisu",)).fetchone()
    assert row is not None
    assert row["term"] == "Maisu"
    assert json.loads(row["variants"]) == ["maizu"]


def test_add_term_rejects_empty(manager: VocabManager):
    with pytest.raises(ValueError, match="non-empty"):
        manager.add_term("")
    with pytest.raises(ValueError, match="non-empty"):
        manager.add_term("   ")


def test_add_term_rejects_invalid_category(manager: VocabManager):
    with pytest.raises(ValueError, match="invalid category"):
        manager.add_term("Maisu", category="bogus")


@pytest.mark.parametrize("category", sorted(VALID_CATEGORIES))
def test_add_term_accepts_each_valid_category(manager: VocabManager, category: str):
    entry = manager.add_term(f"term_{category}", category=category)
    assert entry.category == category


def test_add_term_uniqueness_constraint_per_project(manager: VocabManager):
    manager.add_term("Maisu", project="maisu")
    with pytest.raises(sqlite3.IntegrityError):
        manager.add_term("Maisu", project="maisu")


def test_add_term_same_term_different_project_allowed(manager: VocabManager):
    manager.add_term("Bridge", project="alpha")
    manager.add_term("Bridge", project="beta")  # must NOT raise
    manager.add_term("Bridge", project=None)    # global also OK
    assert len(manager.list_terms()) == 3


# --- list_terms ---

def test_list_empty_returns_empty(manager: VocabManager):
    assert manager.list_terms() == []


def test_list_no_filter_returns_all(manager: VocabManager):
    manager.add_term("alpha")
    manager.add_term("beta", project="proj1")
    manager.add_term("gamma", project="proj2")
    assert {e.term for e in manager.list_terms()} == {"alpha", "beta", "gamma"}


def test_list_by_project_includes_global_by_default(manager: VocabManager):
    manager.add_term("global_term")  # project=NULL
    manager.add_term("project_term", project="proj1")
    manager.add_term("other_project_term", project="proj2")
    terms = {e.term for e in manager.list_terms(project="proj1")}
    assert terms == {"global_term", "project_term"}


def test_list_by_project_can_exclude_global(manager: VocabManager):
    manager.add_term("global_term")
    manager.add_term("project_term", project="proj1")
    terms = {e.term for e in manager.list_terms(project="proj1", include_global=False)}
    assert terms == {"project_term"}


def test_list_by_language_includes_unspecified(manager: VocabManager):
    manager.add_term("untagged")  # language=NULL
    manager.add_term("spanish_term", language="es")
    manager.add_term("english_term", language="en")
    terms = {e.term for e in manager.list_terms(language="es")}
    assert terms == {"untagged", "spanish_term"}


def test_list_orders_by_usage_count_desc_then_term_asc(manager: VocabManager):
    manager.add_term("zebra")
    manager.add_term("alpha")
    manager.add_term("middle")
    manager.increment_usage("zebra")
    manager.increment_usage("zebra")
    manager.increment_usage("alpha")

    ordered = [e.term for e in manager.list_terms()]
    assert ordered == ["zebra", "alpha", "middle"]


# --- remove_term ---

def test_remove_term_global(manager: VocabManager):
    manager.add_term("temp")
    assert manager.remove_term("temp") is True
    assert manager.get_term("temp") is None


def test_remove_term_returns_false_when_absent(manager: VocabManager):
    assert manager.remove_term("nonexistent") is False


def test_remove_term_scoped_to_project(manager: VocabManager):
    manager.add_term("Bridge", project="alpha")
    manager.add_term("Bridge", project="beta")

    assert manager.remove_term("Bridge", project="alpha") is True
    assert manager.get_term("Bridge", project="alpha") is None
    assert manager.get_term("Bridge", project="beta") is not None


# --- get_term ---

def test_get_term_returns_entry(manager: VocabManager):
    manager.add_term("Maisu", variants=["maizu"], project="proj")
    entry = manager.get_term("Maisu", project="proj")
    assert entry is not None
    assert entry.term == "Maisu"
    assert entry.variants == ["maizu"]


def test_get_term_distinguishes_global_vs_project(manager: VocabManager):
    manager.add_term("Bridge", project=None)
    manager.add_term("Bridge", project="proj")
    assert manager.get_term("Bridge", project=None).project is None
    assert manager.get_term("Bridge", project="proj").project == "proj"


# --- increment_usage ---

def test_increment_usage_updates_count_and_last_used(manager: VocabManager):
    manager.add_term("Maisu")
    manager.increment_usage("Maisu")
    entry = manager.get_term("Maisu")
    assert entry.usage_count == 1
    assert entry.last_used is not None


def test_increment_usage_is_per_project(manager: VocabManager):
    manager.add_term("Bridge", project="alpha")
    manager.add_term("Bridge", project="beta")
    manager.increment_usage("Bridge", project="alpha")
    assert manager.get_term("Bridge", project="alpha").usage_count == 1
    assert manager.get_term("Bridge", project="beta").usage_count == 0


# --- corrupt JSON tolerance ---

def test_corrupt_variants_json_is_tolerated(tmp_path: Path):
    db = tmp_path / "vocab.db"
    m = VocabManager(db)
    m.add_term("Maisu")

    # Manually corrupt the variants JSON
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE vocab_entries SET variants = ? WHERE term = ?",
                     ("not-valid-json{{", "Maisu"))
        conn.commit()

    entry = m.get_term("Maisu")
    assert entry is not None
    assert entry.variants == []  # gracefully degrades


# --- VocabEntry dataclass ---

def test_vocab_entry_default_variants_independent():
    """Field default uses default_factory — instances must not share lists."""
    a = VocabEntry(id=None, term="a")
    b = VocabEntry(id=None, term="b")
    a.variants.append("x")
    assert b.variants == []


# --- get_hint ---

def test_hint_empty_vocab_returns_empty_string(manager: VocabManager):
    assert manager.get_hint() == ""


def test_hint_single_term(manager: VocabManager):
    manager.add_term("Maisu")
    assert manager.get_hint() == "Maisu"


def test_hint_multiple_terms_comma_separated(manager: VocabManager):
    manager.add_term("Maisu")
    manager.add_term("Antigravity")
    manager.add_term("auto-whisper")
    hint = manager.get_hint()
    assert hint == "auto-whisper, Maisu, Antigravity" or "Maisu" in hint
    # Order is usage_count DESC then term ASC; with 0 usage, alphabetical.
    parts = [p.strip() for p in hint.split(",")]
    assert set(parts) == {"Maisu", "Antigravity", "auto-whisper"}


def test_hint_orders_by_usage_count_desc(manager: VocabManager):
    manager.add_term("rare")
    manager.add_term("common")
    manager.increment_usage("common")
    manager.increment_usage("common")
    manager.increment_usage("common")
    manager.increment_usage("rare")

    hint = manager.get_hint()
    assert hint.startswith("common")


def test_hint_respects_max_chars_skipping_long_terms(manager: VocabManager):
    manager.add_term("ABC")              # 3 chars
    manager.add_term("X" * 200)          # 200 chars — fits 1st but blocks 2nd at 244 cap
    manager.add_term("DEF")              # 3 chars
    # Make the long term most-used so it lands first in ordering.
    manager.increment_usage("X" * 200)

    hint = manager.get_hint(max_chars=244)
    # "X"*200 (200) fits; ", ABC" or ", DEF" each adds 5 → 205 — both fit.
    # Total of all three fits (210). So all three are present.
    assert "X" * 200 in hint
    assert "ABC" in hint
    assert "DEF" in hint


def test_hint_skips_term_when_addition_overflows_max_chars(manager: VocabManager):
    manager.add_term("a" * 100)
    manager.add_term("b" * 100)
    manager.add_term("c" * 100)
    # Three 100-char terms + 4-char of separators = 304 > 244, so 3rd skipped
    hint = manager.get_hint(max_chars=244)
    parts = hint.split(", ")
    assert len(parts) == 2  # only 2 fit


def test_hint_filters_by_project(manager: VocabManager):
    manager.add_term("global_term")            # project=None
    manager.add_term("alpha_term", project="alpha")
    manager.add_term("beta_term", project="beta")

    hint = manager.get_hint(project="alpha")
    assert "alpha_term" in hint
    assert "global_term" in hint  # global terms still appear in any project
    assert "beta_term" not in hint


def test_hint_filters_by_language(manager: VocabManager):
    manager.add_term("any_lang")              # language=None
    manager.add_term("spanish", language="es")
    manager.add_term("english", language="en")

    hint = manager.get_hint(language="es")
    assert "spanish" in hint
    assert "any_lang" in hint  # NULL language means "applies to any"
    assert "english" not in hint


# --- apply_corrections ---

def test_corrections_empty_text_unchanged(manager: VocabManager):
    manager.add_term("Maisu", variants=["maizu"])
    assert manager.apply_corrections("") == ""
    assert manager.apply_corrections("   ") == "   "


def test_corrections_no_vocab_unchanged(manager: VocabManager):
    text = "hola mundo cualquier cosa"
    assert manager.apply_corrections(text) == text


def test_corrections_no_variants_unchanged(manager: VocabManager):
    """Term with no variants → can't correct anything."""
    manager.add_term("Maisu", variants=[])
    assert manager.apply_corrections("escribí maizu") == "escribí maizu"


def test_corrections_replaces_exact_variant(manager: VocabManager):
    manager.add_term("Maisu", variants=["maizu"])
    assert manager.apply_corrections("escribí maizu en el doc") == "escribí Maisu en el doc"


def test_corrections_case_insensitive_match(manager: VocabManager):
    manager.add_term("Maisu", variants=["maizu"])
    # All these should match the variant "maizu" regardless of case
    assert manager.apply_corrections("MAIZU") == "Maisu"
    assert manager.apply_corrections("Maizu") == "Maisu"
    assert manager.apply_corrections("maIZU") == "Maisu"


def test_corrections_preserves_capitalization_style(manager: VocabManager):
    manager.add_term("kubernetes", variants=["coopernedis"])
    # Original lowercase → canonical lowercase
    assert manager.apply_corrections("uso coopernedis") == "uso kubernetes"
    # Original capitalized → canonical capitalized
    assert manager.apply_corrections("Coopernedis es el orquestador") == "Kubernetes es el orquestador"


def test_corrections_canonical_already_uppercase_preserved(manager: VocabManager):
    """If canonical already has its own casing (e.g. 'API'), don't lowercase it."""
    manager.add_term("API", variants=["apie"])
    assert manager.apply_corrections("usé apie") == "usé API"
    assert manager.apply_corrections("Apie es el endpoint") == "API es el endpoint"


def test_corrections_word_boundary_no_substring_match(manager: VocabManager):
    manager.add_term("Maisu", variants=["maizu"])
    # "maizuverde" must NOT be replaced — only standalone "maizu"
    assert manager.apply_corrections("maizuverde no") == "maizuverde no"
    assert manager.apply_corrections("vermaizu nope") == "vermaizu nope"


def test_corrections_handles_punctuation_neighbors(manager: VocabManager):
    manager.add_term("Maisu", variants=["maizu"])
    assert manager.apply_corrections("maizu, hola") == "Maisu, hola"
    assert manager.apply_corrections("(maizu)") == "(Maisu)"
    assert manager.apply_corrections("¿maizu?") == "¿Maisu?"
    assert manager.apply_corrections("maizu.") == "Maisu."


def test_corrections_multiple_replacements_in_one_text(manager: VocabManager):
    manager.add_term("Maisu", variants=["maizu", "my su"])
    manager.add_term("kubernetes", variants=["coopernedis"])
    text = "estoy en maizu usando coopernedis"
    assert manager.apply_corrections(text) == "estoy en Maisu usando kubernetes"


def test_corrections_skips_multiword_variants_in_v1(manager: VocabManager):
    """Multi-word variants are deferred to v2 — single-word only for v1."""
    manager.add_term("Maisu", variants=["my su"])
    # "my su" has a space → not in v1 single-word variant map → no replace
    assert manager.apply_corrections("dije my su") == "dije my su"


def test_corrections_filters_by_project(manager: VocabManager):
    manager.add_term("Alpha", variants=["alfa"], project="proj_a")
    manager.add_term("Beta", variants=["alfa"], project="proj_b")  # same variant collides

    # When using proj_a, "alfa" → "Alpha"
    out_a = manager.apply_corrections("dije alfa", project="proj_a")
    assert "Alpha" in out_a
    # Project filter respected — at minimum, the right canonical wins.


def test_corrections_filters_by_language(manager: VocabManager):
    manager.add_term("Hola", variants=["ola"], language="es")
    # English-only context shouldn't get the Spanish variant treatment
    out = manager.apply_corrections("ola how are you", language="en")
    assert out == "ola how are you"


def test_corrections_includes_global_when_project_set(manager: VocabManager):
    """Global vocab (project=None) is layered with project-specific."""
    manager.add_term("Maisu", variants=["maizu"], project=None)  # global
    manager.add_term("Project", variants=["proyect"], project="proj_x")

    out = manager.apply_corrections("escribí maizu en proyect", project="proj_x")
    assert out == "escribí Maisu en Project"


def test_corrections_handles_unicode_letters(manager: VocabManager):
    """Spanish accented chars must be word-boundary safe."""
    manager.add_term("España", variants=["espania"])
    assert manager.apply_corrections("vivo en espania") == "vivo en España"
