"""Central Memory Plane — shared state for orchestrator, verifier, reviser.

Provides session-scoped key-value storage with namespace separation.
Uses Redis in production, falls back to an in-memory dict for dev/test.

Cold-store operations (flush_cold, seed_session, query_cold, get_strategy)
run synchronous SQLite I/O via ``asyncio.to_thread`` to avoid blocking
the agent's event loop.
"""

import asyncio
import json
import logging
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

log = logging.getLogger(__name__)

NAMESPACES = ("citations", "evidence", "intermediate", "learning", "verification")

# ── Deterministic protocol step mapping ──────────────────────────────
# Maps memory_plane operations + key patterns to protocol steps.
# This emits ResearchProtocolProgressData without depending on LLM output.
_STEP_MAP = {
    "seed_session": (0, "SEED", "Loading learned strategies from past sessions"),
    "flush_cold": (6, "PERSIST", "Saving session signals to cold store"),
}
_KEY_STEP_MAP = {
    # store/append operations with these keys signal specific steps
    ("intermediate", "specialists_used"): (3, "COLLECT", "Gathering evidence from specialists"),
    ("intermediate", "query_domain"): (3, "COLLECT", "Gathering evidence from specialists"),
    ("intermediate", "coverage_pct"): (3, "COLLECT", "Checking research completeness"),
    ("verification", "verification_verdict"): (5, "VERIFY", "Verifying claims against sources"),
    ("verification", "verification_score"): (5, "VERIFY", "Verifying claims against sources"),
    ("intermediate", "revision_triggered"): (5, "VERIFY", "Revising flagged claims"),
    ("verification", "re_verification_verdict"): (5, "VERIFY", "Re-verifying revised report"),
}


class DictBackend:
    """In-memory fallback when Redis is not available."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self._store.pop(k, None)

    async def keys(self, pattern: str) -> list[str]:
        import fnmatch

        return [k for k in self._store if fnmatch.fnmatch(k, pattern)]

    async def exists(self, key: str) -> bool:
        return key in self._store


class RedisBackend:
    """Redis-backed storage for production use.

    Uses SCAN instead of KEYS for pattern matching to avoid blocking
    the Redis instance on large keyspaces.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def get(self, key: str) -> str | None:
        val = await self._redis.get(key)
        if isinstance(val, bytes):
            return val.decode("utf-8")
        return val

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        await self._redis.set(key, value, ex=ex)

    async def delete(self, *keys: str) -> None:
        if keys:
            await self._redis.delete(*keys)

    async def keys(self, pattern: str) -> list[str]:
        """Iterate keys matching *pattern* using SCAN (non-blocking)."""
        result = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            result.append(key.decode("utf-8") if isinstance(key, bytes) else key)
        return result

    async def exists(self, key: str) -> bool:
        return bool(await self._redis.exists(key))


