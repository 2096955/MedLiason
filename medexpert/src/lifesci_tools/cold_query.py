"""Cold Query Tool — read-only cold store historical queries.

Focused subset of the memory plane for the ``learning`` namespace and
cold store operations: seed_session, flush_cold, query_cold, get_strategy.

Used by the orchestrator at SEED (step 0) and PERSIST (step 6).

Shares the singleton backend from ``MemoryPlaneTool``.
"""

import asyncio
import json
import logging
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

log = logging.getLogger(__name__)

# ── Deterministic protocol step mapping for cold operations ───────────
_STEP_MAP = {
    "seed_session": (0, "SEED", "Loading learned strategies from past sessions"),
    "flush_cold": (6, "PERSIST", "Saving session signals to cold store"),
}


class ColdQueryTool(DynamicTool):
    """Cold store historical queries and session lifecycle operations."""

    def __init__(self, tool_config=None):
        super().__init__(tool_config=tool_config)
        self._ttl_seconds = int(
            self.tool_config.get("ttl_seconds", 3600) if self.tool_config else 3600
        )

    @property
    def tool_name(self) -> str:
        return "cold_query"

    @property
    def tool_description(self) -> str:
        return (
            "Cold store tool for historical intelligence and session lifecycle. "
            "Supports operations: seed_session, flush_cold, query_cold, get_strategy.\n\n"
            "Operations:\n"
            "- seed_session: load learned strategies from cold store into the learning "
            "namespace (requires query param). Call ONCE at protocol start (STEP 0).\n"
            "- flush_cold: auto-collect session signals and persist to cold store "
            "(requires query and query_domain params). Call ONCE at protocol end (STEP 6).\n"
            "- query_cold: look up historical intelligence from the cold store "
            "(requires query param)\n"
            "- get_strategy: fetch a learned strategy by type "
            "(routing, source_ranking, agent_pairs) and key\n\n"
            "Namespace: learning (seeded strategies from cold store history).\n\n"
            "Workflow ordering: Call seed_session ONCE at protocol start (STEP 0). "
            "Call flush_cold ONCE at protocol end (STEP 6)."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "operation": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Operation: seed_session, flush_cold, query_cold, get_strategy",
                ),
                "key": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Key name (required for get_strategy — domain or '_global')",
                    nullable=True,
                ),
                "value": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "For flush_cold: optional JSON with extra signals "
                        "(coverage_pct, verification_verdict, verification_score, "
                        "revision_triggered, specialists_used, sources)."
                    ),
                    nullable=True,
                ),
                "query": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "User query text. Required for seed_session and query_cold. "
                        "For flush_cold: the original user question."
                    ),
                    nullable=True,
                ),
                "query_domain": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Primary domain (e.g. 'drugs', 'literature'). Used by flush_cold.",
                    nullable=True,
                ),
                "strategy_type": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "Type of learned strategy for get_strategy: "
                        "routing, source_ranking, agent_pairs."
                    ),
                    nullable=True,
                ),
            },
            required=["operation"],
        )

    async def init(self, component, tool_config):
        """Ensure the shared backend from MemoryPlaneTool is available."""
        if tool_config:
            self.tool_config = tool_config
        self._cold_db_path = (
            self.tool_config.get("cold_db_path", "") if self.tool_config else ""
        )
        self._ttl_seconds = int(
            self.tool_config.get("ttl_seconds", 3600) if self.tool_config else 3600
        )
        from lifesci_tools.memory_plane import MemoryPlaneTool, DictBackend

        if MemoryPlaneTool._backend is None:
            MemoryPlaneTool._backend = DictBackend()
            log.info("ColdQueryTool initialized shared DictBackend (no Redis)")

    @property
    def _backend(self):
        from lifesci_tools.memory_plane import MemoryPlaneTool, DictBackend

        if MemoryPlaneTool._backend is None:
            MemoryPlaneTool._backend = DictBackend()
        return MemoryPlaneTool._backend

    def _make_key(self, session_id: str, namespace: str, key: str) -> str:
        return f"medexpert:{session_id}:{namespace}:{key}"

    def _session_pattern(self, session_id: str, namespace: str | None = None) -> str:
        if namespace:
            return f"medexpert:{session_id}:{namespace}:*"
        return f"medexpert:{session_id}:*"

    # ── Expected signal keys for completeness diagnostics ─────────────
    _EXPECTED_SIGNALS = (
        "verification_verdict",
        "verification_score",
        "coverage_pct",
        "specialists_used",
        "query_domain",
        "revision_triggered",
    )

    async def _collect_session_signals(self, session_id: str) -> dict:
        """Read structured data from all hot-store namespaces for one session.

        Replicates the logic from MemoryPlaneTool._collect_session_signals.
        """
        signals: dict = {}

        ev_keys_coro = self._backend.keys(
            self._session_pattern(session_id, "evidence")
        )
        cit_keys_coro = self._backend.keys(
            self._session_pattern(session_id, "citations")
        )
        vv_coro = self._backend.get(
            self._make_key(session_id, "verification", "verification_verdict")
        )
        vv_alt_coro = self._backend.get(
            self._make_key(session_id, "verification", "verdict")
        )
        vs_coro = self._backend.get(
            self._make_key(session_id, "verification", "verification_score")
        )
        vs_alt_coro = self._backend.get(
            self._make_key(session_id, "verification", "score")
        )
        cp_coro = self._backend.get(
            self._make_key(session_id, "intermediate", "coverage_pct")
        )
        cp_alt_coro = self._backend.get(
            self._make_key(session_id, "intermediate", "completeness_coverage")
        )
        sp_coro = self._backend.get(
            self._make_key(session_id, "intermediate", "specialists_used")
        )
        dom_coro = self._backend.get(
            self._make_key(session_id, "intermediate", "query_domain")
        )
        rev_coro = self._backend.get(
            self._make_key(session_id, "intermediate", "revision_triggered")
        )
        gvr_coro = self._backend.get(
            self._make_key(session_id, "intermediate", "gvr_cycles")
        )
        revv_coro = self._backend.get(
            self._make_key(session_id, "verification", "re_verification_verdict")
        )
        revs_coro = self._backend.get(
            self._make_key(session_id, "verification", "re_verification_score")
        )
        withheld_coro = self._backend.get(
            self._make_key(session_id, "verification", "report_withheld")
        )

        (
            ev_keys,
            cit_keys,
            raw_vv,
            raw_vv_alt,
            raw_vs,
            raw_vs_alt,
            raw_cp,
            raw_cp_alt,
            raw_specs,
            raw_domain,
            raw_rev,
            raw_gvr,
            raw_revv,
            raw_revs,
            raw_withheld,
        ) = await asyncio.gather(
            ev_keys_coro,
            cit_keys_coro,
            vv_coro,
            vv_alt_coro,
            vs_coro,
            vs_alt_coro,
            cp_coro,
            cp_alt_coro,
            sp_coro,
            dom_coro,
            rev_coro,
            gvr_coro,
            revv_coro,
            revs_coro,
            withheld_coro,
        )

        signals["evidence_count"] = len(ev_keys)

        raw_verdict = raw_vv or raw_vv_alt
        if raw_verdict:
            signals["verification_verdict"] = raw_verdict

        raw_score = raw_vs or raw_vs_alt
        if raw_score:
            try:
                signals["verification_score"] = float(raw_score)
            except (ValueError, TypeError):
                log.debug(
                    "Could not parse verification_score=%r for session %s",
                    raw_score,
                    session_id,
                )

        raw_cov = raw_cp or raw_cp_alt
        if raw_cov:
            try:
                signals["coverage_pct"] = float(raw_cov)
            except (ValueError, TypeError):
                log.debug(
                    "Could not parse coverage_pct=%r for session %s",
                    raw_cov,
                    session_id,
                )

        if raw_specs:
            try:
                specs = json.loads(raw_specs)
                if isinstance(specs, list):
                    signals["specialists_used"] = specs
            except (json.JSONDecodeError, TypeError):
                log.debug(
                    "Could not parse specialists_used=%r for session %s",
                    raw_specs,
                    session_id,
                )

        if raw_domain:
            signals["query_domain"] = raw_domain

        if raw_rev and raw_rev.lower() in ("true", "1"):
            signals["revision_triggered"] = True
        if raw_gvr:
            try:
                signals["gvr_cycles"] = int(raw_gvr)
            except (ValueError, TypeError):
                log.debug(
                    "Could not parse gvr_cycles=%r for session %s",
                    raw_gvr,
                    session_id,
                )
        if raw_revv:
            signals["re_verification_verdict"] = raw_revv
        if raw_revs:
            try:
                signals["re_verification_score"] = float(raw_revs)
            except (ValueError, TypeError):
                log.debug(
                    "Could not parse re_verification_score=%r for session %s",
                    raw_revs,
                    session_id,
                )
        if raw_withheld and raw_withheld.lower() in ("true", "1"):
            signals["report_withheld"] = True

        # ── citations -> source counts ──
        sources_map: dict[str, dict] = {}
        for k in cit_keys:
            raw = await self._backend.get(k)
            if not raw:
                continue
            try:
                cit = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                log.debug(
                    "Could not parse citation key=%s for session %s", k, session_id
                )
                continue
            if isinstance(cit, dict):
                src_name = cit.get("source_type", cit.get("source_name", "unknown"))
                entry = sources_map.setdefault(
                    src_name,
                    {
                        "source_name": src_name,
                        "evidence_count": 0,
                        "avg_grade_score": 0.0,
                    },
                )
                entry["evidence_count"] += 1
                if cit.get("grade_score"):
                    try:
                        n = entry["evidence_count"]
                        entry["avg_grade_score"] = (
                            entry["avg_grade_score"] * (n - 1)
                            + float(cit["grade_score"])
                        ) / n
                    except (ValueError, TypeError):
                        log.debug(
                            "Could not parse grade_score in citation key=%s", k
                        )
        if sources_map:
            signals["sources"] = list(sources_map.values())

        found = [k for k in self._EXPECTED_SIGNALS if k in signals]
        missing = [k for k in self._EXPECTED_SIGNALS if k not in signals]
        if missing:
            log.info(
                "Auto-collection for session %s: found %d/%d signals (missing: %s)",
                session_id,
                len(found),
                len(self._EXPECTED_SIGNALS),
                ", ".join(missing),
            )

        return signals

    # ------------------------------------------------------------------
    # Protocol progress emission
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
                    self._make_key(session_id, "intermediate", "coverage_pct")
                )
                if raw_cp:
                    coverage_pct = float(raw_cp)
                raw_v = await self._backend.get(
                    self._make_key(
                        session_id, "verification", "verification_verdict"
                    )
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
                log_identifier="[ColdQuery:Progress]",
            )
        except Exception as exc:
            log.debug("[ColdQuery:Progress] Could not emit progress: %s", exc)

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
        session_id = getattr(tool_context, "session", None)
        session_id = (
            getattr(session_id, "id", "default") if session_id else "default"
        )

        valid_ops = ("seed_session", "flush_cold", "query_cold", "get_strategy")
        if operation not in valid_ops:
            return {
                "success": False,
                "error": (
                    f"Unknown operation '{operation}'. "
                    f"cold_query supports: {', '.join(valid_ops)}"
                ),
            }

        # ── Deterministic protocol progress emission ──────────────
        if operation in _STEP_MAP:
            step_num, step_name, detail = _STEP_MAP[operation]
            await self._emit_protocol_progress(
                tool_context, step_num, step_name, detail, session_id
            )
            # Reactive step advancement
            try:
                inv = getattr(tool_context, "_invocation_context", None)
                session_obj = getattr(inv, "session", None) if inv else None
                if session_obj and hasattr(session_obj, "state"):
                    current = session_obj.state.get("_protocol_current_step", 0)
                    if step_num > current:
                        session_obj.state["_protocol_current_step"] = step_num
            except Exception:
                pass

        if operation == "flush_cold":
            return await self._op_flush_cold(args, session_id)
        elif operation == "seed_session":
            return await self._op_seed_session(args, session_id)
        elif operation == "query_cold":
            return await self._op_query_cold(args)
        elif operation == "get_strategy":
            return await self._op_get_strategy(args, key)

        return {"success": False, "error": f"Unhandled operation: {operation}"}

    # ------------------------------------------------------------------
    # Cold operation implementations
    # ------------------------------------------------------------------

    async def _op_flush_cold(self, args: dict, session_id: str) -> dict:
        """Auto-collect session signals from hot store and enqueue to cold worker."""
        cold_db_path = getattr(self, "_cold_db_path", "")
        if not cold_db_path:
            return {"success": False, "error": "Cold store not configured"}

        auto_signals = await self._collect_session_signals(session_id)

        try:
            explicit = (
                json.loads(args.get("value", "") or "{}")
                if args.get("value")
                else {}
            )
        except (json.JSONDecodeError, TypeError):
            explicit = {}

        payload = {**auto_signals, **explicit}
        payload["session_id"] = payload.get("session_id", session_id)
        payload["cold_db_path"] = cold_db_path
        payload.setdefault("query_domain", args.get("query_domain", "general"))
        payload.setdefault("query_text", args.get("query", ""))

        try:
            from lifesci_tools.cold_worker import enqueue_session_flush

            enqueued = enqueue_session_flush(payload)
            if not enqueued:
                log.error(
                    "Cold worker queue full -- session %s learning data permanently lost.",
                    payload["session_id"],
                )
                try:
                    overflow_key = self._make_key(
                        session_id, "intermediate", "cold_store_overflow"
                    )
                    await self._backend.set(
                        overflow_key, "true", ex=self._ttl_seconds
                    )
                except Exception:
                    pass
                return {
                    "success": False,
                    "warning": "cold_store_queue_full",
                    "error": (
                        "Cold worker queue is full -- session learning data "
                        "was not persisted. Research results are unaffected."
                    ),
                    "session_id": payload["session_id"],
                }
            return {
                "success": True,
                "enqueued": True,
                "session_id": payload["session_id"],
                "auto_collected_keys": list(auto_signals.keys()),
            }
        except Exception as exc:
            log.exception("Failed to enqueue cold flush")
            return {"success": False, "error": f"flush_cold failed: {exc}"}

    async def _op_seed_session(self, args: dict, session_id: str) -> dict:
        """Load learned strategies from cold store into hot learning namespace."""
        cold_db_path = getattr(self, "_cold_db_path", "")
        if not cold_db_path:
            return {"success": False, "error": "Cold store not configured"}
        query_text = args.get("query", "")
        try:
            from lifesci_tools.strategy_seeder import build_seed_payload

            seed_data = await asyncio.to_thread(
                build_seed_payload, query_text, cold_db_path
            )
            if seed_data.get("seeded"):
                seed_key = self._make_key(session_id, "learning", "seed")
                await self._backend.set(
                    seed_key, json.dumps(seed_data), ex=self._ttl_seconds
                )
            return {"success": True, **seed_data}
        except Exception as exc:
            log.exception("Failed to seed session")
            return {"success": False, "error": f"seed_session failed: {exc}"}

    async def _op_query_cold(self, args: dict) -> dict:
        """Look up historical intelligence from the cold store."""
        cold_db_path = getattr(self, "_cold_db_path", "")
        if not cold_db_path:
            return {"success": False, "error": "Cold store not configured"}
        query_text = args.get("query", "")

        def _sync_query():
            from lifesci_tools import cold_store

            conn = cold_store.get_connection(cold_db_path)
            try:
                normalized = (
                    cold_store.normalize_query(query_text) if query_text else ""
                )
                intel = (
                    cold_store.get_query_intelligence(conn, normalized)
                    if normalized
                    else None
                )
                source_rankings = cold_store.get_source_rankings(conn)
                session_count = cold_store.get_session_count(conn)
            finally:
                conn.close()
            return {
                "success": True,
                "query_intelligence": intel,
                "source_rankings": source_rankings,
                "total_sessions": session_count,
            }

        try:
            return await asyncio.to_thread(_sync_query)
        except Exception as exc:
            log.exception("Failed to query cold store")
            return {"success": False, "error": f"query_cold failed: {exc}"}

    async def _op_get_strategy(self, args: dict, key: str) -> dict:
        """Fetch a specific learned strategy by type and key."""
        cold_db_path = getattr(self, "_cold_db_path", "")
        if not cold_db_path:
            return {"success": False, "error": "Cold store not configured"}
        if not key:
            return {
                "success": False,
                "error": "Key is required for get_strategy (domain or '_global')",
            }
        strategy_type = args.get("strategy_type", "")
        if not strategy_type:
            return {
                "success": False,
                "error": "strategy_type is required (routing, source_ranking, agent_pairs)",
            }

        def _sync_get():
            from lifesci_tools import cold_store

            conn = cold_store.get_connection(cold_db_path)
            try:
                return cold_store.get_strategy_by_type(conn, strategy_type, key)
            finally:
                conn.close()

        try:
            result = await asyncio.to_thread(_sync_get)
            if not result:
                return {"success": True, "found": False, "strategy": None}
            return {"success": True, "found": True, **result}
        except Exception as exc:
            log.exception("Failed to get strategy")
            return {"success": False, "error": f"get_strategy failed: {exc}"}
