"""FastAPI router exposing prompt evolution cold store data via HTTP.

Mounted conditionally in the SAM gateway (``main.py:_setup_routers``)
when ``lifesci_tools`` is importable (i.e. MedExpert deployments).

Endpoints expose read-only views of prompt version history, per-agent
rolling scores, promotion audit logs, and a dashboard overview.
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from lifesci_common.constants import (
    EVOLUTION_COMPOSITE_THRESHOLD,
    EVOLVABLE_AGENTS,
)
from lifesci_tools.cold_store import (
    approve_candidate,
    get_active_prompt,
    get_aggregate_score,
    get_connection,
    get_feedback_stats,
    get_pending_candidates,
    get_prompt_history,
    get_rolling_scores,
    get_strategy_by_type,
    reject_candidate,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/evolution", tags=["prompt-evolution"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = "data/medexpert-cold.db"


def _db_path() -> str:
    return os.environ.get("COLD_DB_PATH", _DEFAULT_DB_PATH)


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class AgentSummary(BaseModel):
    name: str
    active_version: int | None = None
    composite_score: float | None = None
    is_baseline: bool = True


class AgentsResponse(BaseModel):
    agents: list[AgentSummary]


class ScoreEntry(BaseModel):
    session_id: str
    entity: float | None = None
    fidelity: float | None = None
    citation: float | None = None
    quality: float | None = None
    llm_judge: float | None = None
    composite: float
    created_at: str


class AgentScoresResponse(BaseModel):
    agent_name: str
    active_version: int | None = None
    scores: list[ScoreEntry]


class VersionEntry(BaseModel):
    version: int
    is_active: bool = False
    is_baseline: bool = False
    created_at: str
    instruction_preview: str = ""


class AgentHistoryResponse(BaseModel):
    agent_name: str
    versions: list[VersionEntry]


class PromotionEntry(BaseModel):
    agent_name: str
    from_version: int
    to_version: int
    reason: str
    composite_before: float | None = None
    composite_after: float | None = None
    promoted_at: str


class PromotionsResponse(BaseModel):
    promotions: list[PromotionEntry]


class OverviewResponse(BaseModel):
    total_agents: int
    evolved_count: int
    avg_composite: float | None = None
    promotions_last_24h: int
    agents_below_threshold: int


class HealthResponse(BaseModel):
    status: str
    db_path: str
    session_count: int | None = None
    error: str | None = None


class CandidateEntry(BaseModel):
    agent_name: str
    version: int
    instruction_preview: str = ""
    regression_score: float | None = None
    failure_feedback: str = ""
    created_at: str


class CandidatesResponse(BaseModel):
    candidates: list[CandidateEntry]


class ApprovalRequest(BaseModel):
    reviewer: str = ""


class RejectionRequest(BaseModel):
    reviewer: str = ""
    reason: str = ""


class CalibrationSuggestion(BaseModel):
    agent: str
    domain: str
    action: str
    current_weight: float
    suggested_weight: float
    negative_rate: float
    sample_count: int
    reason: str


class CalibrationResponse(BaseModel):
    status: str
    window_days: int
    suggestions: list[CalibrationSuggestion]


class FeedbackSummaryResponse(BaseModel):
    total: int
    upvotes: int
    downvotes: int
    per_domain: dict[str, dict[str, int]]
    window_days: int


class SourceWeightEntry(BaseModel):
    source: str
    weight: float
    validity_ratio: float
    mean_grade: float
    pass_rate: float
    sessions: int


class SourceReliabilityResponse(BaseModel):
    sources: list[SourceWeightEntry]


# ---------------------------------------------------------------------------
# Helper: open and close connection per request
# ---------------------------------------------------------------------------


def _open_conn() -> sqlite3.Connection:
    """Open a cold store connection; caller must close it."""
    return get_connection(_db_path())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Check if the cold store DB is accessible."""
    db = _db_path()
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        row = conn.execute("SELECT COUNT(*) AS cnt FROM session_outcomes").fetchone()
        session_count = row["cnt"] if row else 0
        return HealthResponse(status="ok", db_path=db, session_count=session_count)
    except Exception as exc:
        log.warning("Evolution health check failed: %s", exc)
        return HealthResponse(status="error", db_path=db, error=str(exc))
    finally:
        if conn:
            conn.close()


@router.get("/agents", response_model=AgentsResponse)
async def list_agents():
    """List all evolvable agents with active prompt version and latest composite score."""
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        agents: list[AgentSummary] = []

        for agent_name in EVOLVABLE_AGENTS:
            active = get_active_prompt(conn, agent_name)
            composite: float | None = None
            active_version: int | None = None
            is_baseline = True

            if active:
                active_version = active["version"]
                is_baseline = active.get("is_baseline", False)
                agg = get_aggregate_score(conn, agent_name, active["version"])
                if agg.get("sample_count", 0) > 0:
                    composite = agg.get("avg_composite")

            agents.append(
                AgentSummary(
                    name=agent_name,
                    active_version=active_version,
                    composite_score=composite,
                    is_baseline=is_baseline,
                )
            )

        return AgentsResponse(agents=agents)
    except Exception as exc:
        log.exception("Error listing agents: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list agents",
        ) from exc
    finally:
        if conn:
            conn.close()


