"""Vocabulary manager — custom terms + fuzzy correction.

Solves the "Maisu" problem: project / proper / technical names that Whisper
mistranscribes (maizu, my su, maize) get learned as canonical-term + variants
pairs and corrected on every future transcription.

Storage: SQLite at $APP_SUPPORT/vocab.db, WAL mode so daemon and service can
both read concurrently. Single-user single-machine — concurrency is not a
real concern, but WAL is cheap insurance.

This module is `shared/` because BOTH paths consume it:
- Direct Groq path (daemon, USE_SERVICE_TRANSCRIPTION=0)
- Service path (auto_whisper_service /transcribe endpoint, flag=1)

Schema versioning: schema_version table holds an integer. Migrations live
in MIGRATIONS list, each entry is a callable taking a connection. On startup
the manager runs any migrations newer than the current version.

Phase 2.4 scope: storage + CRUD + hint + apply_corrections. No learning
signals (those need Dictation Buffer from 2.5). Manual entry only via
add_term(). Menu UI for adding terms is in Slice 2.4d.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


# --- Schema definitions ---

SCHEMA_VERSION = 1


def _migration_001(conn: sqlite3.Connection) -> None:
    """Initial schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS vocab_entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            term         TEXT NOT NULL,
            variants     TEXT NOT NULL,            -- JSON array of strings
            language     TEXT,                      -- ISO 639-1, nullable
            project      TEXT,                      -- project tag, nullable (NULL = global)
            category     TEXT NOT NULL DEFAULT 'proper_noun',
            confidence   REAL NOT NULL DEFAULT 0.85,
            usage_count  INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL,             -- ISO 8601 UTC
            last_used    TEXT,                      -- ISO 8601 UTC, nullable
            UNIQUE(term, project)
        );

        CREATE INDEX IF NOT EXISTS idx_vocab_project ON vocab_entries(project);
        CREATE INDEX IF NOT EXISTS idx_vocab_language ON vocab_entries(language);
    """)


# Index = target version (1-indexed). MIGRATIONS[0] runs to reach version 1.
MIGRATIONS: list = [_migration_001]


# --- Data class ---

@dataclass
class VocabEntry:
    id: int | None
    term: str
    variants: list[str] = field(default_factory=list)
    language: str | None = None
    project: str | None = None
    category: str = "proper_noun"
    confidence: float = 0.85
    usage_count: int = 0
    created_at: str = ""
    last_used: str | None = None


VALID_CATEGORIES: frozenset[str] = frozenset({
    "proper_noun",   # names of people, places, projects
    "acronym",       # API, MVP, SaaS — sometimes spelled out by Whisper
    "technical",     # domain-specific words (e.g. "kernel", "tensor")
    "brand",         # product/company names
})


def get_default_db_path() -> Path:
    """Standard location: ~/Library/Application Support/auto-whisper/vocab.db.

    Same directory as the service auth token. Both daemon and service
    use this path. Tests should pass an explicit path to VocabManager
    instead of relying on this default."""
    return Path.home() / "Library" / "Application Support" / "auto-whisper" / "vocab.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- VocabManager ---

class VocabManager:
    """SQLite-backed vocabulary store."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with row-factory and WAL mode set."""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        """Run migrations as needed."""
        with self._connect() as conn:
            # WAL mode is per-database, not per-connection — set it once.
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
            )
            row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = (row["v"] or 0) if row else 0

            for i, migration in enumerate(MIGRATIONS, start=1):
                if i > current:
                    logger.info(f"applying vocab schema migration {i}")
                    migration(conn)
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (i,)
                    )

    # --- CRUD ---

    def add_term(
        self,
        term: str,
        variants: list[str] | None = None,
        language: str | None = None,
        project: str | None = None,
        category: str = "proper_noun",
        confidence: float = 0.85,
    ) -> VocabEntry:
        """Insert a term. Raises sqlite3.IntegrityError on duplicate (term, project)."""
        if not term or not term.strip():
            raise ValueError("term must be non-empty")
        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"invalid category {category!r}; allowed: {sorted(VALID_CATEGORIES)}"
            )

        term = term.strip()
        variants = [v.strip() for v in (variants or []) if v and v.strip()]
        created_at = _now_iso()

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO vocab_entries
                  (term, variants, language, project, category, confidence,
                   usage_count, created_at, last_used)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, NULL)
                """,
                (term, json.dumps(variants), language, project, category,
                 confidence, created_at),
            )
            entry_id = cur.lastrowid

        return VocabEntry(
            id=entry_id, term=term, variants=variants, language=language,
            project=project, category=category, confidence=confidence,
            usage_count=0, created_at=created_at, last_used=None,
        )

    def list_terms(
        self,
        project: str | None = None,
        language: str | None = None,
        include_global: bool = True,
    ) -> list[VocabEntry]:
        """List entries matching project/language filters.

        include_global: when project is given, also include entries with project IS NULL.
        """
        clauses: list[str] = []
        params: list = []

        if project is None:
            # No project filter — return everything, optionally filtered by language.
            pass
        elif include_global:
            clauses.append("(project = ? OR project IS NULL)")
            params.append(project)
        else:
            clauses.append("project = ?")
            params.append(project)

        if language is not None:
            clauses.append("(language = ? OR language IS NULL)")
            params.append(language)

        sql = "SELECT * FROM vocab_entries"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY usage_count DESC, term ASC"

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [self._row_to_entry(r) for r in rows]

    def remove_term(self, term: str, project: str | None = None) -> bool:
        """Delete the (term, project) entry. Returns True if a row was removed."""
        with self._connect() as conn:
            if project is None:
                cur = conn.execute(
                    "DELETE FROM vocab_entries WHERE term = ? AND project IS NULL",
                    (term,),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM vocab_entries WHERE term = ? AND project = ?",
                    (term, project),
                )
            return cur.rowcount > 0

    def get_term(self, term: str, project: str | None = None) -> VocabEntry | None:
        """Fetch the (term, project) entry, or None if not present."""
        with self._connect() as conn:
            if project is None:
                row = conn.execute(
                    "SELECT * FROM vocab_entries WHERE term = ? AND project IS NULL",
                    (term,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM vocab_entries WHERE term = ? AND project = ?",
                    (term, project),
                ).fetchone()
        return self._row_to_entry(row) if row else None

    def increment_usage(self, term: str, project: str | None = None) -> None:
        """Bump usage_count and update last_used for (term, project)."""
        now = _now_iso()
        with self._connect() as conn:
            if project is None:
                conn.execute(
                    """UPDATE vocab_entries
                       SET usage_count = usage_count + 1, last_used = ?
                       WHERE term = ? AND project IS NULL""",
                    (now, term),
                )
            else:
                conn.execute(
                    """UPDATE vocab_entries
                       SET usage_count = usage_count + 1, last_used = ?
                       WHERE term = ? AND project = ?""",
                    (now, term, project),
                )

    # --- Hint generation ---

    # Whisper's `prompt` parameter is documented at ~244 tokens. Tokens are
    # ~3-4 chars on average for Spanish/English, so 244 chars is a conservative
    # upper bound that stays within the limit. Adjust if Whisper rejects.
    DEFAULT_HINT_MAX_CHARS = 244

    def get_hint(
        self,
        project: str | None = None,
        language: str | None = None,
        max_chars: int = DEFAULT_HINT_MAX_CHARS,
    ) -> str:
        """Build a comma-separated hint string for Whisper's `prompt` parameter.

        Selects canonical terms (NOT variants — we want Whisper to learn the
        correct form) ordered by usage_count desc. Stops adding terms when the
        next one would exceed max_chars; does not truncate mid-term.

        Returns "" when no vocab matches the filter — caller should treat
        empty string as "no prompt" and omit the parameter from the Groq call.
        """
        entries = self.list_terms(project=project, language=language)
        if not entries:
            return ""

        parts: list[str] = []
        total = 0
        for entry in entries:
            term = entry.term
            # +2 for ", " separator; on first term no separator yet
            new_len = total + len(term) + (2 if parts else 0)
            if new_len > max_chars:
                continue  # skip terms that don't fit; keep trying smaller ones
            parts.append(term)
            total = new_len

        return ", ".join(parts)

    # --- Correction ---

    # Match a contiguous run of "word characters" (Unicode-aware in Python 3
    # by default). Punctuation between words is preserved by re.sub since it
    # falls outside the match.
    _WORD_RE = re.compile(r"\w+", flags=re.UNICODE)

    def apply_corrections(
        self,
        text: str,
        project: str | None = None,
        language: str | None = None,
    ) -> str:
        """Replace known variants with their canonical terms (case-insensitive,
        word-boundary-aware).

        Capitalization heuristic: if the original word starts with uppercase,
        the canonical replacement is also capitalized at index 0. Otherwise
        the canonical's own casing is used as-is.

        Phase 2.4 v1 — exact variant matching only. Fuzzy / phonetic match
        is intentionally out of scope: high false-positive risk without real
        usage data to tune the threshold. Add later as opt-in.
        """
        if not text or not text.strip():
            return text

        entries = self.list_terms(project=project, language=language)
        if not entries:
            return text

        # Build variant → canonical map. Multi-word variants (with spaces)
        # are skipped here — single-word only for v1; phrase matching is
        # a future enhancement.
        variant_to_canonical: dict[str, str] = {}
        for entry in entries:
            for variant in entry.variants:
                if not variant or " " in variant:
                    continue
                variant_to_canonical[variant.lower()] = entry.term

        if not variant_to_canonical:
            return text

        def _replace(match: re.Match) -> str:
            token = match.group(0)
            canonical = variant_to_canonical.get(token.lower())
            if canonical is None:
                return token
            # Preserve leading-uppercase style of the original token.
            if token[0].isupper() and canonical and not canonical[0].isupper():
                canonical = canonical[0].upper() + canonical[1:]
            return canonical

        return self._WORD_RE.sub(_replace, text)

    # --- Helpers ---

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> VocabEntry:
        try:
            variants = json.loads(row["variants"]) if row["variants"] else []
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"corrupt variants JSON for term {row['term']!r}; treating as empty")
            variants = []
        return VocabEntry(
            id=row["id"],
            term=row["term"],
            variants=variants,
            language=row["language"],
            project=row["project"],
            category=row["category"],
            confidence=row["confidence"],
            usage_count=row["usage_count"],
            created_at=row["created_at"],
            last_used=row["last_used"],
        )
