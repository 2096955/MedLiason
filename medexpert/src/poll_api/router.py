"""
Polling fallback endpoint for task status and events.

When SSE connections drop on Cloud Run, the frontend falls back to polling
this endpoint. It reads from two sources:
  - Redis (via memory_plane): protocol_step, task_complete, final_answer
  - SQL (via TaskRepository): task events for replay, authoritative completion
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as DBSession

from solace_agent_mesh.gateway.http_sse.dependencies import get_db
from solace_agent_mesh.gateway.http_sse.repository.task_repository import TaskRepository

log = logging.getLogger(__name__)

router = APIRouter(prefix="/poll", tags=["Polling Fallback"])

PROTOCOL_STEP_NAMES = {
    0: "SEED",
    1: "PLAN",
    2: "DELEGATE",
    3: "COLLECT",
    4: "SYNTHESIZE",
    5: "VERIFY",
    6: "PERSIST",
}


def _get_redis_backend():
    """Get the Redis backend from memory_plane (if available)."""
    try:
        from lifesci_tools.memory_plane import MemoryPlaneTool
        backend = MemoryPlaneTool._backend
        if backend is not None and hasattr(backend, "get"):
            return backend
    except ImportError:
        pass
    return None


async def _read_redis_signals(session_id: str) -> dict[str, Any]:
    """Read protocol signals from Redis memory plane."""
    result: dict[str, Any] = {
        "protocol_step": None,
        "protocol_step_name": None,
        "task_complete": False,
        "answer_text": None,
    }

    backend = _get_redis_backend()
    if backend is None or not session_id:
        return result

    try:
        # Check task_complete
        complete_key = f"medexpert:{session_id}:intermediate:task_complete"
        val = await backend.get(complete_key)
        if val:
            result["task_complete"] = val.decode("utf-8") == "true" if isinstance(val, bytes) else val == "true"

        # Check final_answer
        answer_key = f"medexpert:{session_id}:intermediate:final_answer"
        val = await backend.get(answer_key)
        if val:
            result["answer_text"] = val.decode("utf-8") if isinstance(val, bytes) else str(val)

        # Check protocol step (stored by protocol_step_validator in session.state)
        # The memory_plane reactive advancement writes step signals, but the
        # canonical step is in session.state. Try to read coverage_pct as a
        # proxy for progress.
        coverage_key = f"medexpert:{session_id}:intermediate:coverage_pct"
        val = await backend.get(coverage_key)
        if val:
            try:
                pct = float(val.decode("utf-8") if isinstance(val, bytes) else val)
                # Estimate step from coverage: 0%=step0, 14%=step1, ..., 100%=step6
                step = min(6, int(pct / 14.3))
                result["protocol_step"] = step
                result["protocol_step_name"] = PROTOCOL_STEP_NAMES.get(step)
            except (ValueError, TypeError):
                pass

        # If task_complete is true, force step to 6
        if result["task_complete"]:
            result["protocol_step"] = 6
            result["protocol_step_name"] = "PERSIST"

    except Exception as exc:
        log.warning("[poll] Failed to read Redis signals: %s", exc)

    return result


def _classify_event(event) -> str:
    """Determine SSE event type from a task event payload."""
    if event.direction == "response" and "result" in event.payload:
        kind = event.payload.get("result", {}).get("kind")
        if kind == "task":
            return "final_response"
        elif kind == "artifact-update":
            return "artifact_update"
    return "status_update"


@router.get("/{task_id}")
async def poll_task(
    task_id: str,
    session_id: str = Query("", description="Session ID for Redis lookup"),
    after: int = Query(0, description="Return events after this timestamp (epoch ms)"),
    db: DBSession = Depends(get_db),
):
    """
    Poll for task status and events. Used as fallback when SSE drops.

    Returns Redis-cached signals (fast) + SQL events (authoritative).
    """
    repo = TaskRepository()
    task = repo.find_by_id(db, task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Determine task completion from SQL (authoritative)
    is_running = task.status in [None, "running", "pending"] and task.end_time is None

    # Read Redis signals (fast, may be ahead of SQL)
    redis_signals = await _read_redis_signals(session_id)

    # Get events since `after` timestamp
    events_since = []
    has_final_event = False
    task_with_events = repo.find_by_id_with_events(db, task_id)
    if task_with_events:
        _, all_events = task_with_events
        for event in all_events:
            if event.created_time > after:
                event_type = _classify_event(event)
                if event_type == "final_response":
                    has_final_event = True
                events_since.append({
                    "event_type": event_type,
                    "payload": event.payload,
                    "created_time": event.created_time,
                })

    # is_complete: true if either SQL says done OR Redis says done
    is_complete = (not is_running) or has_final_event or redis_signals["task_complete"]

    return JSONResponse(content={
        "task_id": task_id,
        "session_id": session_id,
        "is_complete": is_complete,
        "is_running": is_running,
        "protocol_step": redis_signals["protocol_step"],
        "protocol_step_name": redis_signals["protocol_step_name"],
        "answer_text": redis_signals["answer_text"],
        "has_final_event": has_final_event,
        "event_count": len(events_since),
        "events_since": events_since,
    })
