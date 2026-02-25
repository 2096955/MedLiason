"""Tests for the async batched LLM-as-judge grader worker (llm_grader_worker.py).

Covers: enqueue_grading_job, stop, _grade_one (score parsing, timeout, unparseable).
"""

import asyncio
import queue

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from lifesci_tools import llm_grader_worker
from lifesci_tools import cold_store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_worker():
    """Reset the LLM grader worker module state between tests."""
    llm_grader_worker.stop(timeout=1.0)
    llm_grader_worker._queue = queue.Queue()
    llm_grader_worker._started = False
    llm_grader_worker._worker_thread = None
    yield
    llm_grader_worker.stop(timeout=1.0)


@pytest.fixture(autouse=True)
def _clear_ddl_cache():
    cold_store.clear_init_cache()
    yield
    cold_store.clear_init_cache()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnqueue:
    """Tests for enqueue_grading_job."""

    def test_enqueue_disabled(self):
        """When _ENABLED is False, enqueue returns False immediately."""
        with patch.object(llm_grader_worker, "_ENABLED", False):
            result = llm_grader_worker.enqueue_grading_job(
                {
                    "db_path": "/tmp/test.db",
                    "agent_name": "DrugSpecialist",
                    "prompt_version": 0,
                    "session_id": "sess-001",
                    "response": "test response",
                    "evidence": "test evidence",
                    "question": "What is aspirin?",
                }
            )
        assert result is False

    def test_enqueue_and_stop(self, tmp_path):
        """Enqueue a job, then stop the worker without crashing."""
        db_path = str(tmp_path / "test.db")
        job = {
            "db_path": db_path,
            "agent_name": "DrugSpecialist",
            "prompt_version": 0,
            "session_id": "sess-002",
            "response": "Metformin is a biguanide.",
            "evidence": "Metformin reduces HbA1c.",
            "question": "What is metformin?",
        }
        with patch.object(llm_grader_worker, "_ENABLED", True):
            result = llm_grader_worker.enqueue_grading_job(job)
        assert result is True

        # Stop should not raise or hang
        llm_grader_worker.stop(timeout=2.0)


class TestGradeOne:
    """Tests for the _grade_one async function."""

    @pytest.mark.asyncio
    async def test_grade_one_parses_score(self):
        """Mock litellm.acompletion to return '0.85', verify _grade_one returns 0.85."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "0.85"

        mock_acompletion = AsyncMock(return_value=mock_response)

        job = {
            "session_id": "sess-parse",
            "question": "What is aspirin?",
            "evidence": "Aspirin is an NSAID.",
            "response": "Aspirin reduces inflammation.",
        }

        with patch.dict("sys.modules", {"litellm": MagicMock(acompletion=mock_acompletion)}):
            with patch("lifesci_tools.llm_grader_worker.asyncio.wait_for") as mock_wait_for:
                mock_wait_for.return_value = mock_response
                score = await llm_grader_worker._grade_one(job)

        assert score == 0.85

    @pytest.mark.asyncio
    async def test_grade_one_timeout(self):
        """Mock litellm.acompletion to raise TimeoutError, verify returns None."""
        job = {
            "session_id": "sess-timeout",
            "question": "What is aspirin?",
            "evidence": "Aspirin evidence.",
            "response": "Aspirin response.",
        }

        with patch("lifesci_tools.llm_grader_worker.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            with patch.dict("sys.modules", {"litellm": MagicMock()}):
                score = await llm_grader_worker._grade_one(job)

        assert score is None

    @pytest.mark.asyncio
    async def test_grade_one_unparseable(self):
        """Mock litellm.acompletion to return 'great job!', verify returns None."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "great job!"

        job = {
            "session_id": "sess-unparse",
            "question": "What is aspirin?",
            "evidence": "Aspirin is an NSAID.",
            "response": "Aspirin reduces inflammation.",
        }

        with patch.dict("sys.modules", {"litellm": MagicMock(acompletion=AsyncMock(return_value=mock_response))}):
            with patch("lifesci_tools.llm_grader_worker.asyncio.wait_for") as mock_wait_for:
                mock_wait_for.return_value = mock_response
                score = await llm_grader_worker._grade_one(job)

        assert score is None