class MemoryPlaneTool(DynamicTool):
    """Session-scoped key-value store with namespace separation."""

    _backend = None

    def __init__(self, tool_config=None):
        super().__init__(tool_config=tool_config)
        self._read_only = bool(self.tool_config.get("read_only", False))
        self._ttl_seconds = int(self.tool_config.get("ttl_seconds", 3600))

    @property
    def tool_name(self) -> str:
        return "memory_plane"

    @property
    def tool_description(self) -> str:
        if getattr(self, "_read_only", False):
            return (
                "Read-only memory plane for retrieving shared state accumulated by "
                "specialist agents. Supports operations: retrieve, list_keys, "
                "query_cold, get_strategy. "
                "Keys are scoped by session ID and namespace (citations, evidence, "
                "intermediate, learning, verification)."
            )
        return (
            "Central memory plane for storing and retrieving shared state across "
            "agents. Supports operations: store, retrieve, list_keys, clear_session, "
            "append, flush_cold, seed_session, query_cold, get_strategy. "
            "flush_cold auto-collects session signals from the hot store — just "
            "pass query_domain and query_text. "
            "Keys are scoped by session ID and namespace (citations, evidence, "
            "intermediate, learning, verification)."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "operation": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "Operation: store, retrieve, list_keys, clear_session, append, "
                        "flush_cold, seed_session, query_cold, get_strategy"
                    ),
                ),
                "key": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Key name (required for store, retrieve, append, get_strategy)",
                    nullable=True,
                ),
                "value": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "Value to store (JSON string). Required for store/append. "
                        "For flush_cold: optional JSON with extra signals "
                        "(coverage_pct, verification_verdict, verification_score, "
                        "revision_triggered, specialists_used, sources)."
                    ),
                    nullable=True,
                ),
                "namespace": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Namespace: citations, evidence, intermediate, learning, verification. Default: intermediate",
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
        """Connect to Redis or fall back to dict backend."""
        if tool_config:
            self.tool_config = tool_config
        self._read_only = bool(self.tool_config.get("read_only", False))
        self._cold_db_path = self.tool_config.get("cold_db_path", "") if self.tool_config else ""
        redis_url = self.tool_config.get("redis_url") if self.tool_config else None
        if redis_url:
            try:
                import redis.asyncio as aioredis
                import os
                from urllib.parse import urlparse

                # P0 Security: Warn when Redis has no authentication outside dev
                try:
                    parsed = urlparse(redis_url)
                    env_mode = os.environ.get("MEDEXPERT_ENV", "").lower()
                    if not parsed.password and env_mode != "development":
                        log.warning(
                            "SECURITY: Redis connection has no password configured. "
                            "Set REDIS_PASSWORD or use a redis:// URL with credentials "
                            "for production deployments handling PHI/medical data."
                        )
                except Exception:
                    pass  # Don't fail on URL parsing issues

                client = aioredis.from_url(redis_url, decode_responses=False)
                await client.ping()
                MemoryPlaneTool._backend = RedisBackend(client)
                log.info("Memory plane connected to Redis at %s", redis_url)
                return
            except Exception as exc:
                log.warning("Redis connection failed (%s), using dict fallback", exc)

        MemoryPlaneTool._backend = DictBackend()
        log.info("Memory plane using in-memory dict backend")

    def _make_key(self, session_id: str, namespace: str, key: str) -> str:
        return f"medexpert:{session_id}:{namespace}:{key}"

    def _session_pattern(self, session_id: str, namespace: str | None = None) -> str:
        if namespace:
            return f"medexpert:{session_id}:{namespace}:*"
        return f"medexpert:{session_id}:*"

    # ------------------------------------------------------------------
    # Deterministic protocol progress emission
    # ------------------------------------------------------------------

    async def _emit_protocol_progress(
        self, tool_context: ToolContext, step: int, step_name: str, detail: str,
        session_id: str,
    ) -> None:
        """Emit ResearchProtocolProgressData via SSE. Deterministic — tied to tool calls."""
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

            from solace_agent_mesh.common.data_parts import ResearchProtocolProgressData

            # Read coverage + verdict from memory if available
            coverage_pct = None
            verdict = None
            try:
                raw_cp = await self._backend.get(
                    self._make_key(session_id, "intermediate", "coverage_pct")
                )
                if raw_cp:
                    coverage_pct = float(raw_cp)
                raw_v = await self._backend.get(
                    self._make_key(session_id, "verification", "verification_verdict")
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
                log_identifier="[MemoryPlane:Progress]",
            )
        except Exception as exc:
            log.debug("[MemoryPlane:Progress] Could not emit progress: %s", exc)

    # ------------------------------------------------------------------
    # Hot-store auto-collection helpers for flush_cold
    # ------------------------------------------------------------------

    # Expected signal keys for completeness diagnostics
    _EXPECTED_SIGNALS = (
        "verification_verdict", "verification_score", "coverage_pct",
        "specialists_used", "query_domain", "revision_triggered",
    )

    async def _collect_session_signals(self, session_id: str) -> dict:
        """Read structured data from all hot-store namespaces for one session.

        Reads are parallelised across independent namespaces via
        ``asyncio.gather``.  Returns a dict suitable for passing to the
        cold worker.
        """
        signals: dict = {}

        # Fire independent namespace scans in parallel
        ev_keys_coro = self._backend.keys(
            self._session_pattern(session_id, "evidence")
        )
        cit_keys_coro = self._backend.keys(
            self._session_pattern(session_id, "citations")
        )
        # Point reads for known keys (verification + intermediate)
        vv_coro = self._backend.get(self._make_key(session_id, "verification", "verification_verdict"))
        vv_alt_coro = self._backend.get(self._make_key(session_id, "verification", "verdict"))
        vs_coro = self._backend.get(self._make_key(session_id, "verification", "verification_score"))
        vs_alt_coro = self._backend.get(self._make_key(session_id, "verification", "score"))
        cp_coro = self._backend.get(self._make_key(session_id, "intermediate", "coverage_pct"))
        cp_alt_coro = self._backend.get(self._make_key(session_id, "intermediate", "completeness_coverage"))
        sp_coro = self._backend.get(self._make_key(session_id, "intermediate", "specialists_used"))
        dom_coro = self._backend.get(self._make_key(session_id, "intermediate", "query_domain"))
        # STEP 10 GVR signals
        rev_coro = self._backend.get(self._make_key(session_id, "intermediate", "revision_triggered"))
        gvr_coro = self._backend.get(self._make_key(session_id, "intermediate", "gvr_cycles"))
        revv_coro = self._backend.get(self._make_key(session_id, "verification", "re_verification_verdict"))
        revs_coro = self._backend.get(self._make_key(session_id, "verification", "re_verification_score"))
        withheld_coro = self._backend.get(self._make_key(session_id, "verification", "report_withheld"))

        (
            ev_keys, cit_keys,
            raw_vv, raw_vv_alt, raw_vs, raw_vs_alt,
            raw_cp, raw_cp_alt, raw_specs, raw_domain,
            raw_rev, raw_gvr, raw_revv, raw_revs, raw_withheld,
        ) = await asyncio.gather(
            ev_keys_coro, cit_keys_coro,
            vv_coro, vv_alt_coro, vs_coro, vs_alt_coro,
            cp_coro, cp_alt_coro, sp_coro, dom_coro,
            rev_coro, gvr_coro, revv_coro, revs_coro, withheld_coro,
        )

        # ── evidence count ──
        signals["evidence_count"] = len(ev_keys)

        # ── verification verdict ──
        raw_verdict = raw_vv or raw_vv_alt
        if raw_verdict:
            signals["verification_verdict"] = raw_verdict

        # ── verification score ──
        raw_score = raw_vs or raw_vs_alt
        if raw_score:
            try:
                signals["verification_score"] = float(raw_score)
            except (ValueError, TypeError):
                log.debug("Could not parse verification_score=%r for session %s", raw_score, session_id)

        # ── coverage_pct ──
        raw_cov = raw_cp or raw_cp_alt
        if raw_cov:
            try:
                signals["coverage_pct"] = float(raw_cov)
            except (ValueError, TypeError):
                log.debug("Could not parse coverage_pct=%r for session %s", raw_cov, session_id)

        # ── specialists_used ──
        if raw_specs:
            try:
                specs = json.loads(raw_specs)
                if isinstance(specs, list):
                    signals["specialists_used"] = specs
            except (json.JSONDecodeError, TypeError):
                log.debug("Could not parse specialists_used=%r for session %s", raw_specs, session_id)

        # ── query_domain ──
        if raw_domain:
            signals["query_domain"] = raw_domain

        # ── STEP 10 GVR signals ──
        if raw_rev and raw_rev.lower() in ("true", "1"):
            signals["revision_triggered"] = True
        if raw_gvr:
            try:
                signals["gvr_cycles"] = int(raw_gvr)
            except (ValueError, TypeError):
                log.debug("Could not parse gvr_cycles=%r for session %s", raw_gvr, session_id)
        if raw_revv:
            signals["re_verification_verdict"] = raw_revv
        if raw_revs:
            try:
                signals["re_verification_score"] = float(raw_revs)
            except (ValueError, TypeError):
                log.debug("Could not parse re_verification_score=%r for session %s", raw_revs, session_id)
        if raw_withheld and raw_withheld.lower() in ("true", "1"):
            signals["report_withheld"] = True

        # ── citations → source counts (evidence_count + avg_grade_score only) ──
        # Note: citations_valid/invalid and passed_verification are not
        # available from citation data alone — they require verification
        # results.  Provide explicit overrides via the value param if needed.
        sources_map: dict[str, dict] = {}
        for k in cit_keys:
            raw = await self._backend.get(k)
            if not raw:
                continue
            try:
                cit = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                log.debug("Could not parse citation key=%s for session %s", k, session_id)
                continue
            if isinstance(cit, dict):
                src_name = cit.get("source_type", cit.get("source_name", "unknown"))
                entry = sources_map.setdefault(src_name, {
                    "source_name": src_name,
                    "evidence_count": 0,
                    "avg_grade_score": 0.0,
                })
                entry["evidence_count"] += 1
                if cit.get("grade_score"):
                    try:
                        n = entry["evidence_count"]
                        entry["avg_grade_score"] = (
                            entry["avg_grade_score"] * (n - 1) + float(cit["grade_score"])
                        ) / n
                    except (ValueError, TypeError):
                        log.debug("Could not parse grade_score in citation key=%s", k)
        if sources_map:
            signals["sources"] = list(sources_map.values())

        # Log completeness diagnostic
        found = [k for k in self._EXPECTED_SIGNALS if k in signals]
        missing = [k for k in self._EXPECTED_SIGNALS if k not in signals]
        if missing:
            log.info(
                "Auto-collection for session %s: found %d/%d signals (missing: %s)",
                session_id, len(found), len(self._EXPECTED_SIGNALS), ", ".join(missing),
            )

        return signals

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        if MemoryPlaneTool._backend is None:
            MemoryPlaneTool._backend = DictBackend()

        operation = args.get("operation", "").lower()
        key = args.get("key", "")
        value = args.get("value", "")
        namespace = args.get("namespace", "intermediate")
        session_id = getattr(tool_context, "session", None)
        session_id = getattr(session_id, "id", "default") if session_id else "default"

        # Cold operations bypass namespace validation
        cold_ops = ("flush_cold", "seed_session", "query_cold", "get_strategy")
        if operation not in cold_ops and namespace not in NAMESPACES:
            return {
                "success": False,
                "error": f"Invalid namespace '{namespace}'. Must be one of: {NAMESPACES}",
            }

        write_ops = ("store", "append", "clear_session", "flush_cold")
        if getattr(self, "_read_only", False) and operation in write_ops:
            return {
                "success": False,
                "error": f"Memory plane is read-only for this agent. Cannot perform '{operation}'.",
            }

        # ── Deterministic protocol progress emission ──────────────
        # Emit SSE progress signals based on which operation/key is being called.
        # This is tied to actual tool invocations, not LLM output parsing.
        if operation in _STEP_MAP:
            step_num, step_name, detail = _STEP_MAP[operation]
            await self._emit_protocol_progress(
                tool_context, step_num, step_name, detail, session_id
            )
        elif operation in ("store", "append") and (namespace, key) in _KEY_STEP_MAP:
            step_num, step_name, detail = _KEY_STEP_MAP[(namespace, key)]
            await self._emit_protocol_progress(
                tool_context, step_num, step_name, detail, session_id
            )

        if operation == "store":
            if not key:
                return {"success": False, "error": "Key is required for store"}
            full_key = self._make_key(session_id, namespace, key)
            # Idempotency: skip write if key already holds the same value
            existing = await self._backend.get(full_key)
            if existing == value:
                return {"success": True, "key": key, "namespace": namespace, "idempotent": True}
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
            # Strip the prefix to return user-friendly key names
            prefix = f"medexpert:{session_id}:{namespace}:"
            keys = [k[len(prefix) :] for k in raw_keys if k.startswith(prefix)]
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
            full_key = self._make_key(session_id, namespace, key)
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
            await self._backend.set(full_key, json.dumps(existing_list), ex=self._ttl_seconds)
            return {
                "success": True,
                "key": key,
                "namespace": namespace,
                "length": len(existing_list),
            }

        elif operation == "flush_cold":
            return await self._op_flush_cold(args, session_id)

        elif operation == "seed_session":
            return await self._op_seed_session(args, session_id)

        elif operation == "query_cold":
            return await self._op_query_cold(args)

        elif operation == "get_strategy":
            return await self._op_get_strategy(args, key)

        else:
            return {
                "success": False,
                "error": (
                    f"Unknown operation '{operation}'. Use: store, retrieve, list_keys, "
                    "clear_session, append, flush_cold, seed_session, query_cold, "
                    "get_strategy"
                ),
            }

    # ------------------------------------------------------------------
    # Cold operation implementations
    # ------------------------------------------------------------------

    async def _op_flush_cold(self, args: dict, session_id: str) -> dict:
        """Auto-collect session signals from hot store and enqueue to cold worker."""
        cold_db_path = getattr(self, "_cold_db_path", "")
        if not cold_db_path:
            return {"success": False, "error": "Cold store not configured"}

        # 1. Auto-collect from hot store
        auto_signals = await self._collect_session_signals(session_id)

        # 2. Merge with any explicit overrides from the LLM
        try:
            explicit = json.loads(args.get("value", "") or "{}") if args.get("value") else {}
        except (json.JSONDecodeError, TypeError):
            explicit = {}

        # Explicit values take precedence over auto-collected
        payload = {**auto_signals, **explicit}

        # Ensure required fields
        payload["session_id"] = payload.get("session_id", session_id)
        payload["cold_db_path"] = cold_db_path
        payload.setdefault("query_domain", args.get("query_domain", "general"))
        payload.setdefault("query_text", args.get("query", ""))

        try:
            from lifesci_tools.cold_worker import enqueue_session_flush

            enqueued = enqueue_session_flush(payload)
            if not enqueued:
                log.error(
                    "Cold worker queue full — session %s learning data permanently lost. "
                    "Consider increasing queue size or investigating cold store throughput.",
                    payload["session_id"],
                )
                # Store overflow indicator in hot store so orchestrator can report it
                try:
                    overflow_key = self._make_key(session_id, "intermediate", "cold_store_overflow")
                    await self._backend.set(overflow_key, "true", ex=self._ttl_seconds)
                except Exception:
                    pass
                return {
                    "success": False,
                    "warning": "cold_store_queue_full",
                    "error": "Cold worker queue is full — session learning data was not persisted. Research results are unaffected.",
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
                await self._backend.set(seed_key, json.dumps(seed_data), ex=self._ttl_seconds)
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
                normalized = cold_store.normalize_query(query_text) if query_text else ""
                intel = cold_store.get_query_intelligence(conn, normalized) if normalized else None
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
            return {"success": False, "error": "Key is required for get_strategy (domain or '_global')"}
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
