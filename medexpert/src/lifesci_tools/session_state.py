"""Session State Tool — hot-path session state for all agents.

Focused subset of the memory plane providing session-scoped key-value
storage for the ``intermediate`` and ``verification`` namespaces.

Operations: store, retrieve, list_keys, append, clear_session.

Shares the singleton backend from ``MemoryPlaneTool`` — do NOT create
separate Redis connections.
"""

import json
import logging
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

log = logging.getLogger(__name__)

_ALLOWED_NAMESPACES = ("intermediate", "verification")

# ── Deterministic protocol step mapping for session-state keys ────────
_KEY_STEP_MAP = {
    ("intermediate", "specialists_used"): (3, "COLLECT", "Gathering evidence from specialists"),
    ("intermediate", "query_domain"): (3, "COLLECT", "Gathering evidence from specialists"),
    ("intermediate", "coverage_pct"): (3, "COLLECT", "Checking research completeness"),
    ("verification", "verification_verdict"): (5, "VERIFY", "Verifying claims against sources"),
    ("verification", "verification_score"): (5, "VERIFY", "Verifying claims against sources"),
    ("intermediate", "revision_triggered"): (5, "VERIFY", "Revising flagged claims"),
    ("verification", "re_verification_verdict"): (5, "VERIFY", "Re-verifying revised report"),
}


