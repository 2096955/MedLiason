"""Specialist Calibrator — per-domain weight adjustment for MedExpert agents.

Adapted from DEFRA's WeightCalibrator pattern.  Computes a blended
negative-signal rate from user feedback (thumbs-down) and automated
verification outcomes, then suggests incremental weight adjustments
per (specialist, domain) pair.

Weights are stored in ``learned_strategies`` with
``strategy_type = "specialist_weights"`` and consumed by
``strategy_seeder.build_seed_payload()`` to influence future routing.

Source reliability weights use ``strategy_type = "source_weights"``
and are consumed by ``source_collector`` to adjust relevance scores.
"""

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (overridable via env vars)
# ---------------------------------------------------------------------------


@dataclass
class CalibrationConfig:
    """Tuneable parameters for specialist calibration."""

    min_samples: int = int(os.environ.get("CALIBRATION_MIN_SAMPLES", "10"))
    negative_threshold: float = float(
        os.environ.get("CALIBRATION_NEGATIVE_THRESHOLD", "0.30")
    )
    positive_threshold: float = float(
        os.environ.get("CALIBRATION_POSITIVE_THRESHOLD", "0.05")
    )
    adjustment_step: float = float(
        os.environ.get("CALIBRATION_ADJUSTMENT_STEP", "0.05")
    )
    min_weight: float = float(os.environ.get("CALIBRATION_MIN_WEIGHT", "0.3"))
    max_weight: float = float(os.environ.get("CALIBRATION_MAX_WEIGHT", "1.0"))
    window_days: int = int(os.environ.get("CALIBRATION_WINDOW_DAYS", "90"))
    feedback_weight: float = float(
        os.environ.get("CALIBRATION_FEEDBACK_WEIGHT", "0.6")
    )
    verification_weight: float = float(
        os.environ.get("CALIBRATION_VERIFICATION_WEIGHT", "0.4")
    )


# ---------------------------------------------------------------------------
# Calibrator
# ---------------------------------------------------------------------------


