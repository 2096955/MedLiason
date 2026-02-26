"""Integration tests for triage SSE emission pipeline.

Exercises the *real* ``emit_triage_progress`` function (not a mock) so that
the cumulative state pattern, SSE signal construction, and per-tool emission
are fully verified.
"""

import logging
import sys
from unittest.mock import MagicMock

import pytest

from lifesci_common.triage_utils import (
    _TRIAGE_CUMULATIVE_KEY,
    emit_triage_progress,
)

# Pre-build a litellm stub and inject into sys.modules so that the lazy
# ``import litellm`` inside triage_evaluation._run_async_impl never triggers
# the real (very slow) import.  Individual tests override .completion as needed.
_litellm_stub = MagicMock()
if "litellm" not in sys.modules:
    sys.modules["litellm"] = _litellm_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_tool_context():
    """Minimal tool_context with state dict and SSE emission support.

    Sets up the full chain:
    ``tool_context._invocation_context.agent.host_component``
    so that ``emit_triage_progress`` can traverse it via ``getattr``.
    """
    ctx = MagicMock()
    ctx.state = {}

    host = MagicMock()
    host.publish_data_signal_from_thread = MagicMock(return_value=True)
    agent = MagicMock()
    agent.host_component = host
    inv = MagicMock()
    inv.agent = agent
    ctx._invocation_context = inv

    return ctx


# ---------------------------------------------------------------------------
# TestCumulativeEmission
# ---------------------------------------------------------------------------