@router.get("/agents/{agent_name}/scores", response_model=AgentScoresResponse)
async def agent_scores(
    agent_name: str,
    limit: int = Query(
        default=10, ge=1, le=100, description="Number of recent sessions"
    ),
):
    """Rolling scores for a specific agent (last N sessions)."""
    if agent_name not in EVOLVABLE_AGENTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_name}' is not an evolvable agent",
        )

    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        active = get_active_prompt(conn, agent_name)
        active_version = active["version"] if active else None

        raw_scores = get_rolling_scores(conn, agent_name, n_sessions=limit)
        scores = [
            ScoreEntry(
                session_id=s["session_id"],
                entity=s.get("entity"),
                fidelity=s.get("fidelity"),
                citation=s.get("citation"),
                quality=s.get("quality"),
                llm_judge=s.get("llm_judge"),
                composite=s["composite"],
                created_at=s.get("created_at", ""),
            )
            for s in raw_scores
        ]

        return AgentScoresResponse(
            agent_name=agent_name,
            active_version=active_version,
            scores=scores,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Error fetching scores for %s: %s", agent_name, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch agent scores",
        ) from exc
    finally:
        if conn:
            conn.close()


@router.get("/agents/{agent_name}/history", response_model=AgentHistoryResponse)
async def agent_history(
    agent_name: str,
    limit: int = Query(
        default=10, ge=1, le=100, description="Number of versions to return"
    ),
):
    """Prompt version history for a specific agent."""
    if agent_name not in EVOLVABLE_AGENTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_name}' is not an evolvable agent",
        )

    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        raw_versions = get_prompt_history(conn, agent_name, limit=limit)
        versions = [
            VersionEntry(
                version=v["version"],
                is_active=v.get("is_active", False),
                is_baseline=v.get("is_baseline", False),
                created_at=v.get("created_at", ""),
                instruction_preview=v.get("instruction", "")[:100],
            )
            for v in raw_versions
        ]

        return AgentHistoryResponse(agent_name=agent_name, versions=versions)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Error fetching history for %s: %s", agent_name, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch prompt history",
        ) from exc
    finally:
        if conn:
            conn.close()