class SpecialistCalibrator:
    """Computes per-specialist, per-domain weight adjustments."""

    def __init__(self, config: CalibrationConfig | None = None):
        self.cfg = config or CalibrationConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calibrate(
        self,
        conn: sqlite3.Connection,
        window_days: int | None = None,
    ) -> dict[str, Any]:
        """Run specialist calibration and write results to learned_strategies.

        Returns a summary dict with per-specialist suggestions.
        """
        window = window_days or self.cfg.window_days
        cutoff = f"-{window} days"

        # 1. Collect routing patterns with verification scores
        routing_rows = conn.execute(
            """SELECT rp.agent_name, rp.query_domain, rp.coverage_contribution,
                      so.verification_verdict, so.verification_score,
                      rp.session_id
               FROM routing_patterns rp
               JOIN session_outcomes so ON rp.session_id = so.session_id
               WHERE rp.created_at >= datetime('now', ?)""",
            (cutoff,),
        ).fetchall()

        # 2. Collect user feedback joined to sessions
        feedback_rows = conn.execute(
            """SELECT session_id, feedback_type, specialists_used_json
               FROM user_feedback
               WHERE created_at >= datetime('now', ?)""",
            (cutoff,),
        ).fetchall()

        # Build per-session feedback lookup
        session_feedback: dict[str, str] = {}
        for fr in feedback_rows:
            # If multiple feedbacks per session, last one wins
            session_feedback[fr["session_id"]] = fr["feedback_type"]

        # 3. Group by (agent, domain) and compute signals
        groups: dict[tuple[str, str], _AgentDomainStats] = {}

        for row in routing_rows:
            key = (row["agent_name"], row["query_domain"])
            stats = groups.setdefault(key, _AgentDomainStats())
            stats.total += 1

            # Verification signal: failed sessions where specialist had high coverage
            verdict = row["verification_verdict"] or ""
            if verdict in ("CRITICAL_ISSUES", "FAIL") and row["coverage_contribution"] > 0.3:
                stats.verification_failures += 1

            # User feedback signal
            fb = session_feedback.get(row["session_id"])
            if fb == "down":
                stats.feedback_negatives += 1
            elif fb == "up":
                stats.feedback_positives += 1

        # 4. Compute blended negative rate and generate suggestions
        suggestions: list[dict[str, Any]] = []
        domain_weights: dict[str, list[dict[str, Any]]] = {}

        for (agent, domain), stats in groups.items():
            if stats.total < self.cfg.min_samples:
                continue

            # Blended negative rate
            fb_rate = stats.feedback_negatives / stats.total if stats.total else 0.0
            vf_rate = stats.verification_failures / stats.total if stats.total else 0.0
            negative_rate = (
                self.cfg.feedback_weight * fb_rate
                + self.cfg.verification_weight * vf_rate
            )

            # Current weight (default 1.0 if not yet calibrated)
            current = self._get_current_weight(conn, agent, domain)

            # Determine suggestion
            action = "HOLD"
            suggested = current
            reason = ""

            if negative_rate > self.cfg.negative_threshold:
                action = "LOWER_WEIGHT"
                suggested = max(
                    self.cfg.min_weight, current - self.cfg.adjustment_step
                )
                reason = (
                    f"Negative rate {negative_rate:.1%} exceeds threshold "
                    f"{self.cfg.negative_threshold:.0%} "
                    f"(feedback: {fb_rate:.1%}, verification: {vf_rate:.1%})"
                )
            elif negative_rate < self.cfg.positive_threshold:
                action = "RAISE_WEIGHT"
                suggested = min(
                    self.cfg.max_weight, current + self.cfg.adjustment_step
                )
                reason = (
                    f"Negative rate {negative_rate:.1%} below threshold "
                    f"{self.cfg.positive_threshold:.0%}"
                )
            else:
                reason = f"Negative rate {negative_rate:.1%} within acceptable range"

            entry = {
                "agent": agent,
                "weight": round(suggested, 3),
                "negative_rate": round(negative_rate, 4),
                "sample_count": stats.total,
                "action": action,
                "current_weight": round(current, 3),
                "reason": reason,
            }
            suggestions.append(entry)
            domain_weights.setdefault(domain, []).append(entry)

        # 5. Write to learned_strategies
        for domain, entries in domain_weights.items():
            sample_count = sum(e["sample_count"] for e in entries)
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

        conn.commit()
        log.info(
            "Specialist calibration: %d suggestions across %d domains",
            len(suggestions),
            len(domain_weights),
        )
        return {"suggestions": suggestions, "domains": list(domain_weights.keys())}

    def calibrate_sources(
        self,
        conn: sqlite3.Connection,
        window_days: int | None = None,
    ) -> dict[str, Any]:
        """Compute per-source reliability weights from citation validity data.

        Writes to ``learned_strategies`` with ``strategy_type = "source_weights"``.
        """
        window = window_days or self.cfg.window_days
        cutoff = f"-{window} days"

        rows = conn.execute(
            """SELECT source_name,
                      SUM(citations_valid) AS total_valid,
                      SUM(citations_invalid) AS total_invalid,
                      AVG(avg_grade_score) AS mean_grade,
                      AVG(passed_verification) AS pass_rate,
                      COUNT(*) AS sessions
               FROM source_reliability
               WHERE created_at >= datetime('now', ?)
               GROUP BY source_name
               HAVING sessions >= 2""",
            (cutoff,),
        ).fetchall()

        source_weights: list[dict[str, Any]] = []
        for row in rows:
            total_cites = row["total_valid"] + row["total_invalid"]
            validity_ratio = (
                row["total_valid"] / total_cites if total_cites > 0 else 1.0
            )
            # Weight = blend of validity ratio and pass rate
            weight = round(0.6 * validity_ratio + 0.4 * row["pass_rate"], 3)
            weight = max(self.cfg.min_weight, min(self.cfg.max_weight, weight))

            source_weights.append(
                {
                    "source": row["source_name"],
                    "weight": weight,
                    "validity_ratio": round(validity_ratio, 3),
                    "mean_grade": round(row["mean_grade"], 3),
                    "pass_rate": round(row["pass_rate"], 3),
                    "sessions": row["sessions"],
                }
            )

        if source_weights:
            total_sessions = sum(s["sessions"] for s in source_weights)
            confidence = min(1.0, total_sessions / 20)
            conn.execute(
                """INSERT OR REPLACE INTO learned_strategies
                   (strategy_type, domain_or_key, strategy_json, confidence,
                    sample_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                (
                    "source_weights",
                    "_global",
                    json.dumps(source_weights),
                    round(confidence, 3),
                    total_sessions,
                ),
            )
            conn.commit()

        log.info("Source calibration: %d sources weighted", len(source_weights))
        return {"sources": source_weights}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_current_weight(
        self, conn: sqlite3.Connection, agent: str, domain: str
    ) -> float:
        """Look up the current weight for an agent in a domain, default 1.0."""
        row = conn.execute(
            """SELECT strategy_json FROM learned_strategies
               WHERE strategy_type = 'specialist_weights' AND domain_or_key = ?""",
            (domain,),
        ).fetchone()
        if not row:
            return 1.0
        try:
            entries = json.loads(row["strategy_json"])
            for e in entries:
                if e.get("agent") == agent:
                    return e.get("weight", 1.0)
        except (json.JSONDecodeError, TypeError):
            pass
        return 1.0


@dataclass
class _AgentDomainStats:
    """Accumulator for per-(agent, domain) calibration signals."""

    total: int = 0
    verification_failures: int = 0
    feedback_negatives: int = 0
    feedback_positives: int = 0
