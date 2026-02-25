"""Async batched LLM-as-judge grader worker.

Mirrors the cold_worker daemon-thread pattern: a background thread
processes queued grading jobs in batches, calling LiteLLM to score
specialist responses, then backfilling ``prompt_scores.llm_judge_score``.

The worker does NOT block the cold worker — it runs independently.
"""

import asyncio
import atexit
import contextlib
import logging
import os
import queue
import re
import threading
from typing import Any

from lifesci_common.constants import (
    LLM_JUDGE_BATCH_SIZE,
    LLM_JUDGE_MODEL,
    LLM_JUDGE_TEMPERATURE,
    LLM_JUDGE_TIMEOUT_SECONDS,
)
from lifesci_tools import cold_store
from lifesci_tools.prompt_graders import (
    LLM_JUDGE_SYSTEM,
    LLM_JUDGE_USER_TEMPLATE,
    calculate_composite,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_STOP = object()
_queue: queue.Queue = queue.Queue()
_worker_thread: threading.Thread | None = None
_started = False
_lock = threading.Lock()

# Allow disabling via env var
_ENABLED = os.environ.get("LLM_GRADER_ENABLED", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enqueue_grading_job(job: dict[str, Any]) -> bool:
    """Fire-and-forget enqueue of an LLM grading job.

    Expected *job* keys:
      - db_path: str
      - agent_name: str
      - prompt_version: int
      - session_id: str
      - response: str
      - evidence: str
      - question: str

    Returns True if enqueued, False if disabled or queue full.
    """
    if not _ENABLED:
        return False
    try:
        _ensure_started(job.get("db_path", ""))
        _queue.put_nowait(job)
        return True
    except queue.Full:
        log.warning(
            "LLM grader queue full, dropping job for session %s", job.get("session_id")
        )
        return False


def stop(timeout: float = 5.0) -> None:
    """Signal the worker to drain its queue and exit."""
    global _started
    with _lock:
        if not _started:
            return
        _started = False
    with contextlib.suppress(queue.Full):
        _queue.put_nowait(_STOP)
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=timeout)


atexit.register(stop)


# ---------------------------------------------------------------------------
# Internal startup
# ---------------------------------------------------------------------------


def _ensure_started(db_path: str) -> None:
    global _worker_thread, _started
    with _lock:
        if _started:
            return
        _worker_thread = threading.Thread(
            target=_worker_loop,
            args=(db_path,),
            daemon=True,
            name="llm-grader-worker",
        )
        _started = True
        _worker_thread.start()
        log.info("LLM grader worker started (batch_size=%d)", LLM_JUDGE_BATCH_SIZE)


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


def _worker_loop(db_path: str) -> None:
    """Background thread that processes LLM grading jobs in batches."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    batch: list[dict[str, Any]] = []
    # Track how many items need task_done() calls
    pending_done = 0

    def _flush_batch() -> None:
        nonlocal pending_done
        if not batch:
            return
        loop.run_until_complete(_process_batch(batch, db_path))
        for _ in range(pending_done):
            try:
                _queue.task_done()
            except ValueError:
                break  # Queue already drained (e.g. during shutdown)
        pending_done = 0
        batch.clear()

    while True:
        try:
            item = _queue.get(timeout=10.0)
        except queue.Empty:
            _flush_batch()
            continue

        if item is _STOP:
            _flush_batch()
            _queue.task_done()
            break

        batch.append(item)
        pending_done += 1

        if len(batch) >= LLM_JUDGE_BATCH_SIZE:
            _flush_batch()

    loop.close()


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------


async def _process_batch(batch: list[dict[str, Any]], default_db_path: str) -> None:
    """Score a batch of jobs concurrently and write results to cold store."""
    tasks = [_grade_one(job) for job in batch]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for job, result in zip(batch, results, strict=True):
        if isinstance(result, Exception):
            log.warning(
                "LLM grader failed for session %s agent %s: %s",
                job.get("session_id"),
                job.get("agent_name"),
                result,
            )
            continue

        if result is None:
            continue

        try:
            effective_path = job.get("db_path") or default_db_path
            conn = cold_store.get_connection(effective_path)

            # Fetch existing scores to recalculate composite
            rows = conn.execute(
                """SELECT entity_score, fidelity_score, citation_score, quality_score
                   FROM prompt_scores
                   WHERE agent_name = ? AND session_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (job["agent_name"], job["session_id"]),
            ).fetchone()

            if rows:
                scores = {
                    "entity": rows["entity_score"],
                    "fidelity": rows["fidelity_score"],
                    "citation": rows["citation_score"],
                    "quality": rows["quality_score"],
                    "llm_judge": result,
                }
                new_composite = calculate_composite(scores)
                cold_store.update_llm_judge_score(
                    conn,
                    agent_name=job["agent_name"],
                    session_id=job["session_id"],
                    llm_judge_score=result,
                    new_composite=round(new_composite, 4),
                )
            conn.close()
        except Exception:
            log.exception(
                "Failed to write LLM judge score for session %s",
                job.get("session_id"),
            )

    log.info("LLM grader batch complete: %d jobs", len(batch))


async def _grade_one(job: dict[str, Any]) -> float | None:
    """Call LiteLLM to get an LLM-as-judge score for one specialist response."""
    try:
        import litellm

        user_msg = LLM_JUDGE_USER_TEMPLATE.format(
            question=job.get("question", ""),
            evidence=job.get("evidence", "")[:4000],  # Truncate long evidence
            response=job.get("response", "")[:4000],
        )

        response = await asyncio.wait_for(
            litellm.acompletion(
                model=LLM_JUDGE_MODEL,
                messages=[
                    {"role": "system", "content": LLM_JUDGE_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                temperature=LLM_JUDGE_TEMPERATURE,
                max_tokens=10,
            ),
            timeout=LLM_JUDGE_TIMEOUT_SECONDS,
        )

        content = response.choices[0].message.content.strip()
        # Parse the score — expect a single float
        match = re.search(r"(\d+\.?\d*)", content)
        if match:
            score = float(match.group(1))
            return max(0.0, min(1.0, score))

        log.warning("LLM judge returned unparseable response: %s", content[:100])
        return None

    except asyncio.TimeoutError:
        log.warning("LLM judge timed out for session %s", job.get("session_id"))
        return None
    except ImportError:
        log.warning("litellm not available, skipping LLM judge grading")
        return None
    except Exception:
        log.exception("LLM judge error for session %s", job.get("session_id"))
        return None
