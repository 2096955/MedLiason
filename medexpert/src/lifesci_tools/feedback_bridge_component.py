"""SAM flow component that bridges gateway feedback to the cold store.

This component subscribes to the gateway's feedback broker topic, extracts
the feedback payload, enriches it with session context from the cold store,
and enqueues it to the cold_worker for persistence.

It runs as a standard SAM flow — same process model as any other agent
component — eliminating the cross-process monkey-patch problem.
"""

import logging

from solace_ai_connector.components.component_base import ComponentBase

from lifesci_tools.feedback_bridge import bridge_feedback_to_cold_store

log = logging.getLogger(__name__)

info = {
    "class_name": "FeedbackBridgeComponent",
    "description": (
        "Receives user feedback events from the gateway broker topic "
        "and persists them to the MedExpert cold store for learning loops."
    ),
    "config_parameters": [],
    "input_schema": {
        "type": "object",
        "properties": {
            "event_body": {
                "type": "object",
                "description": "Feedback event published by the gateway",
            },
        },
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
        },
    },
}


class FeedbackBridgeComponent(ComponentBase):
    """Process feedback events from the gateway broker topic."""

    def __init__(self, **kwargs):
        super().__init__(info, **kwargs)

    def invoke(self, message, data):
        """Called by SAM for each message received on the subscribed topic.

        The gateway's ``_publish_event`` sends a JSON payload with::

            {
                "event_type": "feedback_submitted",
                "task_id": "...",
                "session_id": "...",
                "user_id": "...",
                "feedback_type": "thumbs_up" | "thumbs_down",
                "feedback_text": "...",
                "timestamp": "..."
            }
        """
        try:
            # SAM broker input delivers the payload under different shapes
            # depending on broker config.  Try the standard paths.
            payload = data if isinstance(data, dict) else {}
            if not payload:
                payload = (
                    message.get_payload()
                    if hasattr(message, "get_payload")
                    else {}
                )

            if not payload or payload.get("event_type") != "feedback_submitted":
                log.debug(
                    "Ignoring non-feedback event: %s", payload.get("event_type")
                )
                return {"success": False, "reason": "not a feedback event"}

            # Normalise feedback_type: gateway sends "thumbs_up"/"thumbs_down",
            # cold store expects "up"/"down"
            raw_type = payload.get("feedback_type", "thumbs_up")
            feedback_type = "down" if "down" in raw_type else "up"

            session_id = payload.get("session_id", "")
            if not session_id:
                log.warning("Feedback event missing session_id — skipping")
                return {"success": False, "reason": "missing session_id"}

            ok = bridge_feedback_to_cold_store(
                session_id=session_id,
                task_id=payload.get("task_id", ""),
                feedback_type=feedback_type,
                feedback_text=payload.get("feedback_text", ""),
            )

            if ok:
                log.info(
                    "Bridged feedback for session %s (type=%s)",
                    session_id,
                    feedback_type,
                )
            else:
                log.debug(
                    "Feedback bridge returned False (cold store not configured?)"
                )

            return {"success": ok}

        except Exception:
            log.exception("Error processing feedback bridge event")
            return {"success": False, "reason": "exception"}