class TestCumulativeEmission:
    """Verify that SSE emissions accumulate state across tool calls."""

    @pytest.mark.asyncio
    async def test_state_accumulates_across_calls(self, mock_tool_context):
        """Later stage emissions include data from earlier stages."""
        mock_tool_context.state["a2a_context"] = {"task_id": "test-123"}

        # Stage 2: routing
        await emit_triage_progress(
            mock_tool_context,
            stage=2,
            stage_name="ROUTING",
            detail="Selected 4 specialists",
        )

        cumulative = mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY]
        assert cumulative["stage"] == 2
        assert cumulative["stage_name"] == "ROUTING"
        assert cumulative["detail"] == "Selected 4 specialists"

        # Stage 3: specialist panel
        verdicts = [
            {"specialist": "internist", "diagnosis": "Flu", "confidence": 80}
        ]
        await emit_triage_progress(
            mock_tool_context,
            stage=3,
            stage_name="SPECIALIST_PANEL",
            detail="Received 1 verdict",
            specialist_verdicts=verdicts,
        )

        cumulative = mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY]
        assert cumulative["stage"] == 3
        assert cumulative["stage_name"] == "SPECIALIST_PANEL"
        assert cumulative["specialist_verdicts"] == verdicts
        # Detail updated to latest stage
        assert cumulative["detail"] == "Received 1 verdict"

        # publish_data_signal_from_thread was called once per stage
        host = mock_tool_context._invocation_context.agent.host_component
        assert host.publish_data_signal_from_thread.call_count == 2

    @pytest.mark.asyncio
    async def test_stage_5_includes_all_prior_data(self, mock_tool_context):
        """Final stage 5 emission includes all accumulated data."""
        mock_tool_context.state["a2a_context"] = {"task_id": "test-123"}

        # Simulate stages 2-4 already accumulated in state
        mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY] = {
            "stage": 4,
            "stage_name": "EVALUATION",
            "detail": "Evaluation complete",
            "specialist_verdicts": [
                {"specialist": "internist", "diagnosis": "Flu", "confidence": 80}
            ],
            "consensus": {"consensus_diagnosis": "Flu", "mean_confidence": 80},
            "evaluation": {"eval_confidence": 85, "flag_for_review": False},
        }

        nba = {"route": "gp_visit", "urgency": "routine", "reasoning": "Test"}
        await emit_triage_progress(
            mock_tool_context,
            stage=5,
            stage_name="NBA",
            detail="Routed to gp_visit",
            nba=nba,
        )

        cumulative = mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY]
        assert cumulative["stage"] == 5
        assert cumulative["stage_name"] == "NBA"
        assert cumulative["nba"] == nba
        # Prior stages preserved
        assert cumulative["specialist_verdicts"] is not None
        assert len(cumulative["specialist_verdicts"]) == 1
        assert cumulative["consensus"] is not None
        assert cumulative["evaluation"] is not None

    @pytest.mark.asyncio
    async def test_none_values_not_overwritten(self, mock_tool_context):
        """None kwargs don't overwrite existing cumulative data.

        The helper skips ``value is not None`` so passing an explicit
        ``specialist_verdicts=None`` (or simply omitting it) must not
        clobber a previously stored value.
        """
        mock_tool_context.state["a2a_context"] = {"task_id": "test-123"}

        # Pre-populate with verdicts from an earlier stage
        mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY] = {
            "specialist_verdicts": [{"specialist": "test", "confidence": 90}],
        }

        # Emit stage 4 without specialist_verdicts kwarg
        await emit_triage_progress(
            mock_tool_context,
            stage=4,
            stage_name="EVALUATION",
            detail="Test",
        )

        cumulative = mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY]
        # specialist_verdicts should still be there from the prior stage
        assert cumulative["specialist_verdicts"] is not None
        assert cumulative["specialist_verdicts"][0]["specialist"] == "test"

    @pytest.mark.asyncio
    async def test_explicit_none_does_not_clobber(self, mock_tool_context):
        """Passing specialist_verdicts=None explicitly must not overwrite."""
        mock_tool_context.state["a2a_context"] = {"task_id": "test-123"}

        mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY] = {
            "specialist_verdicts": [{"specialist": "cardio", "confidence": 75}],
        }

        await emit_triage_progress(
            mock_tool_context,
            stage=4,
            stage_name="EVALUATION",
            detail="Eval done",
            specialist_verdicts=None,  # explicitly None
        )

        cumulative = mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY]
        assert cumulative["specialist_verdicts"][0]["specialist"] == "cardio"

    @pytest.mark.asyncio
    async def test_unknown_kwargs_silently_dropped(self, mock_tool_context):
        """kwargs not in _KNOWN_FIELDS are silently dropped."""
        mock_tool_context.state["a2a_context"] = {"task_id": "test-123"}

        await emit_triage_progress(
            mock_tool_context,
            stage=2,
            stage_name="ROUTING",
            detail="Test",
            not_a_real_field="should be ignored",
        )

        cumulative = mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY]
        assert "not_a_real_field" not in cumulative

    @pytest.mark.asyncio
    async def test_empty_initial_state(self, mock_tool_context):
        """First call with no prior cumulative state starts fresh."""
        mock_tool_context.state["a2a_context"] = {"task_id": "test-123"}

        assert _TRIAGE_CUMULATIVE_KEY not in mock_tool_context.state

        await emit_triage_progress(
            mock_tool_context,
            stage=1,
            stage_name="INTAKE",
            detail="Processing intake",
        )

        cumulative = mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY]
        assert cumulative["stage"] == 1
        assert cumulative["stage_name"] == "INTAKE"
        assert cumulative.get("specialist_verdicts") is None
        assert cumulative.get("nba") is None


# ---------------------------------------------------------------------------
# TestSSEEmission
# ---------------------------------------------------------------------------


