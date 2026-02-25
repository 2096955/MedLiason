"""Cold Worker — background thread that persists session data to SQLite.

Consumes payloads from a queue.Queue and writes them to the cold store.
Refreshes learned strategies every REFRESH_INTERVAL sessions.

Lifecycle:
    - ``enqueue_session_flush()`` lazily starts a single daemon thread.
    - ``stop()`` sends a sentinel to drain the queue and exit cleanly.
    - On process exit the daemon thread is also killed by the runtime.
"""

import atexit
import logging
import os
import queue
import threading
import time
from typing import Any

from lifesci_tools import cold_store, llm_grader_worker, prompt_graders

log = logging.getLogger(__name__)

# Refresh learned strategies every N sessions
REFRESH_INTERVAL = 10

# Retention policy (configurable via environment)
PRUNE_MAX_AGE_DAYS = int(os.environ.get("COLD_STORE_MAX_AGE_DAYS", "90"))
PRUNE_MAX_SESSIONS = int(os.environ.get("COLD_STORE_MAX_SESSIONS", "10000"))

# Retry settings
MAX_RETRIES = 3
BASE_BACKOFF = 0.5  # seconds

# Sentinel object — put on the queue to signal the worker to stop.
_STOP = object()

_queue: queue.Queue = queue.Queue()
_worker_thread: threading.Thread | None = None
_started = False
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enqueue_session_flush(payload: dict[str, Any]) -> bool:
    """Fire-and-forget enqueue of a session flush payload.

    Returns True if enqueued, False if the queue is full.
    """
    _ensure_started(payload.get("cold_db_path", ""))
    try:
        _queue.put_nowait(payload)
        log.debug("Enqueued session flush: %s", payload.get("session_id", "?"))
        return True
    except queue.Full:
        log.warning("Cold worker queue is full, dropping payload")
        return False


def stop(timeout: float = 5.0) -> None:
    """Signal the worker to drain its queue and exit.

    Blocks up to *timeout* seconds for the thread to finish.
    """
    global _started, _worker_thread
    with _lock:
        if not _started:
            return
        _queue.put(_STOP)
        _started = False
    if _worker_thread is not None:
        _worker_thread.join(timeout=timeout)
        _worker_thread = None


def reset_for_testing() -> None:
    """Stop worker and reset all module state.  For use in test fixtures only."""
    global _queue, _started, _worker_thread
    stop(timeout=2.0)
    _queue = queue.Queue()
    _started = False
    _worker_thread = None


# Register atexit so pending items are flushed on normal interpreter shutdown.
atexit.register(stop)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _ensure_started(db_path: str) -> None:
    """Start the daemon worker thread if not already running."""
    global _worker_thread, _started
    with _lock:
        if _started:
            return
        _worker_thread = threading.Thread(
            target=_worker_loop,
            args=(db_path,),
            daemon=True,
            name="cold-worker",
        )
        _worker_thread.start()
        _started = True
        log.info("Cold worker thread started")


def _worker_loop(db_path: str) -> None:
    """Main consumer loop — runs in a daemon thread."""
    sessions_since_refresh = 0

    while True:
        try:
            item = _queue.get(timeout=5.0)
        except queue.Empty:
            continue

        if item is _STOP:
            _queue.task_done()
            log.info("Cold worker received stop sentinel, exiting")
            break

        # Allow overriding db_path per-payload (useful in tests)
        effective_path = item.get("cold_db_path", db_path)
        if not effective_path:
            log.warning("No cold_db_path in payload, skipping")
            _queue.task_done()
            continue

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                _persist_one(effective_path, item)
                success = True
                break
            except Exception:
                wait = BASE_BACKOFF * (2 ** (attempt - 1))
                log.exception(
                    "Cold write attempt %d/%d failed, retrying in %.1fs",
                    attempt,
                    MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)

        if not success:
            log.error(
                "Cold write failed after %d attempts for session %s",
                MAX_RETRIES,
                item.get("session_id", "?"),
            )

        _queue.task_done()

        if success:
            sessions_since_refresh += 1
            # TODO(perf): Pruning runs every REFRESH_INTERVAL (10) sessions. Under high load,
            # this triggers full table scans + VACUUM frequently. Consider time-based guard
            # (e.g., skip if pruned < 1 hour ago) or increase REFRESH_INTERVAL.
            # NOTE: SQLite VACUUM cannot run inside a transaction. The conn from
            # get_connection() uses autocommit=True which avoids this issue, but verify
            # under production connection settings if changed.
            if sessions_since_refresh >= REFRESH_INTERVAL:
                try:
                    conn = cold_store.get_connection(effective_path)
                    cold_store.refresh_learned_strategies(conn)

                    # Prompt evolution check
                    try:
                        from lifesci_tools.prompt_evolver import PromptEvolver

                        evolver = PromptEvolver(effective_path)
                        actions = evolver.check_and_evolve(conn)
                        if actions:
                            log.info("Prompt evolution actions: %s", actions)
                    except Exception:
                        log.exception("Prompt evolution check failed")

                    cold_store.prune_cold_store(
                        conn,
                        max_age_days=PRUNE_MAX_AGE_DAYS,
                        max_sessions=PRUNE_MAX_SESSIONS,
                    )
                    conn.close()
                    sessions_since_refresh = 0
                    log.info(
                        "Refreshed learned strategies after %d sessions",
                        REFRESH_INTERVAL,
                    )
                except Exception:
                    log.exception("Failed to refresh learned strategies")