@router.get("/promotions", response_model=PromotionsResponse)
async def list_promotions(
    limit: int = Query(
        default=20, ge=1, le=100, description="Number of promotions to return"
    ),
):
    """Recent promotion audit log, newest first."""
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        rows = conn.execute(
            """SELECT agent_name, from_version, to_version, reason,
                      composite_before, composite_after, promoted_at
               FROM prompt_promotions
               ORDER BY promoted_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        promotions = [
            PromotionEntry(
                agent_name=r["agent_name"],
                from_version=r["from_version"],
                to_version=r["to_version"],
                reason=r["reason"],
                composite_before=r["composite_before"],
                composite_after=r["composite_after"],
                promoted_at=r["promoted_at"],
            )
            for r in rows
        ]

        return PromotionsResponse(promotions=promotions)
    except Exception as exc:
        log.exception("Error listing promotions: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list promotions",
        ) from exc
    finally:
        if conn:
            conn.close()


@router.get("/overview", response_model=OverviewResponse)
async def overview():
    """Dashboard summary: total agents, evolved count, avg composite, recent promotions."""
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()

        total_agents = len(EVOLVABLE_AGENTS)
        evolved_count = 0
        composites: list[float] = []
        below_threshold = 0

        for agent_name in EVOLVABLE_AGENTS:
            active = get_active_prompt(conn, agent_name)
            if active and not active.get("is_baseline", True):
                evolved_count += 1

            if active:
                agg = get_aggregate_score(conn, agent_name, active["version"])
                avg_c = agg.get("avg_composite")
                if agg.get("sample_count", 0) > 0 and avg_c is not None:
                    composites.append(avg_c)
                    if avg_c < EVOLUTION_COMPOSITE_THRESHOLD:
                        below_threshold += 1

        avg_composite: float | None = None
        if composites:
            avg_composite = round(sum(composites) / len(composites), 4)

        # Promotions in last 24 hours
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM prompt_promotions WHERE promoted_at >= ?",
            (cutoff,),
        ).fetchone()
        promotions_last_24h = row["cnt"] if row else 0

        return OverviewResponse(
            total_agents=total_agents,
            evolved_count=evolved_count,
            avg_composite=avg_composite,
            promotions_last_24h=promotions_last_24h,
            agents_below_threshold=below_threshold,
        )
    except Exception as exc:
        log.exception("Error building overview: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build overview",
        ) from exc
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Candidate approval endpoints (Phase 3)
# ---------------------------------------------------------------------------


@router.get("/candidates", response_model=CandidatesResponse)
async def list_candidates():
    """List prompt evolution candidates awaiting human approval."""
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        raw = get_pending_candidates(conn)
        candidates = [
            CandidateEntry(
                agent_name=c["agent_name"],
                version=c["version"],
                instruction_preview=c.get("instruction", "")[:200],
                regression_score=(
                    c.get("metadata", {}) or {}
                ).get("regression", {}).get("avg_score"),
                failure_feedback=(
                    (c.get("metadata", {}) or {}).get("failure_feedback", "")
                ),
                created_at=c.get("created_at", ""),
            )
            for c in raw
        ]
        return CandidatesResponse(candidates=candidates)
    except Exception as exc:
        log.exception("Error listing candidates: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list candidates",
        ) from exc
    finally:
        if conn:
            conn.close()


@router.post("/candidates/{agent_name}/{version}/approve")
async def approve_prompt_candidate(
    agent_name: str,
    version: int,
    body: ApprovalRequest | None = None,
):
    """Approve and promote a candidate prompt version."""
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        reviewer = body.reviewer if body else ""
        success = approve_candidate(conn, agent_name, version, reviewer=reviewer)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No candidate found for {agent_name} v{version}",
            )
        return {"status": "approved", "agent_name": agent_name, "version": version}
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Error approving candidate: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to approve candidate",
        ) from exc
    finally:
        if conn:
            conn.close()


@router.post("/candidates/{agent_name}/{version}/reject")
async def reject_prompt_candidate(
    agent_name: str,
    version: int,
    body: RejectionRequest | None = None,
):
    """Reject a candidate prompt version."""
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        reviewer = body.reviewer if body else ""
        reason = body.reason if body else ""
        success = reject_candidate(
            conn, agent_name, version, reviewer=reviewer, reason=reason
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No candidate found for {agent_name} v{version}",
            )
        return {"status": "rejected", "agent_name": agent_name, "version": version}
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Error rejecting candidate: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reject candidate",
        ) from exc
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Calibration dashboard endpoints (Phase 4)
# ---------------------------------------------------------------------------


@router.get("/calibration", response_model=CalibrationResponse)
async def calibration_overview(
    window_days: int = Query(default=90, ge=7, le=365),
):
    """Specialist calibration analysis and weight suggestions."""
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        from lifesci_tools.specialist_calibrator import SpecialistCalibrator

        calibrator = SpecialistCalibrator()
        result = calibrator.calibrate(conn, window_days=window_days)
        suggestions = [
            CalibrationSuggestion(
                agent=s["agent"],
                domain=domain,
                action=s["action"],
                current_weight=s["current_weight"],
                suggested_weight=s["weight"],
                negative_rate=s["negative_rate"],
                sample_count=s["sample_count"],
                reason=s["reason"],
            )
            for domain in result.get("domains", [])
            for s in (
                get_strategy_by_type(conn, "specialist_weights", domain) or {}
            ).get("strategy", [])
        ]
        return CalibrationResponse(
            status="ok",
            window_days=window_days,
            suggestions=suggestions,
        )
    except Exception as exc:
        log.exception("Error computing calibration: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute calibration",
        ) from exc
    finally:
        if conn:
            conn.close()


@router.get("/feedback/summary", response_model=FeedbackSummaryResponse)
async def feedback_summary(
    window_days: int = Query(default=90, ge=7, le=365),
):
    """Aggregated user feedback statistics."""
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        stats = get_feedback_stats(conn, window_days=window_days)
        return FeedbackSummaryResponse(
            total=stats.get("total", 0),
            upvotes=stats.get("upvotes", 0),
            downvotes=stats.get("downvotes", 0),
            per_domain=stats.get("per_domain", {}),
            window_days=window_days,
        )
    except Exception as exc:
        log.exception("Error computing feedback summary: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute feedback summary",
        ) from exc
    finally:
        if conn:
            conn.close()


@router.get("/sources/reliability", response_model=SourceReliabilityResponse)
async def source_reliability():
    """Source reliability rankings with calibrated weights."""
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_conn()
        raw = get_strategy_by_type(conn, "source_weights", "_global")
        sources = []
        if raw:
            for s in raw.get("strategy", []):
                sources.append(
                    SourceWeightEntry(
                        source=s["source"],
                        weight=s["weight"],
                        validity_ratio=s.get("validity_ratio", 0.0),
                        mean_grade=s.get("mean_grade", 0.0),
                        pass_rate=s.get("pass_rate", 0.0),
                        sessions=s.get("sessions", 0),
                    )
                )
        return SourceReliabilityResponse(sources=sources)
    except Exception as exc:
        log.exception("Error fetching source reliability: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch source reliability",
        ) from exc
    finally:
        if conn:
            conn.close()
