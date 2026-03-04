"""Shared webhook notification helper for MedExpert safety gates.

Fires a POST to ``MEDEXPERT_WEBHOOK_URL`` (env var) with a JSON payload.
Degrades silently if the URL is not set or the request fails.
"""

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

WEBHOOK_URL = os.environ.get("MEDEXPERT_WEBHOOK_URL", "")
WEBHOOK_TIMEOUT = float(os.environ.get("MEDEXPERT_WEBHOOK_TIMEOUT", "5.0"))


def fire_webhook(event_type: str, payload: dict[str, Any]) -> bool:
    """POST a notification to the configured webhook URL.

    Returns True if delivered, False if no URL configured or request failed.
    """
    if not WEBHOOK_URL:
        return False
    try:
        import httpx

        with httpx.Client(timeout=WEBHOOK_TIMEOUT) as client:
            response = client.post(
                WEBHOOK_URL,
                json={"event_type": event_type, **payload},
            )
            response.raise_for_status()
            log.info("Webhook %s delivered to %s", event_type, WEBHOOK_URL)
            return True
    except Exception:
        log.warning(
            "Webhook %s failed (URL: %s)", event_type, WEBHOOK_URL, exc_info=True
        )
        return False
