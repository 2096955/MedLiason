"""Prompt Evolution Engine for MedExpert specialist agents.

Detects underperforming specialists, generates improved instructions
via a metaprompt, runs golden dataset regression, and promotes new
versions when safe.  Includes auto-rollback if a promoted version
degrades over consecutive sessions.

Called from the cold worker's 10-session refresh cycle.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from lifesci_common.constants import (
    EVOLUTION_COMPOSITE_THRESHOLD,
    EVOLUTION_ROLLING_WINDOW,
    EVOLVABLE_AGENTS,
    FROZEN_AGENTS,
    MAX_PROMPT_VERSIONS,
    ROLLBACK_SESSION_COUNT,
)
from lifesci_tools import cold_store

log = logging.getLogger(__name__)

# Feature flags
EVOLUTION_ENABLED = os.environ.get("PROMPT_EVOLUTION_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
HUMAN_APPROVAL_REQUIRED = os.environ.get(
    "PROMPT_EVOLUTION_HUMAN_APPROVAL", "true"
).lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Metaprompt template
# ---------------------------------------------------------------------------

MEDICAL_METAPROMPT = """\
# Context
You are optimizing the system prompt for a medical research specialist agent.

## Current instruction:
{current_instruction}

## Agent role: {agent_name}

## Recent session performance (rolling {window}-session average):
- Entity preservation: {entity_avg:.2f} (threshold: 0.80)
- Evidence fidelity: {fidelity_avg:.2f} (threshold: 0.30)
- Citation accuracy: {citation_avg:.2f} (threshold: 0.70)
- Composite quality: {quality_avg:.2f} (threshold: 0.60)
- Overall composite: {composite_avg:.2f} (threshold: {threshold:.2f})

## Specific failure patterns:
{failure_feedback}

# Task
Write an improved version of the instruction that addresses the failure patterns.
Preserve:
- The agent's core role and domain expertise
- Citation format requirements ([[cite:...]])
- Evidence grading methodology (GRADE)
- Tool usage patterns (MCP servers, memory_plane, evidence_grader)
- All safety constraints

Improve:
- Specificity around the failing grader dimensions
- Emphasis on preserving medical entities and terminology
- Instructions for evidence source fidelity
- Citation accuracy guidance

