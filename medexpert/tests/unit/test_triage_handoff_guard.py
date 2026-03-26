"""Tests for the triage handoff guard after_model_callback."""

from unittest.mock import MagicMock

from google.genai import types as adk_types

from lifesci_tools.triage_handoff_guard import triage_handoff_guard_callback


def _make_callback_context(state: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.state = state or {}
    return ctx


def _make_llm_response(
    text: str | None = None,
    function_calls: list[tuple[str, dict]] | None = None,
    partial: bool = False,
) -> MagicMock:
    """Build a mock LlmResponse with optional text and function calls."""
    resp = MagicMock()
    resp.partial = partial

    parts = []
    if text:
        parts.append(adk_types.Part(text=text))
    for name, args in function_calls or []:
        parts.append(
            adk_types.Part(
                function_call=adk_types.FunctionCall(
                    id=f"call_{name}", name=name, args=args
                )
            )
        )

    resp.content = adk_types.Content(role="model", parts=parts) if parts else None
    return resp


def _make_host(agent_name: str = "TriageIntakeAgent", handoff_guard: bool = True):
    host = MagicMock()
    host.get_config.side_effect = lambda key, default=None: {
        "agent_name": agent_name,
        "app_config": {"triage_handoff_guard": handoff_guard},
    }.get(key, default)
    return host


class TestHandoffGuardRejects:
    """Cases where the guard should soft-reject."""

    def test_rejects_text_only_after_complete_intake(self):
        """Text mentioning routing without peer tool call -> reject."""
        ctx = _make_callback_context(state={"_triage_intake_complete": True})
        resp = _make_llm_response(
            text="Thank you. I'm now routing your case to our specialist panel."
        )
        host = _make_host()

        result = triage_handoff_guard_callback(ctx, resp, host)

        assert result is not None  # Modified response returned
        # Should contain guidance text, not the original
        assert any(
            "peer_TriageOrchestratorAgent" in (p.text or "")
            for p in result.content.parts
        )

    def test_rejects_when_specialist_panel_mentioned(self):
        """Any text indicating handoff intent without tool call -> reject."""
        ctx = _make_callback_context(state={"_triage_intake_complete": True})
        resp = _make_llm_response(
            text="I'm routing this to the specialist panel for analysis."
        )
        host = _make_host()

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is not None


class TestHandoffGuardProOpusVariants:
    """Tests for Pro/Opus agent name and peer tool compatibility."""

    def test_passes_pro_peer_tool_call(self):
        """peer_TriageOrchestratorAgentPro is recognised as the correct peer tool."""
        ctx = _make_callback_context(state={"_triage_intake_complete": True})
        resp = _make_llm_response(
            text="Routing your case now.",
            function_calls=[
                (
                    "peer_TriageOrchestratorAgentPro",
                    {"task_description": "triage"},
                )
            ],
        )
        host = _make_host(agent_name="TriageIntakeAgentPro")

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None  # Should pass through

    def test_passes_opus_peer_tool_call(self):
        """peer_TriageOrchestratorAgentOpus is recognised as the correct peer tool."""
        ctx = _make_callback_context(state={"_triage_intake_complete": True})
        resp = _make_llm_response(
            text="Routing your case now.",
            function_calls=[
                (
                    "peer_TriageOrchestratorAgentOpus",
                    {"task_description": "triage"},
                )
            ],
        )
        host = _make_host(agent_name="TriageIntakeAgentOpus")

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None  # Should pass through

    def test_rejects_pro_text_only(self):
        """Pro variant still rejects text-only handoff."""
        ctx = _make_callback_context(state={"_triage_intake_complete": True})
        resp = _make_llm_response(text="Routing your case to our specialist panel.")
        host = _make_host(agent_name="TriageIntakeAgentPro")

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is not None
        # Rejection text should reference the Pro peer tool name
        assert any(
            "peer_TriageOrchestratorAgentPro" in (p.text or "")
            for p in result.content.parts
        )

    def test_activates_for_pro_intake_agent(self):
        """Guard fires for TriageIntakeAgentPro (contains 'TriageIntakeAgent')."""
        ctx = _make_callback_context(state={"_triage_intake_complete": True})
        resp = _make_llm_response(
            text="I'm routing this to the specialist panel for analysis."
        )
        host = _make_host(agent_name="TriageIntakeAgentPro")

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is not None  # Guard should activate


class TestHandoffGuardPasses:
    """Cases where the guard should NOT reject (return None)."""

    def test_passes_text_plus_peer_tool_call(self):
        """Text + peer_TriageOrchestratorAgent call -> pass through."""
        ctx = _make_callback_context(state={"_triage_intake_complete": True})
        resp = _make_llm_response(
            text="Thank you. Routing your case now.",
            function_calls=[
                ("peer_TriageOrchestratorAgent", {"task_description": "triage"})
            ],
        )
        host = _make_host()

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None  # No modification

    def test_passes_when_intake_not_complete(self):
        """If triage_intake hasn't returned complete, guard is inactive."""
        ctx = _make_callback_context(state={})
        resp = _make_llm_response(
            text="What is the main reason you are seeking medical help today?"
        )
        host = _make_host()

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None

    def test_passes_when_guard_disabled(self):
        """Guard disabled in config -> always pass through."""
        ctx = _make_callback_context(state={"_triage_intake_complete": True})
        resp = _make_llm_response(text="Routing to specialist panel.")
        host = _make_host(handoff_guard=False)

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None

    def test_passes_partial_responses(self):
        """Streaming partial responses are never rejected."""
        ctx = _make_callback_context(state={"_triage_intake_complete": True})
        resp = _make_llm_response(text="Routing to specialist panel.", partial=True)
        host = _make_host()

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None

    def test_passes_for_non_intake_agent(self):
        """Guard only fires for TriageIntakeAgent."""
        ctx = _make_callback_context(state={"_triage_intake_complete": True})
        resp = _make_llm_response(text="Routing to specialist panel.")
        host = _make_host(agent_name="OrchestratorAgent")

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None