def _persist_one(db_path: str, item: dict[str, Any]) -> None:
    """Write all raw tables for one session in a single transaction."""
    conn = cold_store.get_connection(db_path)
    try:
        session_id = item["session_id"]
        query_domain = item.get("query_domain", "general")
        query_text = item.get("query_text", "")
        coverage_pct = item.get("coverage_pct", 0.0)
        verification_verdict = item.get("verification_verdict", "UNKNOWN")
        verification_score = item.get("verification_score", 0.0)
        revision_triggered = item.get("revision_triggered", False)
        specialists_used = item.get("specialists_used", [])

        # 1. Session outcome
        cold_store.write_session_outcome(
            conn,
            session_id=session_id,
            query_domain=query_domain,
            query_text=query_text,
            coverage_pct=coverage_pct,
            verification_verdict=verification_verdict,
            verification_score=verification_score,
            revision_triggered=revision_triggered,
            specialists_used=specialists_used,
        )

        # 2. Source reliability
        for src in item.get("sources", []):
            cold_store.write_source_reliability(
                conn,
                session_id=session_id,
                source_name=src.get("source_name", ""),
                evidence_count=src.get("evidence_count", 0),
                avg_grade_score=src.get("avg_grade_score", 0.0),
                citations_valid=src.get("citations_valid", 0),
                citations_invalid=src.get("citations_invalid", 0),
                passed_verification=src.get("passed_verification", False),
            )

        # 3. Routing patterns
        for agent in specialists_used:
            cold_store.write_routing_pattern(
                conn,
                session_id=session_id,
                query_domain=query_domain,
                agent_name=agent,
                coverage_contribution=item.get("agent_coverage", {}).get(agent, 0.0),
                session_verification_score=verification_score,
            )

        # 4. Query pattern
        normalized = cold_store.normalize_query(query_text) if query_text else ""
        if normalized:
            cold_store.upsert_query_pattern(
                conn,
                normalized_template=normalized,
                domain=query_domain,
                coverage_pct=coverage_pct,
                verification_score=verification_score,
            )

        # 5. Agent co-activation pairs
        passed = verification_verdict == "PASS"
        for i, a in enumerate(specialists_used):
            for b in specialists_used[i + 1 :]:
                cold_store.upsert_agent_coactivation(
                    conn,
                    agent_a=a,
                    agent_b=b,
                    passed=passed,
                    combined_coverage=coverage_pct,
                )

        # 6. Prompt scores for specialist evolution
        for agent in specialists_used:
            active_prompt = cold_store.get_active_prompt(conn, agent)
            if not active_prompt:
                continue

            scores = prompt_graders.score_specialist(
                agent_name=agent,
                session_signals=item,
            )
            cold_store.write_prompt_score(
                conn,
                agent_name=agent,
                prompt_version=active_prompt["version"],
                session_id=session_id,
                scores=scores,
            )

            # Enqueue async LLM judge grading
            response_text = (item.get("specialist_responses") or {}).get(agent, "")
            if response_text:
                llm_grader_worker.enqueue_grading_job(
                    {
                        "db_path": db_path,
                        "agent_name": agent,
                        "prompt_version": active_prompt["version"],
                        "session_id": session_id,
                        "response": response_text,
                        "evidence": " ".join(
                            s.get("text", "")
                            or s.get("abstract", "")
                            or s.get("source_name", "")
                            for s in item.get("sources", [])
                            if isinstance(s, dict)
                        )[:4000],
                        "question": query_text,
                    }
                )

        conn.commit()
        log.info("Persisted session %s to cold store", session_id)
    finally:
        conn.close()
