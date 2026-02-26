"""Feedback Bridge — connects gateway feedback to cold store learning loops.

This module lives in MedExpert (not SAM core) to maintain proper dependency
direction: application → framework, never framework → application.

Two integration approaches are provided:

1. **Direct call** — ``bridge_feedback_to_cold_store()`` can be called by
   any component that has access to the feedback payload fields.
2. **Post-feedback hook** — ``install_feedback_hook()`` monkey-patches the
   gateway's ``FeedbackService`` to call the bridge after each feedback
   submission.  Call this once at startup (e.g. from a SAM app's ``init``).

Session context enrichment reads from the **cold store** (not Redis hot store)
to avoid TTL expiry issues when users submit feedback after session end.
"""

import json
import logging
import os

log = logging.getLogger(__name__)


def bridge_feedback_to_cold_store(
    session_id: str,
    task_id: str = "",
    feedback_type: str = "up",
    feedback_text: str = "",
) -> bool:
    """Enqueue user feedback to the cold store learning system.

    Enriches with session context from cold store (not hot store).
    Returns True if enqueued, False if cold store is not configured.
    """
    cold_db_path = os.environ.get("COLD_DB_PATH", "")
    if not cold_db_path:
        return False

    from lifesci_tools import cold_store
    from lifesci_tools.cold_worker import enqueue_session_flush

    # Enrich with session context from cold store
    query_domain = ""
    specialists_used: list[str] = []
    verification_verdict = ""

    try:
        conn = cold_store.get_connection(cold_db_path)
        try:
            row = conn.execute(
                "SELECT query_domain, verification_verdict, specialists_used_json "
                "FROM session_outcomes WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row:
                query_domain = row["query_domain"] or ""
                verification_verdict = row["verification_verdict"] or ""
                specialists_used = json.loads(
                    row["specialists_used_json"] or "[]"
                )
        finally:
            conn.close()
    except Exception:
        # Session may not be persisted yet — still enqueue with partial data
        log.debug(
            "Could not enrich feedback for session %s from cold store", session_id
        )

    return enqueue_session_flush(
        {
            "type": "feedback",
            "session_id": session_id,
            "task_id": task_id,
            "feedback_type": feedback_type,
            "feedback_text": feedback_text,
            "query_domain": query_domain,
            "specialists_used": specialists_used,
            "verification_verdict": verification_verdict,
            "cold_db_path": cold_db_path,
        }
    )


_HOOK_INSTALLED = False


def install_feedback_hook() -> bool:
    """Monkey-patch the gateway FeedbackService to bridge feedback to cold store.

    Retained as a **fallback** for single-process deployments where the gateway
    and agents share a process.  The primary integration is the SAM-native
    broker flow defined in ``configs/feedback_bridge.yaml``.

    Includes a double-patch guard — safe to call multiple times.
    Returns True if the hook was installed, False otherwise.
    """
    global _HOOK_INSTALLED
    if _HOOK_INSTALLED:
        return True

    try:
        from solace_agent_mesh.gateway.http_sse.services.feedback_service import (
            FeedbackService,
        )
    except ImportError:
        log.debug("FeedbackService not available — feedback hook not installed")
        return False

    original_process = FeedbackService.process_feedback

    async def _hooked_process_feedback(self, payload, user_id):
        # Call the original method first
        await original_process(self, payload, user_id)

        # Bridge to cold store (non-blocking, errors logged not raised)
        try:
            bridge_feedback_to_cold_store(
                session_id=payload.session_id,
                task_id=payload.task_id,
                feedback_type=payload.feedback_type,
                feedback_text=payload.feedback_text or "",
            )
        except Exception as exc:
            log.debug("Feedback bridge failed: %s", exc)

    FeedbackService.process_feedback = _hooked_process_feedback
    _HOOK_INSTALLED = True
    log.info("Installed feedback bridge hook on FeedbackService")
    return True