class SessionStateTool(DynamicTool):
    """Hot-path session state for intermediate and verification namespaces."""

    def __init__(self, tool_config=None):
        super().__init__(tool_config=tool_config)
        self._ttl_seconds = int(
            self.tool_config.get("ttl_seconds", 3600) if self.tool_config else 3600
        )

    @property
    def tool_name(self) -> str:
        return "session_state"

    @property
    def tool_description(self) -> str:
        return (
            "Hot-path session state tool for storing and retrieving working data "
            "during the research protocol. Supports operations: store, retrieve, "
            "list_keys, append, clear_session.\n\n"
            "Namespaces:\n"
            "- intermediate: working data (specialists_used, coverage_pct, query_domain, "
            "revision_triggered). Keys are agent-scoped: {session}:{agent}:{namespace}:{key}.\n"
            "- verification: verification verdicts and scores from the GVR loop. "
            "Keys are shared (not agent-scoped) for cross-agent access.\n\n"
            "Operations:\n"
            "- store: write a value to namespace + key (idempotent if value unchanged)\n"
            "- retrieve: fetch a single value by namespace + key\n"
            "- list_keys: list all keys in a namespace\n"
            "- append: add an item to a JSON array at namespace + key\n"
            "- clear_session: delete all keys for the current session"
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "operation": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Operation: store, retrieve, list_keys, append, clear_session",
                ),
                "key": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Key name (required for store, retrieve, append)",
                    nullable=True,
                ),
                "value": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Value to store (JSON string). Required for store/append.",
                    nullable=True,
                ),
                "namespace": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Namespace: intermediate (default) or verification.",
                    nullable=True,
                ),
            },
            required=["operation"],
        )

    async def init(self, component, tool_config):
        """Ensure the shared backend from MemoryPlaneTool is available."""
        if tool_config:
            self.tool_config = tool_config
        self._ttl_seconds = int(
            self.tool_config.get("ttl_seconds", 3600) if self.tool_config else 3600
        )
        # Reuse MemoryPlaneTool's backend singleton
        from lifesci_tools.memory_plane import MemoryPlaneTool, DictBackend

        if MemoryPlaneTool._backend is None:
            MemoryPlaneTool._backend = DictBackend()
            log.info("SessionStateTool initialized shared DictBackend (no Redis)")

    @property
    def _backend(self):
        from lifesci_tools.memory_plane import MemoryPlaneTool, DictBackend

        if MemoryPlaneTool._backend is None:
            MemoryPlaneTool._backend = DictBackend()
        return MemoryPlaneTool._backend

    def _make_key(
        self, session_id: str, namespace: str, key: str, agent_name: str = ""
    ) -> str:
        """Build a storage key.

        Both ``intermediate`` and ``verification`` namespaces use shared
        (unscoped) keys for backward compatibility with ``memory_plane``
        and ``cold_query`` signal collection. The orchestrator is the
        sole writer of well-known signal keys (coverage_pct, etc.), so
        agent-scoping is unnecessary today.
        """
        return f"medexpert:{session_id}:{namespace}:{key}"

    def _session_pattern(self, session_id: str, namespace: str | None = None) -> str:
        if namespace:
            return f"medexpert:{session_id}:{namespace}:*"
        return f"medexpert:{session_id}:*"

    # ------------------------------------------------------------------
    # Deterministic protocol progress emission
    # ------------------------------------------------------------------

    async def _emit_protocol_progress(
        self,
        tool_context: ToolContext,
        step: int,
        step_name: str,
        detail: str,
        session_id: str,
    ) -> None:
        """Emit ResearchProtocolProgressData via SSE."""
        try:
            a2a_context = tool_context.state.get("a2a_context")
            if not a2a_context:
                return
            inv = getattr(tool_context, "_invocation_context", None)
            if not inv:
                return
            agent = getattr(inv, "agent", None)
            host = getattr(agent, "host_component", None) if agent else None
            if not host:
                return

            from solace_agent_mesh.common.data_parts import (
                ResearchProtocolProgressData,
            )

            coverage_pct = None
            verdict = None
            try:
                raw_cp = await self._backend.get(
                    f"medexpert:{session_id}:intermediate:coverage_pct"
                )
                if raw_cp:
                    coverage_pct = float(raw_cp)
                raw_v = await self._backend.get(
                    f"medexpert:{session_id}:verification:verification_verdict"
                )
                if raw_v:
                    verdict = raw_v
            except Exception:
                pass

            progress = ResearchProtocolProgressData(
                step=step,
                step_name=step_name,
                total_steps=7,
                detail=detail,
                coverage_pct=coverage_pct,
                gvr_cycle=0,
                verification_verdict=verdict,
            )

            host.publish_data_signal_from_thread(
                a2a_context=a2a_context,
                signal_data=progress,
                skip_buffer_flush=False,
                log_identifier="[SessionState:Progress]",
            )
        except Exception as exc:
            log.debug("[SessionState:Progress] Could not emit progress: %s", exc)

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        operation = args.get("operation", "").lower()
        key = args.get("key", "")
        value = args.get("value", "")
        namespace = args.get("namespace", "intermediate")
        session_id = getattr(tool_context, "session", None)
        session_id = (
            getattr(session_id, "id", "default") if session_id else "default"
        )

        if namespace not in _ALLOWED_NAMESPACES:
            return {
                "success": False,
                "error": (
                    f"Invalid namespace '{namespace}' for session_state. "
                    f"Must be one of: {_ALLOWED_NAMESPACES}"
                ),
            }

        valid_ops = ("store", "retrieve", "list_keys", "append", "clear_session")
        if operation not in valid_ops:
            return {
                "success": False,
                "error": (
                    f"Unknown operation '{operation}'. "
                    f"session_state supports: {', '.join(valid_ops)}"
                ),
            }

        # ── Deterministic protocol progress emission ──────────────
        step_num = None
        if operation in ("store", "append") and (namespace, key) in _KEY_STEP_MAP:
            step_num, step_name, detail = _KEY_STEP_MAP[(namespace, key)]
            await self._emit_protocol_progress(
                tool_context, step_num, step_name, detail, session_id
            )

        # ── Reactive step advancement for protocol validator ──────
        if step_num is not None:
            try:
                inv = getattr(tool_context, "_invocation_context", None)
                session_obj = getattr(inv, "session", None) if inv else None
                if session_obj and hasattr(session_obj, "state"):
                    current = session_obj.state.get("_protocol_current_step", 0)
                    if step_num > current:
                        session_obj.state["_protocol_current_step"] = step_num
            except Exception:
                pass

        # Derive agent name for scoped keys
        agent_name = ""
        try:
            inv = getattr(tool_context, "_invocation_context", None)
            agent = getattr(inv, "agent", None) if inv else None
            agent_name = getattr(agent, "name", "") if agent else ""
        except Exception:
            pass

        if operation == "store":
            if not key:
                return {"success": False, "error": "Key is required for store"}
            full_key = self._make_key(session_id, namespace, key, agent_name)
            existing = await self._backend.get(full_key)
            if existing == value:
                return {
                    "success": True,
                    "key": key,
                    "namespace": namespace,
                    "idempotent": True,
                }
            await self._backend.set(full_key, value, ex=self._ttl_seconds)
            return {"success": True, "key": key, "namespace": namespace}

        elif operation == "retrieve":
            if not key:
                return {"success": False, "error": "Key is required for retrieve"}
            full_key = self._make_key(session_id, namespace, key, agent_name)
            stored = await self._backend.get(full_key)
            if stored is None:
                return {"success": True, "found": False, "value": None}
            return {"success": True, "found": True, "value": stored}

        elif operation == "list_keys":
            pattern = self._session_pattern(session_id, namespace)
            raw_keys = await self._backend.keys(pattern)
            # Strip prefix to return user-friendly names
            # Handle both agent-scoped and shared patterns
            keys = []
            for k in raw_keys:
                # Find the namespace part and extract key after it
                ns_marker = f":{namespace}:"
                idx = k.find(ns_marker)
                if idx >= 0:
                    keys.append(k[idx + len(ns_marker):])
            return {"success": True, "namespace": namespace, "keys": keys}

        elif operation == "clear_session":
            pattern = self._session_pattern(session_id)
            all_keys = await self._backend.keys(pattern)
            if all_keys:
                await self._backend.delete(*all_keys)
            return {"success": True, "cleared": len(all_keys)}

        elif operation == "append":
            if not key:
                return {"success": False, "error": "Key is required for append"}
            full_key = self._make_key(session_id, namespace, key, agent_name)
            existing = await self._backend.get(full_key)
            if existing:
                try:
                    existing_list = json.loads(existing)
                    if not isinstance(existing_list, list):
                        existing_list = [existing_list]
                except (json.JSONDecodeError, TypeError):
                    existing_list = [existing]
            else:
                existing_list = []

            try:
                new_item = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                new_item = value

            existing_list.append(new_item)
            await self._backend.set(
                full_key, json.dumps(existing_list), ex=self._ttl_seconds
            )
            return {
                "success": True,
                "key": key,
                "namespace": namespace,
                "length": len(existing_list),
            }

        # Should not reach here given the valid_ops check above
        return {"success": False, "error": f"Unhandled operation: {operation}"}