class TestSSEEmission:
    """Verify SSE signal is published correctly via host component."""

    @pytest.mark.asyncio
    async def test_publishes_triage_progress_data(self, mock_tool_context):
        """publish_data_signal_from_thread receives a TriageProgressData model."""
        mock_tool_context.state["a2a_context"] = {"task_id": "test-123"}

        await emit_triage_progress(
            mock_tool_context,
            stage=3,
            stage_name="SPECIALIST_PANEL",
            detail="Test",
            specialist_verdicts=[],
        )

        host = mock_tool_context._invocation_context.agent.host_component
        host.publish_data_signal_from_thread.assert_called_once()

        # Extract the signal from keyword args
        call_kwargs = host.publish_data_signal_from_thread.call_args
        signal = call_kwargs.kwargs.get("signal_data")
        if signal is None:
            # Positional arg fallback (a2a_context, signal_data, ...)
            signal = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
        assert signal is not None

        from solace_agent_mesh.common.data_parts import TriageProgressData

        assert isinstance(signal, TriageProgressData)
        assert signal.stage == 3
        assert signal.stage_name == "SPECIALIST_PANEL"
        assert signal.type == "triage_progress"

    @pytest.mark.asyncio
    async def test_a2a_context_forwarded(self, mock_tool_context):
        """The a2a_context dict is forwarded to publish_data_signal_from_thread."""
        a2a = {"task_id": "test-456", "session_id": "s1"}
        mock_tool_context.state["a2a_context"] = a2a

        await emit_triage_progress(
            mock_tool_context,
            stage=2,
            stage_name="ROUTING",
            detail="Test",
        )

        host = mock_tool_context._invocation_context.agent.host_component
        call_kwargs = host.publish_data_signal_from_thread.call_args.kwargs
        assert call_kwargs["a2a_context"] is a2a

    @pytest.mark.asyncio
    async def test_warning_logged_when_a2a_context_missing(
        self, mock_tool_context, caplog
    ):
        """WARNING log emitted when a2a_context not in tool_context.state."""
        # Deliberately do NOT set a2a_context
        with caplog.at_level(logging.WARNING, logger="lifesci_common.triage_utils"):
            await emit_triage_progress(
                mock_tool_context,
                stage=2,
                stage_name="ROUTING",
                detail="Test",
            )

        assert "a2a_context" in caplog.text
        assert "SSE emission skipped" in caplog.text

        # Cumulative state is still written even when SSE is skipped
        cumulative = mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY]
        assert cumulative["stage"] == 2

    @pytest.mark.asyncio
    async def test_publish_not_called_when_a2a_missing(self, mock_tool_context):
        """No publish attempt when a2a_context is absent."""
        await emit_triage_progress(
            mock_tool_context,
            stage=2,
            stage_name="ROUTING",
            detail="Test",
        )

        host = mock_tool_context._invocation_context.agent.host_component
        host.publish_data_signal_from_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_silent_on_host_exception(self, mock_tool_context):
        """Exceptions during publish are caught — no raise to caller."""
        mock_tool_context.state["a2a_context"] = {"task_id": "test-123"}
        host = mock_tool_context._invocation_context.agent.host_component
        host.publish_data_signal_from_thread.side_effect = RuntimeError(
            "broker down"
        )

        # Should not raise
        await emit_triage_progress(
            mock_tool_context,
            stage=2,
            stage_name="ROUTING",
            detail="Test",
        )

        # Cumulative state is still written (exception is after write-back)
        cumulative = mock_tool_context.state[_TRIAGE_CUMULATIVE_KEY]
        assert cumulative["stage"] == 2

    @pytest.mark.asyncio
    async def test_warning_when_invocation_context_missing(
        self, mock_tool_context, caplog
    ):
        """WARNING when _invocation_context is None."""
        mock_tool_context.state["a2a_context"] = {"task_id": "test-123"}
        # Remove _invocation_context
        del mock_tool_context._invocation_context

        with caplog.at_level(logging.WARNING, logger="lifesci_common.triage_utils"):
            await emit_triage_progress(
                mock_tool_context,
                stage=2,
                stage_name="ROUTING",
                detail="Test",
            )

        assert "_invocation_context" in caplog.text
        assert "SSE emission skipped" in caplog.text


# ---------------------------------------------------------------------------
# TestEvaluationToolEmitsStage4
# ---------------------------------------------------------------------------


