"""Evidence Store Tool — evidence and citation management.

Focused subset of the memory plane for the ``citations`` and ``evidence``
namespaces. Used by the orchestrator at COLLECT (step 3) and the verifier
at VERIFY (step 5).

Operations: store, retrieve, list_keys.
Keys are shared (not agent-scoped) — all agents see the same data.

Shares the singleton backend from ``MemoryPlaneTool``.
"""

import logging
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

log = logging.getLogger(__name__)

_ALLOWED_NAMESPACES = ("citations", "evidence")


class EvidenceStoreTool(DynamicTool):
    """Evidence and citation management for the research pipeline."""

    def __init__(self, tool_config=None):
        super().__init__(tool_config=tool_config)
        self._read_only = bool(
            self.tool_config.get("read_only", False) if self.tool_config else False
        )
        self._ttl_seconds = int(
            self.tool_config.get("ttl_seconds", 3600) if self.tool_config else 3600
        )

    @property
    def tool_name(self) -> str:
        return "evidence_store"

    @property
    def tool_description(self) -> str:
        if self._read_only:
            return (
                "Read-only evidence store for retrieving citations and graded evidence "
                "accumulated by specialist agents. Supports operations: retrieve, list_keys.\n\n"
                "Namespaces:\n"
                "- citations: published source references with citation IDs (s0r0, s0r1, ...)\n"
                "- evidence: graded evidence items and citation_map_json for the verifier"
            )
        return (
            "Evidence and citation store for managing research evidence. "
            "Supports operations: store, retrieve, list_keys.\n\n"
            "Keys are shared across all agents (not agent-scoped).\n\n"
            "Namespaces:\n"
            "- citations: published source references with citation IDs (s0r0, s0r1, ...)\n"
            "- evidence: graded evidence items and citation_map_json for the verifier\n\n"
            "Operations:\n"
            "- store: write a value to namespace + key (idempotent if value unchanged)\n"
            "- retrieve: fetch a single value by namespace + key\n"
            "- list_keys: list all keys in a namespace"
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "operation": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Operation: store, retrieve, list_keys",
                ),
                "key": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Key name (required for store, retrieve)",
                    nullable=True,
                ),
                "value": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Value to store (JSON string). Required for store.",
                    nullable=True,
                ),
                "namespace": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Namespace: citations or evidence. Default: evidence.",
                    nullable=True,
                ),
            },
            required=["operation"],
        )

    async def init(self, component, tool_config):
        """Ensure the shared backend from MemoryPlaneTool is available."""
        if tool_config:
            self.tool_config = tool_config
        self._read_only = bool(
            self.tool_config.get("read_only", False) if self.tool_config else False
        )
        self._ttl_seconds = int(
            self.tool_config.get("ttl_seconds", 3600) if self.tool_config else 3600
        )
        from lifesci_tools.memory_plane import MemoryPlaneTool, DictBackend

        if MemoryPlaneTool._backend is None:
            MemoryPlaneTool._backend = DictBackend()
            log.info("EvidenceStoreTool initialized shared DictBackend (no Redis)")

    @property
    def _backend(self):
        from lifesci_tools.memory_plane import MemoryPlaneTool, DictBackend

        if MemoryPlaneTool._backend is None:
            MemoryPlaneTool._backend = DictBackend()
        return MemoryPlaneTool._backend

    def _make_key(self, session_id: str, namespace: str, key: str) -> str:
        return f"medexpert:{session_id}:{namespace}:{key}"

    def _session_pattern(self, session_id: str, namespace: str) -> str:
        return f"medexpert:{session_id}:{namespace}:*"

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        operation = args.get("operation", "").lower()
        key = args.get("key", "")
        value = args.get("value", "")
        namespace = args.get("namespace", "evidence")
        session_id = getattr(tool_context, "session", None)
        session_id = (
            getattr(session_id, "id", "default") if session_id else "default"
        )

        if namespace not in _ALLOWED_NAMESPACES:
            return {
                "success": False,
                "error": (
                    f"Invalid namespace '{namespace}' for evidence_store. "
                    f"Must be one of: {_ALLOWED_NAMESPACES}"
                ),
            }

        valid_ops = ("store", "retrieve", "list_keys")
        if operation not in valid_ops:
            return {
                "success": False,
                "error": (
                    f"Unknown operation '{operation}'. "
                    f"evidence_store supports: {', '.join(valid_ops)}"
                ),
            }

        if self._read_only and operation == "store":
            return {
                "success": False,
                "error": "Evidence store is read-only for this agent. Cannot perform 'store'.",
            }

        if operation == "store":
            if not key:
                return {"success": False, "error": "Key is required for store"}
            full_key = self._make_key(session_id, namespace, key)
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
            full_key = self._make_key(session_id, namespace, key)
            stored = await self._backend.get(full_key)
            if stored is None:
                return {"success": True, "found": False, "value": None}
            return {"success": True, "found": True, "value": stored}

        elif operation == "list_keys":
            pattern = self._session_pattern(session_id, namespace)
            raw_keys = await self._backend.keys(pattern)
            prefix = f"medexpert:{session_id}:{namespace}:"
            keys = [k[len(prefix):] for k in raw_keys if k.startswith(prefix)]
            return {"success": True, "namespace": namespace, "keys": keys}

        return {"success": False, "error": f"Unhandled operation: {operation}"}
