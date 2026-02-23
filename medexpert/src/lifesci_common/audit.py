"""Append-only audit logger for MedExpert.

Writes structured JSONL events to a local file.  Designed for a
straightforward swap to GCS / AlloyDB / Cloud Logging later.

Events are flushed on every call (no background thread) to guarantee
that safety-critical actions are persisted immediately.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("medexpert.audit")

# Default path; overridable via MEDEXPERT_AUDIT_LOG env var or init_audit()
_DEFAULT_AUDIT_PATH = "data/audit.jsonl"
_audit_path: str = os.environ.get("MEDEXPERT_AUDIT_LOG", _DEFAULT_AUDIT_PATH)


def init_audit(path: Optional[str] = None) -> None:
    """Set the audit log file path and ensure the parent directory exists."""
    global _audit_path
    if path:
        _audit_path = path
    if not _audit_path:
        _audit_path = _DEFAULT_AUDIT_PATH
    Path(_audit_path).parent.mkdir(parents=True, exist_ok=True)


def _compute_hash(text: str) -> str:
    """SHA-256 hex digest of input text (for PHI-safe fingerprinting)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit_event(
    *,
    session_id: str,
    user_id: str = "",
    agent_id: str = "",
    action: str,
    resource: str = "",
    outcome: str = "success",
    latency_ms: float = 0.0,
    input_hash: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict:
    """Append one audit record to the JSONL log.

    Parameters
    ----------
    session_id : str
        Session identifier.
    user_id : str
        User or principal identifier (empty for system events).
    agent_id : str
        Agent that performed the action.
    action : str
        E.g. ``"memory_store"``, ``"llm_call"``, ``"report_generate"``,
        ``"source_publish"``, ``"phi_redact"``, ``"input_guard_flag"``,
        ``"rate_limit_deny"``, ``"critical_withhold"``.
    resource : str
        The resource accessed (key name, model name, endpoint, etc.).
    outcome : str
        ``"success"``, ``"denied"``, ``"error"``, ``"withheld"``.
    latency_ms : float
        Wall-clock time for the action.
    input_hash : str | None
        SHA-256 of the input text (never log raw PHI).
    metadata : dict | None
        Arbitrary extra fields (token counts, claim IDs, etc.).

    Returns
    -------
    dict
        The record that was written.
    """
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "action": action,
        "resource": resource,
        "outcome": outcome,
        "latency_ms": round(latency_ms, 1),
    }
    if input_hash:
        record["input_hash"] = input_hash
    if metadata:
        record["meta"] = metadata

    line = json.dumps(record, separators=(",", ":"), default=str)
    try:
        with open(_audit_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        log.warning("Failed to write audit event to %s", _audit_path, exc_info=True)

    return record
