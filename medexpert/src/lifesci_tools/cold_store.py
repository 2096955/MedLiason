"""Cold Store — SQLite-backed persistent storage for session outcomes.

Captures structured signals from each MedExpert session (GRADE scores,
coverage percentages, verification verdicts, routing decisions) and
aggregates them into learned strategies that seed future sessions.

Uses only Python stdlib (sqlite3, json, pathlib) — zero extra dependencies.
"""

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Stop words removed during query normalisation
_STOP_WORDS = frozenset(
    "a an and are as at be by can do does for from has have how in is it "
    "its may not of on or so that the their them then there these this "
    "to was we were what when where which who why will with would".split()
)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_outcomes (
    session_id       TEXT PRIMARY KEY,
    query_domain     TEXT NOT NULL,
    query_text       TEXT NOT NULL DEFAULT '',
    coverage_pct     REAL NOT NULL DEFAULT 0.0,
    verification_verdict TEXT NOT NULL DEFAULT 'UNKNOWN',
    verification_score   REAL NOT NULL DEFAULT 0.0,
    revision_triggered   INTEGER NOT NULL DEFAULT 0,
    specialists_used_json TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS source_reliability (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL,
    source_name      TEXT NOT NULL,
    evidence_count   INTEGER NOT NULL DEFAULT 0,
    avg_grade_score  REAL NOT NULL DEFAULT 0.0,
    citations_valid  INTEGER NOT NULL DEFAULT 0,
    citations_invalid INTEGER NOT NULL DEFAULT 0,
    passed_verification INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES session_outcomes(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS routing_patterns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL,
    query_domain     TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    coverage_contribution REAL NOT NULL DEFAULT 0.0,
    session_verification_score REAL NOT NULL DEFAULT 0.0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES session_outcomes(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS query_patterns (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_template  TEXT UNIQUE NOT NULL,
    domain               TEXT NOT NULL DEFAULT '',
    frequency            INTEGER NOT NULL DEFAULT 1,
    avg_coverage_pct     REAL NOT NULL DEFAULT 0.0,
    avg_verification_score REAL NOT NULL DEFAULT 0.0,
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_coactivation (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_a          TEXT NOT NULL,
    agent_b          TEXT NOT NULL,
    co_used_count    INTEGER NOT NULL DEFAULT 0,
    co_passed_count  INTEGER NOT NULL DEFAULT 0,
    avg_combined_coverage REAL NOT NULL DEFAULT 0.0,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_a, agent_b)
);

CREATE TABLE IF NOT EXISTS learned_strategies (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_type    TEXT NOT NULL,
    domain_or_key    TEXT NOT NULL,
    strategy_json    TEXT NOT NULL DEFAULT '{}',
    confidence       REAL NOT NULL DEFAULT 0.0,
    sample_count     INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(strategy_type, domain_or_key)
);
"""

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

# Track which DB files have been initialised this process to skip DDL repeats.
# Call ``clear_init_cache()`` if a DB is deleted and recreated at runtime.
_initialised_paths: set[str] = set()

# Migrations applied after schema creation.  Each entry is
# (check_sql, migrate_sql) — the check returns rows when the migration
# is already applied.
_MIGRATIONS = [
    # M1: Ensure ON DELETE CASCADE on source_reliability/routing_patterns.
    # SQLite cannot ALTER foreign keys, so we detect the old schema by
    # checking for "ON DELETE CASCADE" in the table DDL.
    (
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_reliability' "
        "AND sql LIKE '%ON DELETE CASCADE%'",
        # Re-create tables with CASCADE.  This drops existing child rows —
        # acceptable because learned_strategies (the real value) is preserved.
        """
        CREATE TABLE IF NOT EXISTS source_reliability_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            avg_grade_score REAL NOT NULL DEFAULT 0.0,
            citations_valid INTEGER NOT NULL DEFAULT 0,
            citations_invalid INTEGER NOT NULL DEFAULT 0,
            passed_verification INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES session_outcomes(session_id) ON DELETE CASCADE
        );
        INSERT OR IGNORE INTO source_reliability_new SELECT * FROM source_reliability;
        DROP TABLE source_reliability;
        ALTER TABLE source_reliability_new RENAME TO source_reliability;

        CREATE TABLE IF NOT EXISTS routing_patterns_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            query_domain TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            coverage_contribution REAL NOT NULL DEFAULT 0.0,
            session_verification_score REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES session_outcomes(session_id) ON DELETE CASCADE
        );
        INSERT OR IGNORE INTO routing_patterns_new SELECT * FROM routing_patterns;
        DROP TABLE routing_patterns;
        ALTER TABLE routing_patterns_new RENAME TO routing_patterns;
        """,
    ),
]


def clear_init_cache() -> None:
    """Clear the DDL initialisation cache.

    Call this if a cold-store DB is deleted and recreated during the
    same process (e.g. disaster recovery).
    """
    _initialised_paths.clear()


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open (or create) the cold-store database and ensure schema exists."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    resolved = str(path.resolve())
    if resolved not in _initialised_paths:
        conn.executescript(_SCHEMA_SQL)
        _apply_migrations(conn)
        _initialised_paths.add(resolved)
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Run pending schema migrations."""
    for check_sql, migrate_sql in _MIGRATIONS:
        row = conn.execute(check_sql).fetchone()
        if not row:
            log.info("Applying cold-store migration: %s...", check_sql[:60])
            conn.executescript(migrate_sql)
            conn.commit()


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def write_session_outcome(
    conn: sqlite3.Connection,
    session_id: str,
    query_domain: str,
    query_text: str = "",
    coverage_pct: float = 0.0,
    verification_verdict: str = "UNKNOWN",
    verification_score: float = 0.0,
    revision_triggered: bool = False,
    specialists_used: Optional[list[str]] = None,
) -> None:
    """Insert or replace a session outcome row."""
    conn.execute(
        """INSERT OR REPLACE INTO session_outcomes
           (session_id, query_domain, query_text, coverage_pct,
            verification_verdict, verification_score, revision_triggered,
            specialists_used_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            query_domain,
            query_text,
            coverage_pct,
            verification_verdict,
            verification_score,
            int(revision_triggered),
            json.dumps(specialists_used or []),
        ),
    )


def write_source_reliability(
    conn: sqlite3.Connection,
    session_id: str,
    source_name: str,
    evidence_count: int = 0,
    avg_grade_score: float = 0.0,
    citations_valid: int = 0,
    citations_invalid: int = 0,
    passed_verification: bool = False,
) -> None:
    """Insert a source reliability row for one MCP source in one session."""
    conn.execute(
        """INSERT INTO source_reliability
           (session_id, source_name, evidence_count, avg_grade_score,
            citations_valid, citations_invalid, passed_verification)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            source_name,
            evidence_count,
            avg_grade_score,
            citations_valid,
            citations_invalid,
            int(passed_verification),
        ),
    )


def write_routing_pattern(
    conn: sqlite3.Connection,
    session_id: str,
    query_domain: str,
    agent_name: str,
    coverage_contribution: float = 0.0,
    session_verification_score: float = 0.0,
) -> None:
    """Insert a routing pattern row for one specialist in one session."""
    conn.execute(
        """INSERT INTO routing_patterns
           (session_id, query_domain, agent_name, coverage_contribution,
            session_verification_score)
           VALUES (?, ?, ?, ?, ?)""",
        (
            session_id,
            query_domain,
            agent_name,
            coverage_contribution,
            session_verification_score,
        ),
    )


def upsert_query_pattern(
    conn: sqlite3.Connection,
    normalized_template: str,
    domain: str = "",
    coverage_pct: float = 0.0,
    verification_score: float = 0.0,
) -> None:
    """Insert or update a query pattern with running averages."""
    conn.execute(
        """INSERT INTO query_patterns
              (normalized_template, domain, frequency,
               avg_coverage_pct, avg_verification_score)
           VALUES (?, ?, 1, ?, ?)
           ON CONFLICT(normalized_template) DO UPDATE SET
              frequency = frequency + 1,
              avg_coverage_pct = (avg_coverage_pct * frequency + ?) / (frequency + 1),
              avg_verification_score = (avg_verification_score * frequency + ?) / (frequency + 1),
              domain = CASE WHEN excluded.domain != '' THEN excluded.domain ELSE domain END,
              updated_at = datetime('now')""",
        (
            normalized_template,
            domain,
            coverage_pct,
            verification_score,
            coverage_pct,
            verification_score,
        ),
    )


def upsert_agent_coactivation(
    conn: sqlite3.Connection,
    agent_a: str,
    agent_b: str,
    passed: bool = False,
    combined_coverage: float = 0.0,
) -> None:
    """Insert or update pairwise specialist co-activation stats."""
    # Ensure deterministic ordering
    a, b = sorted([agent_a, agent_b])
    conn.execute(
        """INSERT INTO agent_coactivation
              (agent_a, agent_b, co_used_count, co_passed_count, avg_combined_coverage)
           VALUES (?, ?, 1, ?, ?)
           ON CONFLICT(agent_a, agent_b) DO UPDATE SET
              co_used_count = co_used_count + 1,
              co_passed_count = co_passed_count + ?,
              avg_combined_coverage =
                  (avg_combined_coverage * co_used_count + ?) / (co_used_count + 1),
              updated_at = datetime('now')""",
        (
            a,
            b,
            int(passed),
            combined_coverage,
            int(passed),
            combined_coverage,
        ),
    )


# ---------------------------------------------------------------------------
# Strategy refresh (materialized aggregates)
# ---------------------------------------------------------------------------


def refresh_learned_strategies(conn: sqlite3.Connection) -> int:
    """Re-compute learned_strategies from raw tables. Returns rows written."""
    rows_written = 0

    # 1. Routing strategies per domain: best agents ranked by avg verification score
    cursor = conn.execute(
        """SELECT query_domain, agent_name,
                  AVG(coverage_contribution) AS avg_coverage,
                  AVG(session_verification_score) AS avg_vscore,
                  COUNT(*) AS cnt
           FROM routing_patterns
           GROUP BY query_domain, agent_name
           HAVING cnt >= 2
           ORDER BY query_domain, avg_vscore DESC"""
    )
    domain_agents: dict[str, list[dict]] = {}
    for row in cursor:
        domain = row["query_domain"]
        domain_agents.setdefault(domain, []).append(
            {
                "agent": row["agent_name"],
                "avg_coverage": round(row["avg_coverage"], 3),
                "avg_verification_score": round(row["avg_vscore"], 3),
                "sample_count": row["cnt"],
            }
        )

    for domain, agents in domain_agents.items():
        sample_count = sum(a["sample_count"] for a in agents)
        confidence = min(1.0, sample_count / 20)
        conn.execute(
            """INSERT OR REPLACE INTO learned_strategies
               (strategy_type, domain_or_key, strategy_json, confidence, sample_count, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            ("routing", domain, json.dumps(agents), round(confidence, 3), sample_count),
        )
        rows_written += 1

    # 2. Source rankings: aggregate reliability across sessions
    cursor = conn.execute(
        """SELECT source_name,
                  SUM(evidence_count) AS total_evidence,
                  AVG(avg_grade_score) AS mean_grade,
                  SUM(citations_valid) AS total_valid,
                  SUM(citations_invalid) AS total_invalid,
                  AVG(passed_verification) AS pass_rate,
                  COUNT(*) AS cnt
           FROM source_reliability
           GROUP BY source_name
           HAVING cnt >= 2
           ORDER BY mean_grade DESC"""
    )
    source_rankings = []
    total_source_samples = 0
    for row in cursor:
        total_source_samples += row["cnt"]
        source_rankings.append(
            {
                "source": row["source_name"],
                "mean_grade": round(row["mean_grade"], 3),
                "total_evidence": row["total_evidence"],
                "valid_citations": row["total_valid"],
                "invalid_citations": row["total_invalid"],
                "pass_rate": round(row["pass_rate"], 3),
                "sessions": row["cnt"],
            }
        )
    if source_rankings:
        confidence = min(1.0, total_source_samples / 20)
        conn.execute(
            """INSERT OR REPLACE INTO learned_strategies
               (strategy_type, domain_or_key, strategy_json, confidence, sample_count, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (
                "source_ranking",
                "_global",
                json.dumps(source_rankings),
                round(confidence, 3),
                total_source_samples,
            ),
        )
        rows_written += 1

    # 3. Best agent pairs per domain
    cursor = conn.execute(
        """SELECT agent_a, agent_b,
                  co_used_count, co_passed_count,
                  avg_combined_coverage
           FROM agent_coactivation
           WHERE co_used_count >= 2
           ORDER BY avg_combined_coverage DESC"""
    )
    pair_data = []
    total_pair_samples = 0
    for row in cursor:
        total_pair_samples += row["co_used_count"]
        pair_data.append(
            {
                "agents": [row["agent_a"], row["agent_b"]],
                "co_used": row["co_used_count"],
                "co_passed": row["co_passed_count"],
                "avg_coverage": round(row["avg_combined_coverage"], 3),
            }
        )
    if pair_data:
        confidence = min(1.0, total_pair_samples / 20)
        conn.execute(
            """INSERT OR REPLACE INTO learned_strategies
               (strategy_type, domain_or_key, strategy_json, confidence, sample_count, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (
                "agent_pairs",
                "_global",
                json.dumps(pair_data),
                round(confidence, 3),
                total_pair_samples,
            ),
        )
        rows_written += 1

    conn.commit()
    log.info("Refreshed learned strategies: %d rows written", rows_written)
    return rows_written


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def get_routing_strategy(
    conn: sqlite3.Connection, domain: str
) -> Optional[dict[str, Any]]:
    """Fetch the routing strategy for a given query domain."""
    row = conn.execute(
        """SELECT strategy_json, confidence, sample_count
           FROM learned_strategies
           WHERE strategy_type = 'routing' AND domain_or_key = ?""",
        (domain,),
    ).fetchone()
    if not row:
        return None
    return {
        "domain": domain,
        "agents": json.loads(row["strategy_json"]),
        "confidence": row["confidence"],
        "sample_count": row["sample_count"],
    }


def get_source_rankings(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    """Fetch the global source reliability rankings."""
    row = conn.execute(
        """SELECT strategy_json, confidence, sample_count
           FROM learned_strategies
           WHERE strategy_type = 'source_ranking' AND domain_or_key = '_global'"""
    ).fetchone()
    if not row:
        return None
    return {
        "sources": json.loads(row["strategy_json"]),
        "confidence": row["confidence"],
        "sample_count": row["sample_count"],
    }


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard index: |intersection| / |union|."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


DEFAULT_SIMILARITY_THRESHOLD = 0.5


def _row_to_query_intel(
    row: sqlite3.Row, similarity: float = 1.0
) -> dict[str, Any]:
    """Convert a query_patterns row to a query intelligence dict."""
    return {
        "template": row["normalized_template"],
        "domain": row["domain"],
        "frequency": row["frequency"],
        "avg_coverage_pct": round(row["avg_coverage_pct"], 3),
        "avg_verification_score": round(row["avg_verification_score"], 3),
        "similarity": similarity,
    }


def get_query_intelligence(
    conn: sqlite3.Connection,
    normalized: str,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> Optional[dict[str, Any]]:
    """Fetch historical stats for a normalised query template.

    Tries exact match first.  If none found, computes Jaccard similarity
    across all stored query_patterns and returns the best match above
    *similarity_threshold*.
    """
    # Fast path: exact match
    row = conn.execute(
        """SELECT normalized_template, domain, frequency,
                  avg_coverage_pct, avg_verification_score
           FROM query_patterns WHERE normalized_template = ?""",
        (normalized,),
    ).fetchone()
    if row:
        return _row_to_query_intel(row, similarity=1.0)

    # Fuzzy fallback: Jaccard over all patterns
    # TODO(perf): This is O(n) where n = number of query_patterns rows. At 10K+ patterns,
    # fetchall() loads everything into memory and iterates. Consider MinHash or BM25 index
    # for large-scale deployments. Current max_sessions=10000 makes this bounded but slow.
    query_tokens = set(normalized.split())
    if not query_tokens:
        return None

    all_rows = conn.execute(
        """SELECT normalized_template, domain, frequency,
                  avg_coverage_pct, avg_verification_score
           FROM query_patterns"""
    ).fetchall()

    best_row = None
    best_sim = 0.0
    for candidate in all_rows:
        candidate_tokens = set(candidate["normalized_template"].split())
        sim = _jaccard_similarity(query_tokens, candidate_tokens)
        if sim > best_sim:
            best_sim = sim
            best_row = candidate

    if best_row and best_sim >= similarity_threshold:
        return _row_to_query_intel(best_row, similarity=round(best_sim, 3))

    return None


def get_best_agent_pairs(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    """Fetch the global best agent pair rankings."""
    row = conn.execute(
        """SELECT strategy_json, confidence, sample_count
           FROM learned_strategies
           WHERE strategy_type = 'agent_pairs' AND domain_or_key = '_global'"""
    ).fetchone()
    if not row:
        return None
    return {
        "pairs": json.loads(row["strategy_json"]),
        "confidence": row["confidence"],
        "sample_count": row["sample_count"],
    }


def get_strategy_by_type(
    conn: sqlite3.Connection, strategy_type: str, domain_or_key: str
) -> Optional[dict[str, Any]]:
    """Generic lookup of any learned strategy by type and key."""
    row = conn.execute(
        """SELECT strategy_json, confidence, sample_count
           FROM learned_strategies
           WHERE strategy_type = ? AND domain_or_key = ?""",
        (strategy_type, domain_or_key),
    ).fetchone()
    if not row:
        return None
    return {
        "strategy_type": strategy_type,
        "domain_or_key": domain_or_key,
        "strategy": json.loads(row["strategy_json"]),
        "confidence": row["confidence"],
        "sample_count": row["sample_count"],
    }


def get_session_count(conn: sqlite3.Connection) -> int:
    """Return total number of session outcomes stored."""
    row = conn.execute("SELECT COUNT(*) AS cnt FROM session_outcomes").fetchone()
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Query normalisation
# ---------------------------------------------------------------------------


def normalize_query(text: str) -> str:
    """Lowercase, remove punctuation, strip stop words, sort tokens."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in _STOP_WORDS]
    return " ".join(sorted(set(tokens)))


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

DEFAULT_MAX_AGE_DAYS = 90
DEFAULT_MAX_SESSIONS = 10_000


def prune_cold_store(
    conn: sqlite3.Connection,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
) -> dict[str, Any]:
    """Delete old session data and enforce row limits.

    Deletes sessions older than *max_age_days* first, then trims the
    oldest sessions if the count still exceeds *max_sessions*.
    Child rows (source_reliability, routing_patterns) are removed
    automatically via ON DELETE CASCADE.

    Returns a dict with counts of deleted rows.
    """
    deleted: dict[str, Any] = {"age_based": 0, "count_based": 0, "vacuumed": False}

    # 1. Age-based pruning
    cursor = conn.execute(
        "DELETE FROM session_outcomes "
        "WHERE created_at < datetime('now', ?)",
        (f"-{max_age_days} days",),
    )
    deleted["age_based"] = cursor.rowcount

    # 2. Count-based pruning (keep newest max_sessions)
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM session_outcomes"
    ).fetchone()
    current_count = row["cnt"] if row else 0

    if current_count > max_sessions:
        excess = current_count - max_sessions
        conn.execute(
            "DELETE FROM session_outcomes WHERE session_id IN ("
            "  SELECT session_id FROM session_outcomes "
            "  ORDER BY created_at ASC LIMIT ?"
            ")",
            (excess,),
        )
        deleted["count_based"] = excess

    # 3. Prune old query_patterns
    conn.execute(
        "DELETE FROM query_patterns "
        "WHERE updated_at < datetime('now', ?)",
        (f"-{max_age_days} days",),
    )

    # 4. Prune old agent_coactivation entries
    conn.execute(
        "DELETE FROM agent_coactivation "
        "WHERE updated_at < datetime('now', ?)",
        (f"-{max_age_days} days",),
    )

    conn.commit()

    total_deleted = deleted["age_based"] + deleted["count_based"]
    if total_deleted > 100:
        conn.execute("VACUUM")
        deleted["vacuumed"] = True

    log.info(
        "Cold store pruned: %d age-based, %d count-based, vacuum=%s",
        deleted["age_based"],
        deleted["count_based"],
        deleted["vacuumed"],
    )
    return deleted