class TestEvaluationToolEmitsStage4:
    """Verify triage_evaluation tool emits stage 4 SSE."""

    @pytest.mark.asyncio
    async def test_evaluation_emits_stage_4(self):
        """TriageEvaluationTool emits stage 4 progress on normal path."""
        from lifesci_tools.triage_evaluation import TriageEvaluationTool

        tool = TriageEvaluationTool()
        tool.tool_config = {"model": "test-model"}

        ctx = MagicMock()
        ctx.state = {"a2a_context": {"task_id": "t1"}}
        host = MagicMock()
        host.publish_data_signal_from_thread = MagicMock(return_value=True)
        agent = MagicMock()
        agent.host_component = host
        inv = MagicMock()
        inv.agent = agent
        ctx._invocation_context = inv

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"eval_confidence": 75, "explanation": "Good match", '
            '"flag_for_review": false, "flag_reason": null}'
        )

        litellm_mod = sys.modules["litellm"]
        litellm_mod.completion = MagicMock(return_value=mock_response)

        result = await tool._run_async_impl(
            {
                "consensus_diagnosis": "Common Cold",
                "mean_confidence": "80",
                "clinical_note": '{"chief_complaint": "runny nose"}',
            },
            ctx,
        )

        assert result["eval_confidence"] == 75
        assert result["diagnosis"] == "Common Cold"

        # Verify SSE was emitted
        assert host.publish_data_signal_from_thread.called
        call_kwargs = host.publish_data_signal_from_thread.call_args.kwargs
        signal = call_kwargs.get("signal_data")
        assert signal is not None
        assert signal.stage == 4
        assert signal.stage_name == "EVALUATION"

    @pytest.mark.asyncio
    async def test_evaluation_emergency_emits_stage_4(self):
        """Emergency fast-path also emits stage 4."""
        from lifesci_tools.triage_evaluation import TriageEvaluationTool

        tool = TriageEvaluationTool()
        tool.tool_config = {}

        ctx = MagicMock()
        ctx.state = {"a2a_context": {"task_id": "t2"}}
        host = MagicMock()
        host.publish_data_signal_from_thread = MagicMock(return_value=True)
        agent = MagicMock()
        agent.host_component = host
        inv = MagicMock()
        inv.agent = agent
        ctx._invocation_context = inv

        result = await tool._run_async_impl(
            {
                "consensus_diagnosis": "Chest pain",
                "mean_confidence": "0",
                "clinical_note": '{"chief_complaint": "chest pain, shortness of breath"}',
                "emergency_override": True,
            },
            ctx,
        )

        assert result["confirmed_emergency"] is True or result["confirmed_emergency"] is False
        # Verify SSE was emitted for emergency path too
        assert host.publish_data_signal_from_thread.called
        signal = host.publish_data_signal_from_thread.call_args.kwargs["signal_data"]
        assert signal.stage == 4
        assert signal.stage_name == "EVALUATION"

    @pytest.mark.asyncio
    async def test_evaluation_cumulative_includes_consensus(self):
        """Stage 4 emission includes consensus from kwargs."""
        from lifesci_tools.triage_evaluation import TriageEvaluationTool

        tool = TriageEvaluationTool()
        tool.tool_config = {"model": "test-model"}

        ctx = MagicMock()
        ctx.state = {
            "a2a_context": {"task_id": "t3"},
            _TRIAGE_CUMULATIVE_KEY: {
                "specialist_verdicts": [
                    {"specialist": "GP", "diagnosis": "Cold", "confidence": 80}
                ],
            },
        }
        host = MagicMock()
        host.publish_data_signal_from_thread = MagicMock(return_value=True)
        agent = MagicMock()
        agent.host_component = host
        inv = MagicMock()
        inv.agent = agent
        ctx._invocation_context = inv

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"eval_confidence": 82, "explanation": "Strong match", '
            '"flag_for_review": false, "flag_reason": null}'
        )

        litellm_mod = sys.modules["litellm"]
        litellm_mod.completion = MagicMock(return_value=mock_response)

        await tool._run_async_impl(
            {
                "consensus_diagnosis": "Common Cold",
                "mean_confidence": "80",
                "clinical_note": '{"chief_complaint": "runny nose"}',
            },
            ctx,
        )

        # Cumulative state should include both prior verdicts and new evaluation
        cumulative = ctx.state[_TRIAGE_CUMULATIVE_KEY]
        assert cumulative["specialist_verdicts"] is not None
        assert cumulative["evaluation"] is not None
        assert cumulative["consensus"] is not None


# ---------------------------------------------------------------------------
# TestNBAToolEmitsStage5
# ---------------------------------------------------------------------------


