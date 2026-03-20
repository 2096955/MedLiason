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
from typing import Any

log = logging.getLogger(__name__)

# Stop words removed during query normalisation
_STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "may",
        "not",
        "of",
        "on",
        "or",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
    ]
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

CREATE TABLE IF NOT EXISTS prompt_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name    TEXT    NOT NULL,
    version       INTEGER NOT NULL,
    instruction   TEXT    NOT NULL,
    model         TEXT    NOT NULL DEFAULT 'openai/gemini-2.5-flash-001',
    temperature   REAL    NOT NULL DEFAULT 0.3,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    metadata_json TEXT,
    is_active     INTEGER NOT NULL DEFAULT 0,
    is_baseline   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(agent_name, version)
);

CREATE TABLE IF NOT EXISTS prompt_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name      TEXT    NOT NULL,
    prompt_version  INTEGER NOT NULL,
    session_id      TEXT    NOT NULL,
    entity_score    REAL,
    fidelity_score  REAL,
    citation_score  REAL,
    quality_score   REAL,
    llm_judge_score REAL,
    composite_score REAL    NOT NULL,
    grader_detail   TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES session_outcomes(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS prompt_promotions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name            TEXT    NOT NULL,
    from_version          INTEGER NOT NULL,
    to_version            INTEGER NOT NULL,
    reason                TEXT    NOT NULL,
    composite_before      REAL,
    composite_after       REAL,
    regression_passed     INTEGER NOT NULL DEFAULT 1,
    regression_detail     TEXT,
    promoted_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_feedback (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id            TEXT    NOT NULL,
    task_id               TEXT    NOT NULL DEFAULT '',
    feedback_type         TEXT    NOT NULL CHECK(feedback_type IN ('up', 'down')),
    feedback_text         TEXT    NOT NULL DEFAULT '',
    query_domain          TEXT    NOT NULL DEFAULT '',
    specialists_used_json TEXT    NOT NULL DEFAULT '[]',
    verification_verdict  TEXT    NOT NULL DEFAULT '',
    created_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Secondary indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_session_outcomes_created ON session_outcomes(created_at);
CREATE INDEX IF NOT EXISTS idx_session_outcomes_domain ON session_outcomes(query_domain);
CREATE INDEX IF NOT EXISTS idx_source_reliability_domain ON source_reliability(source_name);
CREATE INDEX IF NOT EXISTS idx_source_reliability_session ON source_reliability(session_id);
CREATE INDEX IF NOT EXISTS idx_routing_patterns_domain ON routing_patterns(query_domain);
CREATE INDEX IF NOT EXISTS idx_routing_patterns_agent ON routing_patterns(agent_name);
CREATE INDEX IF NOT EXISTS idx_user_feedback_created ON user_feedback(created_at);
CREATE INDEX IF NOT EXISTS idx_user_feedback_session ON user_feedback(session_id);
CREATE INDEX IF NOT EXISTS idx_query_patterns_domain ON query_patterns(domain);
CREATE INDEX IF NOT EXISTS idx_agent_coactivation_agents ON agent_coactivation(agent_a, agent_b);
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
    # M2: Add prompt evolution tables.  check_sql detects whether the table
    # already exists (e.g. created by _SCHEMA_SQL on a fresh DB).
    (
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prompt_versions'",
        """
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name    TEXT    NOT NULL,
            version       INTEGER NOT NULL,
            instruction   TEXT    NOT NULL,
            model         TEXT    NOT NULL DEFAULT 'openai/gemini-2.5-flash-001',
            temperature   REAL    NOT NULL DEFAULT 0.3,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            metadata_json TEXT,
            is_active     INTEGER NOT NULL DEFAULT 0,
            is_baseline   INTEGER NOT NULL DEFAULT 0,
            UNIQUE(agent_name, version)
        );
        CREATE TABLE IF NOT EXISTS prompt_scores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name      TEXT    NOT NULL,
            prompt_version  INTEGER NOT NULL,
            session_id      TEXT    NOT NULL,
            entity_score    REAL,
            fidelity_score  REAL,
            citation_score  REAL,
            quality_score   REAL,
            llm_judge_score REAL,
            composite_score REAL    NOT NULL,
            grader_detail   TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES session_outcomes(session_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS prompt_promotions (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name            TEXT    NOT NULL,
            from_version          INTEGER NOT NULL,
            to_version            INTEGER NOT NULL,
            reason                TEXT    NOT NULL,
            composite_before      REAL,
            composite_after       REAL,
            regression_passed     INTEGER NOT NULL DEFAULT 1,
            regression_detail     TEXT,
            promoted_at           TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    # M3: Add user_feedback table for learning loop integration.
    (
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_feedback'",
        """
        CREATE TABLE IF NOT EXISTS user_feedback (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id            TEXT    NOT NULL,
            task_id               TEXT    NOT NULL DEFAULT '',
            feedback_type         TEXT    NOT NULL CHECK(feedback_type IN ('up', 'down')),
            feedback_text         TEXT    NOT NULL DEFAULT '',
            query_domain          TEXT    NOT NULL DEFAULT '',
            specialists_used_json TEXT    NOT NULL DEFAULT '[]',
            verification_verdict  TEXT    NOT NULL DEFAULT '',
            created_at            TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    # M4: Add status column to prompt_versions for human approval gate.
    (
        "SELECT 1 FROM pragma_table_info('prompt_versions') WHERE name='status'",
        "ALTER TABLE prompt_versions ADD COLUMN status TEXT NOT NULL DEFAULT 'active';",
    ),
    # M5: Remove FK constraint from user_feedback for existing databases.
    # SQLite lacks ALTER TABLE DROP CONSTRAINT, so we recreate the table.
    # Detects the old schema by checking for "REFERENCES" in the DDL.
    (
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_feedback' "
        "AND sql NOT LIKE '%REFERENCES%'",
        """
        CREATE TABLE IF NOT EXISTS user_feedback_new (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id            TEXT    NOT NULL,
            task_id               TEXT    NOT NULL DEFAULT '',
            feedback_type         TEXT    NOT NULL CHECK(feedback_type IN ('up', 'down')),
            feedback_text         TEXT    NOT NULL DEFAULT '',
            query_domain          TEXT    NOT NULL DEFAULT '',
            specialists_used_json TEXT    NOT NULL DEFAULT '[]',
            verification_verdict  TEXT    NOT NULL DEFAULT '',
            created_at            TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        INSERT OR IGNORE INTO user_feedback_new SELECT * FROM user_feedback;
        DROP TABLE user_feedback;
        ALTER TABLE user_feedback_new RENAME TO user_feedback;
        """,
    ),
    # M6: Add triage_outcomes table for triage pipeline learning.
    (
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='triage_outcomes'",
        """
        CREATE TABLE IF NOT EXISTS triage_outcomes (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id              TEXT    NOT NULL,
            chief_complaint         TEXT    NOT NULL DEFAULT '',
            consensus_diagnosis     TEXT    NOT NULL DEFAULT '',
            mean_confidence         REAL    NOT NULL DEFAULT 0.0,
            nba_route               TEXT    NOT NULL DEFAULT '',
            nba_urgency             TEXT    NOT NULL DEFAULT '',
            flag_for_review         INTEGER NOT NULL DEFAULT 0,
            emergency_override      INTEGER NOT NULL DEFAULT 0,
            specialists_invoked_json TEXT   NOT NULL DEFAULT '[]',
            created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    # M7: Add pending_calibration table for human review gate.
    (
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_calibration'",
        """
        CREATE TABLE IF NOT EXISTS pending_calibration (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name       TEXT    NOT NULL,
            domain           TEXT    NOT NULL,
            current_weight   REAL    NOT NULL,
            suggested_weight REAL    NOT NULL,
            delta            REAL    NOT NULL,
            negative_rate    REAL    NOT NULL DEFAULT 0.0,
            sample_count     INTEGER NOT NULL DEFAULT 0,
            action           TEXT    NOT NULL,
            reason           TEXT    NOT NULL DEFAULT '',
            status           TEXT    NOT NULL DEFAULT 'pending'
                             CHECK(status IN ('pending', 'applied', 'rejected')),
            reviewer         TEXT    NOT NULL DEFAULT '',
            reviewed_at      TEXT,
            created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    # M8: Add report_mode and advisory_perspectives_json to session_outcomes (C2+C6).
    (
        "SELECT 1 FROM pragma_table_info('session_outcomes') WHERE name='report_mode'",
        """
        ALTER TABLE session_outcomes ADD COLUMN report_mode TEXT DEFAULT NULL;
        ALTER TABLE session_outcomes ADD COLUMN advisory_perspectives_json TEXT DEFAULT NULL;
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
    conn.execute("PRAGMA busy_timeout=5000")
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
    specialists_used: list[str] | None = None,
    report_mode: str | None = None,
    advisory_perspectives_json: str | None = None,
) -> None:
    """Insert or replace a session outcome row."""
    conn.execute(
        """INSERT OR REPLACE INTO session_outcomes
           (session_id, query_domain, query_text, coverage_pct,
            verification_verdict, verification_score, revision_triggered,
            specialists_used_json, report_mode, advisory_perspectives_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            query_domain,
            query_text,
            coverage_pct,
            verification_verdict,
            verification_score,
            int(revision_triggered),
            json.dumps(specialists_used or []),
            report_mode,
            advisory_perspectives_json,
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


def write_user_feedback(
    conn: sqlite3.Connection,
    session_id: str,
    task_id: str = "",
    feedback_type: str = "up",
    feedback_text: str = "",
    query_domain: str = "",
    specialists_used: list[str] | None = None,
    verification_verdict: str = "",
) -> int:
    """Insert a user feedback row. Returns the row id."""
    cursor = conn.execute(
        """INSERT INTO user_feedback
           (session_id, task_id, feedback_type, feedback_text,
            query_domain, specialists_used_json, verification_verdict)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            task_id,
            feedback_type,
            feedback_text,
            query_domain,
            json.dumps(specialists_used or []),
            verification_verdict,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_feedback_stats(
    conn: sqlite3.Connection,
    window_days: int = 90,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """Aggregate feedback stats over a rolling window.

    If *agent_name* is provided, only counts feedback for sessions where
    that specialist was used.

    Returns dict with total, upvotes, downvotes, per_domain, and
    per_specialist breakdowns.
    """
    cutoff = f"-{window_days} days"

    # Build base query — optionally filter by specialist
    if agent_name:
        base_where = (
            "WHERE created_at >= datetime('now', ?) "
            "AND specialists_used_json LIKE ?"
        )
        base_params: tuple = (cutoff, f'%"{agent_name}"%')
    else:
        base_where = "WHERE created_at >= datetime('now', ?)"
        base_params = (cutoff,)

    # Totals
    row = conn.execute(
        f"SELECT COUNT(*) AS total, "
        f"SUM(CASE WHEN feedback_type='up' THEN 1 ELSE 0 END) AS upvotes, "
        f"SUM(CASE WHEN feedback_type='down' THEN 1 ELSE 0 END) AS downvotes "
        f"FROM user_feedback {base_where}",
        base_params,
    ).fetchone()
    total = row["total"] if row else 0
    upvotes = row["upvotes"] if row else 0
    downvotes = row["downvotes"] if row else 0

    # Per-domain breakdown
    per_domain: dict[str, dict[str, int]] = {}
    rows = conn.execute(
        f"SELECT query_domain, feedback_type, COUNT(*) AS cnt "
        f"FROM user_feedback {base_where} "
        f"GROUP BY query_domain, feedback_type",
        base_params,
    ).fetchall()
    for r in rows:
        domain = r["query_domain"] or "unknown"
        per_domain.setdefault(domain, {"up": 0, "down": 0})
        per_domain[domain][r["feedback_type"]] = r["cnt"]

    return {
        "total": total,
        "upvotes": upvotes,
        "downvotes": downvotes,
        "per_domain": per_domain,
        "window_days": window_days,
    }


def get_recent_feedback_texts(
    conn: sqlite3.Connection,
    agent_name: str,
    limit: int = 5,
    feedback_type: str = "down",
) -> list[str]:
    """Fetch recent feedback text for sessions involving a specific specialist.

    Defaults to negative (thumbs-down) feedback for evolution signals.
    """
    rows = conn.execute(
        """SELECT feedback_text FROM user_feedback
           WHERE feedback_type = ?
             AND specialists_used_json LIKE ?
             AND feedback_text != ''
           ORDER BY created_at DESC LIMIT ?""",
        (feedback_type, f'%"{agent_name}"%', limit),
    ).fetchall()
    return [r["feedback_text"] for r in rows]


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
) -> dict[str, Any] | None:
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


def get_source_rankings(conn: sqlite3.Connection) -> dict[str, Any] | None:
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


def _row_to_query_intel(row: sqlite3.Row, similarity: float = 1.0) -> dict[str, Any]:
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
) -> dict[str, Any] | None:
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


def get_best_agent_pairs(conn: sqlite3.Connection) -> dict[str, Any] | None:
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
) -> dict[str, Any] | None:
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
# Prompt version helpers
# ---------------------------------------------------------------------------


def write_prompt_version(
    conn: sqlite3.Connection,
    agent_name: str,
    version: int,
    instruction: str,
    model: str = "openai/gemini-2.5-flash-001",
    temperature: float = 0.3,
    metadata: dict[str, Any] | None = None,
    is_active: bool = False,
    is_baseline: bool = False,
    status: str = "active",
) -> int:
    """Insert a new prompt version. Returns the row id.

    The *status* parameter sets the lifecycle state: 'active', 'baseline',
    'candidate' (awaiting approval), 'approved', 'rejected', 'archived'.
    """
    cursor = conn.execute(
        """INSERT INTO prompt_versions
           (agent_name, version, instruction, model, temperature,
            metadata_json, is_active, is_baseline, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent_name,
            version,
            instruction,
            model,
            temperature,
            json.dumps(metadata) if metadata else None,
            int(is_active),
            int(is_baseline),
            status,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_active_prompt(
    conn: sqlite3.Connection, agent_name: str
) -> dict[str, Any] | None:
    """Fetch the currently active prompt version for an agent."""
    row = conn.execute(
        """SELECT id, agent_name, version, instruction, model, temperature,
                  created_at, metadata_json, is_active, is_baseline
           FROM prompt_versions
           WHERE agent_name = ? AND is_active = 1
           ORDER BY version DESC LIMIT 1""",
        (agent_name,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "agent_name": row["agent_name"],
        "version": row["version"],
        "instruction": row["instruction"],
        "model": row["model"],
        "temperature": row["temperature"],
        "created_at": row["created_at"],
        "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else None,
        "is_baseline": bool(row["is_baseline"]),
    }


def get_prompt_version(
    conn: sqlite3.Connection, agent_name: str, version: int
) -> dict[str, Any] | None:
    """Fetch a specific prompt version by agent name and version number."""
    row = conn.execute(
        """SELECT id, agent_name, version, instruction, model, temperature,
                  created_at, metadata_json, is_active, is_baseline
           FROM prompt_versions
           WHERE agent_name = ? AND version = ?""",
        (agent_name, version),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "agent_name": row["agent_name"],
        "version": row["version"],
        "instruction": row["instruction"],
        "model": row["model"],
        "temperature": row["temperature"],
        "created_at": row["created_at"],
        "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else None,
        "is_active": bool(row["is_active"]),
        "is_baseline": bool(row["is_baseline"]),
    }


def get_prompt_history(
    conn: sqlite3.Connection, agent_name: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Fetch recent prompt versions for an agent, newest first."""
    rows = conn.execute(
        """SELECT id, agent_name, version, instruction, model, temperature,
                  created_at, metadata_json, is_active, is_baseline
           FROM prompt_versions
           WHERE agent_name = ?
           ORDER BY version DESC LIMIT ?""",
        (agent_name, limit),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "agent_name": r["agent_name"],
            "version": r["version"],
            "instruction": r["instruction"],
            "model": r["model"],
            "temperature": r["temperature"],
            "created_at": r["created_at"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else None,
            "is_active": bool(r["is_active"]),
            "is_baseline": bool(r["is_baseline"]),
        }
        for r in rows
    ]


def get_next_prompt_version(conn: sqlite3.Connection, agent_name: str) -> int:
    """Return the next version number for an agent (max + 1, or 0 if none)."""
    row = conn.execute(
        "SELECT MAX(version) AS mv FROM prompt_versions WHERE agent_name = ?",
        (agent_name,),
    ).fetchone()
    current_max = row["mv"] if row and row["mv"] is not None else -1
    return current_max + 1


def promote_prompt(conn: sqlite3.Connection, agent_name: str, to_version: int) -> None:
    """Set *to_version* as the active prompt, clearing previous active flag."""
    conn.execute(
        "UPDATE prompt_versions SET is_active = 0 WHERE agent_name = ? AND is_active = 1",
        (agent_name,),
    )
    conn.execute(
        "UPDATE prompt_versions SET is_active = 1 WHERE agent_name = ? AND version = ?",
        (agent_name, to_version),
    )
    conn.commit()


def write_prompt_score(
    conn: sqlite3.Connection,
    agent_name: str,
    prompt_version: int,
    session_id: str,
    scores: dict[str, Any],
) -> int:
    """Insert a prompt score row. Returns the row id."""
    composite = scores.get("composite", 0.0)
    cursor = conn.execute(
        """INSERT INTO prompt_scores
           (agent_name, prompt_version, session_id,
            entity_score, fidelity_score, citation_score,
            quality_score, llm_judge_score, composite_score,
            grader_detail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent_name,
            prompt_version,
            session_id,
            scores.get("entity"),
            scores.get("fidelity"),
            scores.get("citation"),
            scores.get("quality"),
            scores.get("llm_judge"),
            composite,
            json.dumps(scores.get("detail")) if scores.get("detail") else None,
        ),
    )
    return cursor.lastrowid


def update_llm_judge_score(
    conn: sqlite3.Connection,
    agent_name: str,
    session_id: str,
    llm_judge_score: float,
    new_composite: float,
) -> None:
    """Backfill the LLM judge score and recalculated composite."""
    conn.execute(
        """UPDATE prompt_scores
           SET llm_judge_score = ?, composite_score = ?
           WHERE agent_name = ? AND session_id = ?""",
        (llm_judge_score, new_composite, agent_name, session_id),
    )
    conn.commit()


def get_rolling_scores(
    conn: sqlite3.Connection, agent_name: str, n_sessions: int = 5
) -> list[dict[str, Any]]:
    """Fetch the most recent N prompt scores for an agent, newest first."""
    rows = conn.execute(
        """SELECT agent_name, prompt_version, session_id,
                  entity_score, fidelity_score, citation_score,
                  quality_score, llm_judge_score, composite_score,
                  grader_detail, created_at
           FROM prompt_scores
           WHERE agent_name = ?
           ORDER BY created_at DESC LIMIT ?""",
        (agent_name, n_sessions),
    ).fetchall()
    return [
        {
            "agent_name": r["agent_name"],
            "prompt_version": r["prompt_version"],
            "session_id": r["session_id"],
            "entity": r["entity_score"],
            "fidelity": r["fidelity_score"],
            "citation": r["citation_score"],
            "quality": r["quality_score"],
            "llm_judge": r["llm_judge_score"],
            "composite": r["composite_score"],
            "detail": json.loads(r["grader_detail"]) if r["grader_detail"] else None,
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_aggregate_score(
    conn: sqlite3.Connection, agent_name: str, prompt_version: int
) -> dict[str, Any]:
    """Compute aggregate score statistics for a specific prompt version."""
    row = conn.execute(
        """SELECT AVG(composite_score) AS avg_composite,
                  AVG(entity_score) AS avg_entity,
                  AVG(fidelity_score) AS avg_fidelity,
                  AVG(citation_score) AS avg_citation,
                  AVG(quality_score) AS avg_quality,
                  AVG(llm_judge_score) AS avg_llm_judge,
                  COUNT(*) AS sample_count
           FROM prompt_scores
           WHERE agent_name = ? AND prompt_version = ?""",
        (agent_name, prompt_version),
    ).fetchone()
    if not row or row["sample_count"] == 0:
        return {"sample_count": 0}
    return {
        "avg_composite": round(row["avg_composite"], 4)
        if row["avg_composite"]
        else 0.0,
        "avg_entity": round(row["avg_entity"], 4)
        if row["avg_entity"] is not None
        else None,
        "avg_fidelity": round(row["avg_fidelity"], 4)
        if row["avg_fidelity"] is not None
        else None,
        "avg_citation": round(row["avg_citation"], 4)
        if row["avg_citation"] is not None
        else None,
        "avg_quality": round(row["avg_quality"], 4)
        if row["avg_quality"] is not None
        else None,
        "avg_llm_judge": round(row["avg_llm_judge"], 4)
        if row["avg_llm_judge"] is not None
        else None,
        "sample_count": row["sample_count"],
    }


def write_prompt_promotion(
    conn: sqlite3.Connection,
    agent_name: str,
    from_version: int,
    to_version: int,
    reason: str,
    composite_before: float = 0.0,
    composite_after: float = 0.0,
    regression_detail: dict[str, Any] | None = None,
) -> int:
    """Record a prompt promotion in the audit trail. Returns the row id."""
    cursor = conn.execute(
        """INSERT INTO prompt_promotions
           (agent_name, from_version, to_version, reason,
            composite_before, composite_after, regression_passed,
            regression_detail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent_name,
            from_version,
            to_version,
            reason,
            composite_before,
            composite_after,
            1,
            json.dumps(regression_detail) if regression_detail else None,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def prune_prompt_versions(
    conn: sqlite3.Connection,
    agent_name: str,
    max_versions: int = 50,
) -> int:
    """Remove oldest non-baseline, non-active prompt versions beyond max_versions.

    Returns the number of versions deleted.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM prompt_versions WHERE agent_name = ?",
        (agent_name,),
    ).fetchone()
    current = row["cnt"] if row else 0
    if current <= max_versions:
        return 0

    excess = current - max_versions
    cursor = conn.execute(
        """DELETE FROM prompt_versions WHERE id IN (
             SELECT id FROM prompt_versions
             WHERE agent_name = ? AND is_baseline = 0 AND is_active = 0
             ORDER BY version ASC LIMIT ?
           )""",
        (agent_name, excess),
    )
    deleted = cursor.rowcount
    if deleted:
        conn.commit()
    return deleted


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
        "DELETE FROM session_outcomes WHERE created_at < datetime('now', ?)",
        (f"-{max_age_days} days",),
    )
    deleted["age_based"] = cursor.rowcount

    # 2. Count-based pruning (keep newest max_sessions)
    row = conn.execute("SELECT COUNT(*) AS cnt FROM session_outcomes").fetchone()
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
        "DELETE FROM query_patterns WHERE updated_at < datetime('now', ?)",
        (f"-{max_age_days} days",),
    )

    # 4. Prune old agent_coactivation entries
    conn.execute(
        "DELETE FROM agent_coactivation WHERE updated_at < datetime('now', ?)",
        (f"-{max_age_days} days",),
    )

    # 5. Prune old prompt_scores (CASCADE handles session_outcomes deletes,
    #    but also prune scores older than max_age_days independently)
    conn.execute(
        "DELETE FROM prompt_scores WHERE created_at < datetime('now', ?)",
        (f"-{max_age_days} days",),
    )

    # 6. Prune old prompt_promotions audit trail
    conn.execute(
        "DELETE FROM prompt_promotions WHERE promoted_at < datetime('now', ?)",
        (f"-{max_age_days} days",),
    )

    # 7. Prune old user_feedback
    conn.execute(
        "DELETE FROM user_feedback WHERE created_at < datetime('now', ?)",
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


# ---------------------------------------------------------------------------
# Prompt candidate helpers (human approval gate)
# ---------------------------------------------------------------------------


def get_pending_candidates(
    conn: sqlite3.Connection,
    agent_name: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch prompt versions with status='candidate' awaiting human approval."""
    if agent_name:
        rows = conn.execute(
            """SELECT id, agent_name, version, instruction, model, temperature,
                      created_at, metadata_json, is_active, is_baseline, status
               FROM prompt_versions
               WHERE agent_name = ? AND status = 'candidate'
               ORDER BY created_at DESC""",
            (agent_name,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, agent_name, version, instruction, model, temperature,
                      created_at, metadata_json, is_active, is_baseline, status
               FROM prompt_versions
               WHERE status = 'candidate'
               ORDER BY created_at DESC"""
        ).fetchall()
    return [
        {
            "id": r["id"],
            "agent_name": r["agent_name"],
            "version": r["version"],
            "instruction": r["instruction"],
            "model": r["model"],
            "temperature": r["temperature"],
            "created_at": r["created_at"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else None,
            "is_active": bool(r["is_active"]),
            "is_baseline": bool(r["is_baseline"]),
            "status": r["status"],
        }
        for r in rows
    ]


def approve_candidate(
    conn: sqlite3.Connection,
    agent_name: str,
    version: int,
    reviewer: str = "",
) -> bool:
    """Approve a candidate prompt version and promote it to active.

    Returns True if the candidate was found and promoted, False otherwise.
    """
    row = conn.execute(
        """SELECT id, status FROM prompt_versions
           WHERE agent_name = ? AND version = ?""",
        (agent_name, version),
    ).fetchone()
    if not row or row["status"] != "candidate":
        return False

    # Find current active version for audit trail
    prev_active = conn.execute(
        "SELECT version FROM prompt_versions "
        "WHERE agent_name = ? AND is_active = 1",
        (agent_name,),
    ).fetchone()
    from_version = prev_active["version"] if prev_active else 0

    # Archive current active version
    conn.execute(
        """UPDATE prompt_versions SET is_active = 0, status = 'archived'
           WHERE agent_name = ? AND is_active = 1""",
        (agent_name,),
    )
    # Promote the candidate
    conn.execute(
        """UPDATE prompt_versions SET is_active = 1, status = 'active',
              metadata_json = json_set(COALESCE(metadata_json, '{}'),
                  '$.approved_by', ?,
                  '$.approved_at', datetime('now'))
           WHERE agent_name = ? AND version = ?""",
        (reviewer, agent_name, version),
    )

    # Record in prompt_promotions audit trail
    write_prompt_promotion(
        conn,
        agent_name=agent_name,
        from_version=from_version,
        to_version=version,
        reason=f"human_approval (reviewer: {reviewer or 'unknown'})",
    )

    log.info(
        "Approved and promoted %s v%d (reviewer: %s)",
        agent_name, version, reviewer or "unknown",
    )
    return True


def reject_candidate(
    conn: sqlite3.Connection,
    agent_name: str,
    version: int,
    reviewer: str = "",
    reason: str = "",
) -> bool:
    """Reject a candidate prompt version.

    Returns True if the candidate was found and rejected, False otherwise.
    """
    row = conn.execute(
        """SELECT id, status FROM prompt_versions
           WHERE agent_name = ? AND version = ?""",
        (agent_name, version),
    ).fetchone()
    if not row or row["status"] != "candidate":
        return False

    conn.execute(
        """UPDATE prompt_versions SET status = 'rejected',
              metadata_json = json_set(COALESCE(metadata_json, '{}'),
                  '$.rejected_by', ?,
                  '$.rejected_at', datetime('now'),
                  '$.rejection_reason', ?)
           WHERE agent_name = ? AND version = ?""",
        (reviewer, reason, agent_name, version),
    )
    conn.commit()
    log.info(
        "Rejected %s v%d (reviewer: %s, reason: %s)",
        agent_name, version, reviewer or "unknown", reason or "none",
    )
    return True


# ---------------------------------------------------------------------------
# Pending calibration helpers (human review gate)
# ---------------------------------------------------------------------------


def write_pending_calibration(
    conn: sqlite3.Connection,
    *,
    agent_name: str,
    domain: str,
    current_weight: float,
    suggested_weight: float,
    delta: float,
    negative_rate: float = 0.0,
    sample_count: int = 0,
    action: str = "",
    reason: str = "",
) -> int:
    """Insert a calibration entry pending human review.

    Returns the row id of the inserted (or existing) entry.
    Deduplicates: if a pending entry already exists for the same
    (agent_name, domain), returns the existing id without inserting.

    NOTE: Caller is responsible for committing the transaction.
    """
    # Dedup: skip if a pending entry already exists for this agent/domain
    existing = conn.execute(
        "SELECT id FROM pending_calibration "
        "WHERE agent_name = ? AND domain = ? AND status = 'pending'",
        (agent_name, domain),
    ).fetchone()
    if existing:
        log.debug(
            "Pending calibration already exists for %s/%s (id=%d), skipping",
            agent_name,
            domain,
            existing["id"],
        )
        return existing["id"]

    cursor = conn.execute(
        """INSERT INTO pending_calibration
           (agent_name, domain, current_weight, suggested_weight, delta,
            negative_rate, sample_count, action, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent_name,
            domain,
            round(current_weight, 4),
            round(suggested_weight, 4),
            round(delta, 4),
            round(negative_rate, 4),
            sample_count,
            action,
            reason,
        ),
    )
    row_id = cursor.lastrowid or 0
    log.info(
        "Pending calibration %d: %s/%s delta=%.3f (%s)",
        row_id,
        agent_name,
        domain,
        delta,
        action,
    )
    return row_id


def get_pending_calibrations(
    conn: sqlite3.Connection,
    status: str = "pending",
) -> list[dict[str, Any]]:
    """Fetch calibration entries by status (default: pending)."""
    rows = conn.execute(
        """SELECT id, agent_name, domain, current_weight, suggested_weight,
                  delta, negative_rate, sample_count, action, reason,
                  status, reviewer, reviewed_at, created_at
           FROM pending_calibration
           WHERE status = ?
           ORDER BY created_at DESC""",
        (status,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "agent_name": r["agent_name"],
            "domain": r["domain"],
            "current_weight": r["current_weight"],
            "suggested_weight": r["suggested_weight"],
            "delta": r["delta"],
            "negative_rate": r["negative_rate"],
            "sample_count": r["sample_count"],
            "action": r["action"],
            "reason": r["reason"],
            "status": r["status"],
            "reviewer": r["reviewer"],
            "reviewed_at": r["reviewed_at"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def apply_pending_calibration(
    conn: sqlite3.Connection,
    calibration_id: int,
    reviewer: str = "",
) -> bool:
    """Apply a pending calibration: write weight to learned_strategies.

    Uses BEGIN IMMEDIATE to prevent TOCTOU races with concurrent
    calibrator writes to the same domain's learned_strategies row.

    Returns True if applied, False if not found or already resolved.
    """
    # Commit any auto-started transaction, then acquire write lock
    # to prevent TOCTOU race with concurrent calibrator writes
    try:
        conn.commit()
    except Exception:
        pass
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM pending_calibration WHERE id = ? AND status = 'pending'",
            (calibration_id,),
        ).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return False

        agent_name = row["agent_name"]
        domain = row["domain"]
        suggested = row["suggested_weight"]

        # Read current strategy_json for this domain
        existing = conn.execute(
            """SELECT strategy_json FROM learned_strategies
               WHERE strategy_type = 'specialist_weights' AND domain_or_key = ?""",
            (domain,),
        ).fetchone()

        if existing:
            try:
                entries = json.loads(existing["strategy_json"])
            except (json.JSONDecodeError, TypeError):
                entries = []
        else:
            entries = []

        # Update or insert the agent's weight
        found = False
        for entry in entries:
            if entry.get("agent") == agent_name:
                entry["weight"] = round(suggested, 3)
                entry["current_weight"] = round(suggested, 3)
                found = True
                break
        if not found:
            entries.append({
                "agent": agent_name,
                "weight": round(suggested, 3),
                "current_weight": round(suggested, 3),
                "negative_rate": row["negative_rate"],
                "sample_count": row["sample_count"],
                "action": row["action"],
                "reason": f"Applied from pending calibration #{calibration_id}",
            })

        sample_count = sum(e.get("sample_count", 0) for e in entries)
        confidence = min(1.0, sample_count / 20)
        conn.execute(
            """INSERT OR REPLACE INTO learned_strategies
               (strategy_type, domain_or_key, strategy_json, confidence,
                sample_count, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (
                "specialist_weights",
                domain,
                json.dumps(entries),
                round(confidence, 3),
                sample_count,
            ),
        )

        # Mark as applied
        conn.execute(
            """UPDATE pending_calibration
               SET status = 'applied', reviewer = ?, reviewed_at = datetime('now')
               WHERE id = ?""",
            (reviewer, calibration_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log.info(
        "Applied calibration %d: %s/%s weight=%.3f (reviewer: %s)",
        calibration_id,
        agent_name,
        domain,
        suggested,
        reviewer or "unknown",
    )
    return True


def reject_pending_calibration(
    conn: sqlite3.Connection,
    calibration_id: int,
    reviewer: str = "",
    reason: str = "",
) -> bool:
    """Reject a pending calibration.

    Returns True if rejected, False if not found or already resolved.
    """
    row = conn.execute(
        "SELECT id FROM pending_calibration WHERE id = ? AND status = 'pending'",
        (calibration_id,),
    ).fetchone()
    if not row:
        return False

    conn.execute(
        """UPDATE pending_calibration
           SET status = 'rejected', reviewer = ?, reviewed_at = datetime('now'),
               reason = CASE WHEN ? != '' THEN ? ELSE reason END
           WHERE id = ?""",
        (reviewer, reason, reason, calibration_id),
    )
    conn.commit()
    log.info(
        "Rejected calibration %d (reviewer: %s, reason: %s)",
        calibration_id,
        reviewer or "unknown",
        reason or "none",
    )
    return True