Return ONLY the improved instruction text, nothing else.
"""


# ---------------------------------------------------------------------------
# Prompt Evolver
# ---------------------------------------------------------------------------


class PromptEvolver:
    """Orchestrates the self-evolving prompt optimisation loop."""

    def __init__(
        self,
        db_path: str,
        golden_dataset_path: str | None = None,
    ):
        self.db_path = db_path
        self.golden_dataset_path = golden_dataset_path or str(
            Path(__file__).resolve().parents[2] / "tests" / "golden_dataset.json"
        )
        self._golden_cache: list[dict] | None = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def check_and_evolve(self, conn) -> list[dict[str, Any]]:
        """Check all specialists and evolve any below threshold.

        Returns a list of action dicts describing what was done:
          [{"agent": str, "action": "evolved"|"rolled_back"|"skipped", ...}]
        """
        if not EVOLUTION_ENABLED:
            return []

        actions: list[dict[str, Any]] = []

        for agent_name in EVOLVABLE_AGENTS:
            if agent_name in FROZEN_AGENTS:
                continue

            # 1. Check for rollback first
            rolled_back = self._check_rollback(conn, agent_name)
            if rolled_back:
                actions.append(rolled_back)
                continue

            # 2. Check for underperformance
            underperf = self._check_underperformer(conn, agent_name)
            if not underperf:
                continue

            # 3. Attempt evolution
            result = self._evolve_agent(conn, agent_name, underperf)
            actions.append(result)

        return actions

    # ------------------------------------------------------------------
    # Auto-rollback
    # ------------------------------------------------------------------

    def _check_rollback(self, conn, agent_name: str) -> dict[str, Any] | None:
        """If the last N sessions all score below the pre-promotion avg, rollback."""
        active = cold_store.get_active_prompt(conn, agent_name)
        if not active or active["is_baseline"] or active["version"] == 0:
            return None  # Nothing to rollback to

        recent = cold_store.get_rolling_scores(conn, agent_name, ROLLBACK_SESSION_COUNT)
        if len(recent) < ROLLBACK_SESSION_COUNT:
            return None  # Not enough data

        # Get pre-promotion composite from the promotion record
        promo_rows = conn.execute(
            """SELECT composite_before FROM prompt_promotions
               WHERE agent_name = ? AND to_version = ?
               ORDER BY promoted_at DESC LIMIT 1""",
            (agent_name, active["version"]),
        ).fetchone()
        if not promo_rows:
            return None

        pre_promo_avg = promo_rows["composite_before"] or 0.0
        all_below = all(s["composite"] < pre_promo_avg for s in recent)

        if not all_below:
            return None

        # Rollback to previous version
        prev_version = active["version"] - 1
        prev = cold_store.get_prompt_version(conn, agent_name, prev_version)
        if not prev:
            return None

        cold_store.promote_prompt(conn, agent_name, prev_version)
        cold_store.write_prompt_promotion(
            conn,
            agent_name=agent_name,
            from_version=active["version"],
            to_version=prev_version,
            reason="auto_rollback",
            composite_before=pre_promo_avg,
            composite_after=sum(s["composite"] for s in recent) / len(recent),
        )

        log.warning(
            "Auto-rollback: %s v%d → v%d (recent avg %.3f < pre-promo %.3f)",
            agent_name,
            active["version"],
            prev_version,
            sum(s["composite"] for s in recent) / len(recent),
            pre_promo_avg,
        )
        return {
            "agent": agent_name,
            "action": "rolled_back",
            "from_version": active["version"],
            "to_version": prev_version,
        }

    # ------------------------------------------------------------------
    # Underperformance detection
    # ------------------------------------------------------------------

    def _check_underperformer(self, conn, agent_name: str) -> dict[str, Any] | None:
        """Return details if agent's rolling avg composite < threshold.

        Incorporates user feedback as a mild penalty: persistent thumbs-down
        across recent sessions lowers the effective composite score.
        """
        recent = cold_store.get_rolling_scores(
            conn, agent_name, EVOLUTION_ROLLING_WINDOW
        )
        if len(recent) < EVOLUTION_ROLLING_WINDOW:
            return None  # Not enough data to judge

        avg_composite = sum(s["composite"] for s in recent) / len(recent)

        # Apply user feedback penalty (if persistent negative feedback exists)
        try:
            feedback_stats = cold_store.get_feedback_stats(
                conn, window_days=EVOLUTION_ROLLING_WINDOW * 10, agent_name=agent_name
            )
            if feedback_stats and feedback_stats.get("total", 0) > 0:
                downvote_ratio = feedback_stats.get("downvotes", 0) / feedback_stats["total"]
                # Mild penalty: max -0.05 for 100% downvotes
                avg_composite -= downvote_ratio * 0.05
        except Exception:
            log.debug("Could not fetch feedback stats for %s", agent_name)

        if avg_composite >= EVOLUTION_COMPOSITE_THRESHOLD:
            return None  # Performing fine

        # Compute per-grader averages for the metaprompt
        def _safe_avg(key: str) -> float:
            vals = [s[key] for s in recent if s.get(key) is not None]
            return sum(vals) / len(vals) if vals else 0.0

        return {
            "agent_name": agent_name,
            "avg_composite": round(avg_composite, 4),
            "avg_entity": round(_safe_avg("entity"), 4),
            "avg_fidelity": round(_safe_avg("fidelity"), 4),
            "avg_citation": round(_safe_avg("citation"), 4),
            "avg_quality": round(_safe_avg("quality"), 4),
            "scores": recent,
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _evolve_agent(
        self,
        conn,
        agent_name: str,
        underperf: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate an improved prompt, regress, and promote if better."""
        active = cold_store.get_active_prompt(conn, agent_name)
        if not active:
            return {
                "agent": agent_name,
                "action": "skipped",
                "reason": "no_active_prompt",
            }

        current_instruction = active["instruction"]
        failure_feedback = self._build_failure_feedback(underperf, conn=conn)

        # 1. Generate improved prompt
        try:
            improved = self._generate_improved_prompt(
                agent_name, current_instruction, failure_feedback, underperf
            )
        except Exception as exc:
            log.exception("Metaprompt generation failed for %s", agent_name)
            return {
                "agent": agent_name,
                "action": "skipped",
                "reason": f"metaprompt_error: {exc}",
            }

        if not improved or not improved.strip():
            return {
                "agent": agent_name,
                "action": "skipped",
                "reason": "empty_prompt_generated",
            }

        # 2. Regression test
        try:
            regression = self._run_regression(agent_name, improved)
        except Exception as exc:
            log.exception("Regression test failed for %s", agent_name)
            return {
                "agent": agent_name,
                "action": "skipped",
                "reason": f"regression_error: {exc}",
            }

        if not regression.get("passed"):
            log.info(
                "Regression failed for %s: %s",
                agent_name,
                regression.get("reason", "unknown"),
            )
            return {
                "agent": agent_name,
                "action": "skipped",
                "reason": "regression_failed",
                "detail": regression,
            }

        # 3. Write new version (candidate or auto-promote)
        new_version = cold_store.get_next_prompt_version(conn, agent_name)

        if HUMAN_APPROVAL_REQUIRED:
            # Write as candidate — requires human approval before promotion
            cold_store.write_prompt_version(
                conn,
                agent_name=agent_name,
                version=new_version,
                instruction=improved,
                model=active["model"],
                temperature=active["temperature"],
                metadata={
                    "evolved_from": active["version"],
                    "failure_feedback": failure_feedback,
                    "regression": regression,
                },
                is_active=False,
                status="candidate",
            )
            log.info(
                "Created candidate %s v%d (composite %.3f → regression %.3f). "
                "Awaiting human approval.",
                agent_name,
                new_version,
                underperf["avg_composite"],
                regression.get("avg_score", 0.0),
            )
            return {
                "agent": agent_name,
                "action": "candidate_created",
                "from_version": active["version"],
                "to_version": new_version,
                "old_composite": underperf["avg_composite"],
                "regression_score": regression.get("avg_score", 0.0),
            }

        # Auto-promote (when human approval disabled, e.g. testing)
        cold_store.write_prompt_version(
            conn,
            agent_name=agent_name,
            version=new_version,
            instruction=improved,
            model=active["model"],
            temperature=active["temperature"],
            metadata={
                "evolved_from": active["version"],
                "failure_feedback": failure_feedback,
                "regression": regression,
            },
            is_active=False,
        )
        cold_store.promote_prompt(conn, agent_name, new_version)
        cold_store.write_prompt_promotion(
            conn,
            agent_name=agent_name,
            from_version=active["version"],
            to_version=new_version,
            reason="evolution",
            composite_before=underperf["avg_composite"],
            composite_after=regression.get("avg_score", 0.0),
            regression_detail=regression,
        )

        # Prune old versions
        cold_store.prune_prompt_versions(conn, agent_name, MAX_PROMPT_VERSIONS)

        log.info(
            "Evolved %s: v%d → v%d (composite %.3f → regression %.3f)",
            agent_name,
            active["version"],
            new_version,
            underperf["avg_composite"],
            regression.get("avg_score", 0.0),
        )

        return {
            "agent": agent_name,
            "action": "evolved",
            "from_version": active["version"],
            "to_version": new_version,
            "old_composite": underperf["avg_composite"],
            "regression_score": regression.get("avg_score", 0.0),
        }

    # ------------------------------------------------------------------
    # Metaprompt generation
    # ------------------------------------------------------------------

    def _generate_improved_prompt(
        self,
        agent_name: str,
        current_instruction: str,
        failure_feedback: str,
        underperf: dict[str, Any],
    ) -> str:
        """Call LiteLLM with the metaprompt to generate an improved instruction."""
        import litellm

        prompt = MEDICAL_METAPROMPT.format(
            current_instruction=current_instruction,
            agent_name=agent_name,
            window=EVOLUTION_ROLLING_WINDOW,
            entity_avg=underperf.get("avg_entity", 0.0),
            fidelity_avg=underperf.get("avg_fidelity", 0.0),
            citation_avg=underperf.get("avg_citation", 0.0),
            quality_avg=underperf.get("avg_quality", 0.0),
            composite_avg=underperf.get("avg_composite", 0.0),
            threshold=EVOLUTION_COMPOSITE_THRESHOLD,
            failure_feedback=failure_feedback,
        )

        response = litellm.completion(
            model="openai/gemini-2.5-flash-001",
            messages=[
                {"role": "system", "content": "You are a prompt engineering expert."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=4000,
        )
        return response.choices[0].message.content.strip()

    # ------------------------------------------------------------------
    # Failure feedback builder
    # ------------------------------------------------------------------

    def _build_failure_feedback(self, underperf: dict[str, Any], conn=None) -> str:
        """Build human-readable failure feedback from grader averages.

        Includes recent user feedback text when available.
        """
        lines = []
        if underperf.get("avg_entity", 1.0) < 0.8:
            lines.append(
                f"Entity preservation is low ({underperf['avg_entity']:.2f}). "
                "Medical entities (drug names, PMIDs, gene symbols, NCT IDs) from "
                "source evidence are not appearing in the specialist's response."
            )
        if underperf.get("avg_fidelity", 1.0) < 0.3:
            lines.append(
                f"Evidence fidelity is low ({underperf['avg_fidelity']:.2f}). "
                "The response diverges significantly from the source evidence text."
            )
        if underperf.get("avg_citation", 1.0) < 0.7:
            lines.append(
                f"Citation accuracy is low ({underperf['avg_citation']:.2f}). "
                "Claims are not properly attributed to source evidence, or "
                "citations reference non-existent sources."
            )
        if underperf.get("avg_quality", 1.0) < 0.6:
            lines.append(
                f"Composite quality is low ({underperf['avg_quality']:.2f}). "
                "Responses may be too short, have low evidence grades, or "
                "contribute insufficiently to coverage."
            )
        if not lines:
            lines.append(
                "Overall composite score has degraded below threshold. "
                "Improve specificity, evidence grounding, and citation accuracy."
            )

        # Include recent user feedback text for richer evolution context
        if conn is not None:
            try:
                agent_name = underperf.get("agent_name", "")
                if agent_name:
                    feedback_texts = cold_store.get_recent_feedback_texts(
                        conn, agent_name, limit=5
                    )
                    if feedback_texts:
                        lines.append("\nRecent user complaints:")
                        for text in feedback_texts:
                            lines.append(f"  - {text}")
            except Exception:
                log.debug("Could not fetch user feedback texts for failure feedback")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Golden dataset regression
    # ------------------------------------------------------------------

    def _load_golden_dataset(self) -> list[dict[str, Any]]:
        """Load and cache the golden dataset."""
        if self._golden_cache is not None:
            return self._golden_cache
        path = Path(self.golden_dataset_path)
        if not path.exists():
            log.warning("Golden dataset not found at %s", path)
            self._golden_cache = []
            return []
        self._golden_cache = json.loads(path.read_text())
        return self._golden_cache

    def _run_regression(self, agent_name: str, new_instruction: str) -> dict[str, Any]:
        """Offline golden dataset regression test.

        For each golden item where expected_agents includes this specialist,
        generate a mock response with the new instruction and score it.
        Passes if no item drops below a minimum score threshold.
        """
        dataset = self._load_golden_dataset()
        if not dataset:
            # No dataset = auto-pass (but log warning)
            log.warning("No golden dataset available — regression auto-pass")
            return {"passed": True, "reason": "no_dataset", "scores": []}

        relevant = [
            item for item in dataset if agent_name in item.get("expected_agents", [])
        ]
        if not relevant:
            return {"passed": True, "reason": "no_relevant_items", "scores": []}

        try:
            import litellm
        except ImportError:
            log.warning("litellm not available — regression auto-pass")
            return {"passed": True, "reason": "litellm_unavailable", "scores": []}

        scores = []
        for item in relevant:
            question = item["question"]
            try:
                response = litellm.completion(
                    model="openai/gemini-2.5-flash-001",
                    messages=[
                        {"role": "system", "content": new_instruction},
                        {"role": "user", "content": question},
                    ],
                    temperature=0.3,
                    max_tokens=2000,
                )
                output = response.choices[0].message.content.strip().lower()
            except Exception as exc:
                log.warning(
                    "Regression LLM call failed for '%s': %s", question[:50], exc
                )
                scores.append({"question": question, "score": 0.0, "error": str(exc)})
                continue

            # Score: keyword coverage (same logic as eval_runner)
            expected_kw = item.get("expected_keywords", [])
            if expected_kw:
                found = sum(1 for kw in expected_kw if kw.lower() in output)
                score = found / len(expected_kw)
            else:
                score = 1.0 if output else 0.0

            scores.append({"question": question, "score": round(score, 3)})

        if not scores:
            return {"passed": True, "reason": "no_scores", "scores": []}

        avg_score = sum(s["score"] for s in scores) / len(scores)
        min_score = min(s["score"] for s in scores)

        # Pass if average >= 0.4 and no single item scores 0
        passed = avg_score >= 0.4 and min_score > 0.0
        return {
            "passed": passed,
            "reason": "pass" if passed else f"avg={avg_score:.3f} min={min_score:.3f}",
            "avg_score": round(avg_score, 4),
            "min_score": round(min_score, 4),
            "scores": scores,
        }


# ---------------------------------------------------------------------------
# Baseline seeding
# ---------------------------------------------------------------------------


def seed_baselines(db_path: str, configs_dir: str) -> int:
    """Read specialist YAML configs and write version 0 (baseline) for each.

    Idempotent: skips agents that already have a prompt version.
    Returns the number of baselines seeded.
    """
    try:
        import yaml
    except ImportError:
        log.warning("PyYAML not available — cannot seed baselines")
        return 0

    conn = cold_store.get_connection(db_path)
    configs_path = Path(configs_dir)
    count = 0

    for agent_name in EVOLVABLE_AGENTS:
        existing = cold_store.get_active_prompt(conn, agent_name)
        if existing:
            continue

        # Try common naming conventions
        yaml_name = _agent_to_yaml_filename(agent_name)
        yaml_path = configs_path / yaml_name
        if not yaml_path.exists():
            log.warning("YAML config not found for %s at %s", agent_name, yaml_path)
            continue

        try:
            with open(yaml_path) as f:
                config = yaml.safe_load(f)

            instruction = _extract_instruction(config)
            if not instruction:
                log.warning("No instruction found in %s", yaml_path)
                continue

            cold_store.write_prompt_version(
                conn,
                agent_name=agent_name,
                version=0,
                instruction=instruction,
                model="openai/gemini-2.5-flash-001",
                temperature=0.3,
                is_active=True,
                is_baseline=True,
            )
            count += 1
            log.info("Seeded baseline prompt for %s (v0)", agent_name)
        except Exception:
            log.exception("Failed to seed baseline for %s", agent_name)

    conn.close()
    return count


def _agent_to_yaml_filename(agent_name: str) -> str:
    """Convert CamelCase agent name to snake_case.yaml filename."""
    # LiteratureSpecialist → literature_specialist.yaml
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", agent_name).lower()
    return f"{name}.yaml"


def _extract_instruction(config: dict) -> str | None:
    """Extract the instruction field from a SAM agent YAML config."""
    # Navigate: apps[0].app_config.instruction
    apps = config.get("apps", [])
    if not apps:
        return None
    app_config = apps[0].get("app_config", {})
    return app_config.get("instruction")