class TestNBAToolEmitsStage5:
    """Verify triage_nba tool emits stage 5 SSE."""

    @pytest.mark.asyncio
    async def test_nba_emits_stage_5(self):
        """TriageNBATool emits stage 5 progress."""
        from lifesci_tools.triage_nba import TriageNBATool

        tool = TriageNBATool()
        tool.tool_config = {}  # No cold_db_path → dispatch skipped

        ctx = MagicMock()
        ctx.state = {"a2a_context": {"task_id": "t1"}}
        host = MagicMock()
        host.publish_data_signal_from_thread = MagicMock(return_value=True)
        agent = MagicMock()
        agent.host_component = host
        inv = MagicMock()
        inv.agent = agent
        ctx._invocation_context = inv

        result = await tool._run_async_impl(
            {
                "consensus_diagnosis": "Common Cold",
                "mean_confidence": "80",
                "eval_confidence": "85",
            },
            ctx,
        )

        assert result["route"] == "self_care"

        # Verify SSE was emitted
        assert host.publish_data_signal_from_thread.called
        signal = host.publish_data_signal_from_thread.call_args.kwargs["signal_data"]
        assert signal.stage == 5
        assert signal.stage_name == "NBA"

    @pytest.mark.asyncio
    async def test_nba_emergency_emits_stage_5(self):
        """Emergency override NBA also emits stage 5."""
        from lifesci_tools.triage_nba import TriageNBATool

        tool = TriageNBATool()
        tool.tool_config = {}

        ctx = MagicMock()
        ctx.state = {"a2a_context": {"task_id": "t2"}}
        host = MagicMock()
        host.publish_data_signal_from_thread = MagicMock(return_value=True)
        agent = MagicMock()
        agent.host_component = host
        inv = MagicMock()
        inv.agent = agent
        ctx._invocation_context = inv

        result = await tool._run_async_impl(
            {
                "consensus_diagnosis": "Chest pain",
                "mean_confidence": "0",
                "eval_confidence": "0",
                "emergency_override": True,
            },
            ctx,
        )

        assert result["route"] == "emergency_services"
        assert result["urgency"] == "immediate"

        signal = host.publish_data_signal_from_thread.call_args.kwargs["signal_data"]
        assert signal.stage == 5
        assert signal.stage_name == "NBA"
        assert signal.emergency_override is True

    @pytest.mark.asyncio
    async def test_nba_cumulative_preserves_prior_stages(self):
        """Stage 5 emission preserves specialist_verdicts and evaluation from prior stages."""
        from lifesci_tools.triage_nba import TriageNBATool

        tool = TriageNBATool()
        tool.tool_config = {}

        ctx = MagicMock()
        ctx.state = {
            "a2a_context": {"task_id": "t3"},
            _TRIAGE_CUMULATIVE_KEY: {
                "stage": 4,
                "stage_name": "EVALUATION",
                "detail": "Evaluation done",
                "specialist_verdicts": [
                    {
                        "specialist": "internist",
                        "diagnosis": "Common Cold",
                        "confidence": 80,
                    }
                ],
                "consensus": {
                    "consensus_diagnosis": "Common Cold",
                    "mean_confidence": 80,
                },
                "evaluation": {
                    "eval_confidence": 85,
                    "flag_for_review": False,
                },
            },
        }
        host = MagicMock()
        host.publish_data_signal_from_thread = MagicMock(return_value=True)
        agent = MagicMock()
        agent.host_component = host
        inv = MagicMock()
        inv.agent = agent
        ctx._invocation_context = inv

        await tool._run_async_impl(
            {
                "consensus_diagnosis": "Common Cold",
                "mean_confidence": "80",
                "eval_confidence": "85",
            },
            ctx,
        )

        # Full cumulative state at stage 5
        cumulative = ctx.state[_TRIAGE_CUMULATIVE_KEY]
        assert cumulative["stage"] == 5
        assert cumulative["stage_name"] == "NBA"
        assert cumulative["specialist_verdicts"] is not None
        assert cumulative["consensus"] is not None
        assert cumulative["evaluation"] is not None
        assert cumulative["nba"] is not None
        assert cumulative["nba"]["route"] == "self_care"
