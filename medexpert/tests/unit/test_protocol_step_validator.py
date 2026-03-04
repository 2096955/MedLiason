"""Tests for the protocol step validator callback.

Validates the 7-step research protocol enforcement: tool-level gating,
predictive step advancement, session state management, and soft rejection
of out-of-step tool calls.
"""

from unittest.mock import MagicMock

import pytest

from lifesci_tools.protocol_step_validator import (
    ALLOWED_TOOLS_PER_STEP,
    STEP_LABELS,
    _SELECTED_AGENTS_KEY,
    _SESSION_STATE_KEY,
    _detect_step_advancement,
    _is_tool_allowed,
    cleanup_step_state,
    protocol_step_validator_callback,
)

# Lazy import — these are only needed for callback tests
from google.adk.models.llm_response import LlmResponse
from google.genai import types as adk_types


# ── Fixtures ──────────────────────────────────────────────────────────


def _make_callback_context(session_state: dict | None = None):
    """Build a minimal mock CallbackContext with session.state."""
    ctx = MagicMock()
    session = MagicMock()
    session.state = session_state if session_state is not None else {}
    ctx._invocation_context = MagicMock()
    ctx._invocation_context.session = session
    return ctx, session


def _make_host_component(protocol_enforcement: bool = True):
    """Build a mock SamAgentComponent with get_config returning app_config."""
    comp = MagicMock()
    comp.get_config.side_effect = lambda key, default=None: (
        {"protocol_enforcement": protocol_enforcement}
        if key == "app_config"
        else default
    )
    comp.log_identifier = "[test]"
    return comp


def _make_llm_response(*tool_calls, text_parts=None):
    """Create an LlmResponse containing function_call parts.

    Each *tool_calls* entry is a tuple ``(name, args_dict)``.
    """
    parts = []
    if text_parts:
        for t in text_parts:
            parts.append(adk_types.Part(text=t))
    for name, args in tool_calls:
        fc = adk_types.FunctionCall(name=name, args=args or {})
        parts.append(adk_types.Part(function_call=fc))
    return LlmResponse(
        content=adk_types.Content(role="model", parts=parts),
        partial=False,
    )


# ── _is_tool_allowed tests ───────────────────────────────────────────


class TestIsToolAllowed:
    """Test the tool allowance checker."""

    def test_memory_plane_allowed_at_step_0(self):
        assert _is_tool_allowed("memory_plane", 0) is True

    def test_phi_redactor_allowed_at_step_0(self):
        assert _is_tool_allowed("phi_redactor", 0) is True

    def test_query_decomposer_not_allowed_at_step_0(self):
        """Must do SEED first."""
        assert _is_tool_allowed("query_decomposer", 0) is False

    def test_query_decomposer_allowed_at_step_1(self):
        assert _is_tool_allowed("query_decomposer", 1) is True

    def test_peer_literature_allowed_at_step_2_glob(self):
        """peer_* glob matches any tool starting with 'peer_'."""
        assert _is_tool_allowed("peer_LiteratureSpecialist", 2) is True

    def test_peer_literature_not_allowed_at_step_1(self):
        """PLAN step does not allow delegation."""
        assert _is_tool_allowed("peer_LiteratureSpecialist", 1) is False

    def test_peer_tool_allowed_at_step_3_collect(self):
        """COLLECT step also permits peer_* for follow-up delegation."""
        assert _is_tool_allowed("peer_DrugSpecialist", 3) is True

    def test_report_generator_not_allowed_at_step_3(self):
        """Cannot synthesize during COLLECT."""
        assert _is_tool_allowed("report_generator", 3) is False

    def test_report_generator_allowed_at_step_4(self):
        assert _is_tool_allowed("report_generator", 4) is True

    def test_peer_verifier_allowed_at_step_5(self):
        assert _is_tool_allowed("peer_VerifierAgent", 5) is True

    def test_peer_reviser_allowed_at_step_5(self):
        assert _is_tool_allowed("peer_ReviserAgent", 5) is True

    def test_peer_verifier_pro_allowed_at_step_5(self):
        """Pro variant verifier must pass step 5 (glob pattern: peer_Verifier*)."""
        assert _is_tool_allowed("peer_VerifierAgentPro", 5) is True

    def test_peer_verifier_opus_allowed_at_step_5(self):
        """Opus variant verifier must pass step 5."""
        assert _is_tool_allowed("peer_VerifierAgentOpus", 5) is True

    def test_peer_reviser_pro_allowed_at_step_5(self):
        """Pro variant reviser must pass step 5."""
        assert _is_tool_allowed("peer_ReviserAgentPro", 5) is True

    def test_peer_reviser_opus_allowed_at_step_5(self):
        """Opus variant reviser must pass step 5."""
        assert _is_tool_allowed("peer_ReviserAgentOpus", 5) is True

    def test_memory_plane_allowed_at_step_6(self):
        assert _is_tool_allowed("memory_plane", 6) is True

    def test_publish_sources_allowed_at_step_3(self):
        assert _is_tool_allowed("publish_sources", 3) is True

    def test_completeness_checker_allowed_at_step_3(self):
        assert _is_tool_allowed("completeness_checker", 3) is True

    def test_unknown_step_is_permissive(self):
        """Unknown step numbers should not block (fallback)."""
        assert _is_tool_allowed("any_tool", 99) is True

    def test_phi_redactor_allowed_at_step_4(self):
        """Pre-output safety during SYNTHESIZE."""
        assert _is_tool_allowed("phi_redactor", 4) is True

    def test_arbitrary_peer_at_step_2(self):
        """Any peer_* tool should match the glob at DELEGATE step."""
        assert _is_tool_allowed("peer_EpidemiologySpecialist", 2) is True
        assert _is_tool_allowed("peer_GenomicsSpecialist", 2) is True


# ── _detect_step_advancement tests ───────────────────────────────────


class TestDetectStepAdvancement:
    """Test predictive step advancement logic."""

    def test_seed_session_advances_to_1(self):
        """seed_session completes step 0, advances to step 1 (ready for PLAN)."""
        result = _detect_step_advancement(
            "memory_plane", {"operation": "seed_session"}, current_step=0
        )
        assert result == 1

    def test_query_decomposer_advances_to_2(self):
        """query_decomposer completes step 1, advances to step 2 (ready for DELEGATE)."""
        result = _detect_step_advancement(
            "query_decomposer", {}, current_step=1
        )
        assert result == 2

    def test_peer_tool_at_step_0_advances_to_2(self):
        """Any peer_* tool when current_step < 2 should advance to 2."""
        result = _detect_step_advancement(
            "peer_LiteratureSpecialist", {}, current_step=0
        )
        assert result == 2

    def test_peer_tool_at_step_1_advances_to_2(self):
        result = _detect_step_advancement(
            "peer_ClinicalTrialsSpecialist", {}, current_step=1
        )
        assert result == 2

    def test_peer_tool_at_step_2_stays_at_2(self):
        """Already at DELEGATE — no advancement."""
        result = _detect_step_advancement(
            "peer_DrugSpecialist", {}, current_step=2
        )
        assert result == 2

    def test_publish_sources_advances_to_3(self):
        result = _detect_step_advancement(
            "publish_sources", {}, current_step=2
        )
        assert result == 3

    def test_report_generator_advances_to_5(self):
        """report_generator completes step 4, advances to step 5 (ready for VERIFY)."""
        result = _detect_step_advancement(
            "report_generator", {}, current_step=4
        )
        assert result == 5

    def test_peer_verifier_advances_to_5(self):
        result = _detect_step_advancement(
            "peer_VerifierAgent", {}, current_step=4
        )
        assert result == 5

    def test_peer_verifier_pro_advances_to_5(self):
        """Pro variant verifier must advance to step 5."""
        result = _detect_step_advancement(
            "peer_VerifierAgentPro", {}, current_step=4
        )
        assert result == 5

    def test_peer_verifier_opus_advances_to_5(self):
        """Opus variant verifier must advance to step 5."""
        result = _detect_step_advancement(
            "peer_VerifierAgentOpus", {}, current_step=4
        )
        assert result == 5

    def test_peer_reviser_pro_advances_to_5(self):
        """Pro variant reviser stays at step 5."""
        result = _detect_step_advancement(
            "peer_ReviserAgentPro", {}, current_step=5
        )
        assert result == 5

    def test_flush_cold_advances_to_6(self):
        result = _detect_step_advancement(
            "memory_plane", {"operation": "flush_cold"}, current_step=5
        )
        assert result == 6

    def test_no_backward_step(self):
        """Step should never decrease — triggers only when new >= current."""
        result = _detect_step_advancement(
            "memory_plane", {"operation": "seed_session"}, current_step=3
        )
        assert result == 3  # stays at 3, seed_session trigger (1) < current (3)

    def test_memory_plane_store_no_advancement(self):
        """Regular memory_plane operations (store/retrieve) don't advance."""
        result = _detect_step_advancement(
            "memory_plane", {"operation": "store"}, current_step=2
        )
        assert result == 2

    def test_completeness_checker_advances_to_3(self):
        result = _detect_step_advancement(
            "completeness_checker", {}, current_step=2
        )
        assert result == 3

    def test_peer_reviser_advances_to_5(self):
        result = _detect_step_advancement(
            "peer_ReviserAgent", {}, current_step=4
        )
        assert result == 5


# ── cleanup_step_state tests ─────────────────────────────────────────


class TestCleanupStepState:
    """Test session state cleanup."""

    def test_resets_to_0(self):
        state = {_SESSION_STATE_KEY: 4}
        cleanup_step_state(state)
        assert state[_SESSION_STATE_KEY] == 0

    def test_sets_key_if_missing(self):
        state = {}
        cleanup_step_state(state)
        assert state[_SESSION_STATE_KEY] == 0


# ── Callback integration tests ───────────────────────────────────────


class TestProtocolStepValidatorCallback:
    """Test the full callback function."""

    def test_returns_none_when_enforcement_disabled(self):
        """Non-orchestrator agents (no protocol_enforcement) are skipped."""
        ctx, _ = _make_callback_context()
        comp = _make_host_component(protocol_enforcement=False)
        llm_resp = _make_llm_response(("query_decomposer", {}))

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None

    def test_returns_none_for_partial_response(self):
        """Streaming (partial) responses are not validated."""
        ctx, _ = _make_callback_context()
        comp = _make_host_component()
        llm_resp = LlmResponse(
            content=adk_types.Content(
                role="model",
                parts=[adk_types.Part(function_call=adk_types.FunctionCall(
                    name="query_decomposer", args={}
                ))],
            ),
            partial=True,
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None

    def test_returns_none_for_text_only_response(self):
        """Responses with no function calls are passed through."""
        ctx, _ = _make_callback_context()
        comp = _make_host_component()
        llm_resp = LlmResponse(
            content=adk_types.Content(
                role="model",
                parts=[adk_types.Part(text="Thinking about the next step...")],
            ),
            partial=False,
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None

    def test_valid_tool_at_step_0_passes(self):
        """memory_plane at step 0 should pass through."""
        session_state = {_SESSION_STATE_KEY: 0}
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(
            ("memory_plane", {"operation": "seed_session", "query": "test"})
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None

    def test_invalid_tool_at_step_0_rejected(self):
        """query_decomposer at step 0 should be rejected (must SEED first)."""
        session_state = {_SESSION_STATE_KEY: 0}
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(("query_decomposer", {}))

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is not None
        # Should contain error text
        text_parts = [p.text for p in result.content.parts if p.text]
        assert any("[Protocol Error]" in t for t in text_parts)
        assert any("query_decomposer" in t for t in text_parts)
        # Should force re-planning
        assert result.turn_complete is False

    def test_predictive_advancement_allows_step_sequence(self):
        """Calling seed_session then query_decomposer in same turn should pass.

        seed_session is valid at step 0 and advances to step 1.
        query_decomposer is then valid at step 1 and advances to step 2.
        """
        session_state = {_SESSION_STATE_KEY: 0}
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(
            ("memory_plane", {"operation": "seed_session", "query": "test"}),
            ("query_decomposer", {"question": "What are treatments for X?"}),
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        # Both tools should be valid with predictive advancement
        assert result is None
        # Session state should be updated to step 2 (ready for DELEGATE)
        assert session_state[_SESSION_STATE_KEY] == 2

    def test_peer_delegation_at_step_2(self):
        """Peer tools at DELEGATE step should pass."""
        session_state = {_SESSION_STATE_KEY: 2}
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(
            ("peer_LiteratureSpecialist", {"task": "search"}),
            ("peer_DrugSpecialist", {"task": "lookup"}),
            ("peer_GenomicsSpecialist", {"task": "variants"}),
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None

    def test_report_generator_rejected_at_step_3(self):
        """Cannot synthesize during COLLECT step."""
        session_state = {_SESSION_STATE_KEY: 3}
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(("report_generator", {"mode": "full_synthesis"}))

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is not None
        text_parts = [p.text for p in result.content.parts if p.text]
        assert any("[Protocol Error]" in t for t in text_parts)
        assert any("report_generator" in t for t in text_parts)

    def test_mixed_valid_and_invalid_tools(self):
        """Valid tools are kept, invalid tools are rejected."""
        session_state = {_SESSION_STATE_KEY: 3}
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        # publish_sources is valid at step 3, report_generator is not
        llm_resp = _make_llm_response(
            ("publish_sources", {"sources": []}),
            ("report_generator", {"mode": "full_synthesis"}),
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is not None
        # Should keep publish_sources and reject report_generator
        fc_parts = [p for p in result.content.parts if p.function_call]
        text_parts = [p for p in result.content.parts if p.text]
        assert len(fc_parts) == 1
        assert fc_parts[0].function_call.name == "publish_sources"
        assert any("report_generator" in p.text for p in text_parts)

    def test_step_advances_and_persists(self):
        """Step should be persisted in session state after advancement."""
        session_state = {_SESSION_STATE_KEY: 0}
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        # seed_session is valid at step 0, and advances step to 1
        llm_resp = _make_llm_response(
            ("memory_plane", {"operation": "seed_session", "query": "test"})
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None
        assert session_state[_SESSION_STATE_KEY] == 1

    def test_default_step_is_0(self):
        """When session state has no step key, default to 0."""
        session_state = {}  # No _protocol_current_step key
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(("memory_plane", {"operation": "seed_session"}))

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None
        # Step 0 is the default, seed_session is valid at step 0

    def test_verify_revise_at_step_5(self):
        """Both VerifierAgent and ReviserAgent should be allowed at step 5."""
        session_state = {_SESSION_STATE_KEY: 5}
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(
            ("peer_VerifierAgent", {"task": "verify report"}),
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None

    def test_flush_cold_at_step_6(self):
        """memory_plane flush_cold should work at step 6."""
        session_state = {_SESSION_STATE_KEY: 6}
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(
            ("memory_plane", {"operation": "flush_cold", "query": "test"}),
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None

    def test_returns_none_when_no_content(self):
        """Handle edge case of empty content."""
        ctx, _ = _make_callback_context()
        comp = _make_host_component()
        llm_resp = LlmResponse(content=None, partial=False)

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None

    def test_returns_none_when_no_parts(self):
        """Handle edge case of empty parts list."""
        ctx, _ = _make_callback_context()
        comp = _make_host_component()
        llm_resp = LlmResponse(
            content=adk_types.Content(role="model", parts=[]),
            partial=False,
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None


# ── Dynamic peer filtering tests (B2) ──────────────────────────────


class TestDynamicPeerFiltering:
    """Test _is_tool_allowed with selected_agents parameter (B2 enhancement)."""

    def test_no_selected_agents_allows_all_peers_at_step_2(self):
        """When selected_agents is None, all peer_* tools pass (graceful fallback)."""
        assert _is_tool_allowed("peer_LiteratureSpecialist", 2, None) is True
        assert _is_tool_allowed("peer_GenomicsSpecialist", 2, None) is True

    def test_empty_selected_agents_allows_all_peers(self):
        """Empty list also falls back to allowing all."""
        assert _is_tool_allowed("peer_DrugSpecialist", 2, []) is True

    def test_selected_agents_allows_listed_peer(self):
        """Peer tool in selected_agents list is allowed."""
        selected = ["LiteratureSpecialist", "DrugSpecialist"]
        assert _is_tool_allowed("peer_LiteratureSpecialist", 2, selected) is True
        assert _is_tool_allowed("peer_DrugSpecialist", 2, selected) is True

    def test_selected_agents_rejects_unlisted_peer(self):
        """Peer tool NOT in selected_agents list is rejected."""
        selected = ["LiteratureSpecialist", "DrugSpecialist"]
        assert _is_tool_allowed("peer_GenomicsSpecialist", 2, selected) is False
        assert _is_tool_allowed("peer_EnvironmentalSpecialist", 2, selected) is False

    def test_selected_agents_applies_at_step_3_collect(self):
        """Dynamic filtering also applies at COLLECT step (follow-up delegation)."""
        selected = ["LiteratureSpecialist", "ClinicalTrialsSpecialist"]
        assert _is_tool_allowed("peer_LiteratureSpecialist", 3, selected) is True
        assert _is_tool_allowed("peer_EpidemiologySpecialist", 3, selected) is False

    def test_selected_agents_does_not_affect_step_5(self):
        """Step 5 uses explicit tool names, not peer_* glob — unaffected."""
        selected = ["LiteratureSpecialist"]
        # VerifierAgent is allowed at step 5 by explicit name, not glob
        assert _is_tool_allowed("peer_VerifierAgent", 5, selected) is True
        assert _is_tool_allowed("peer_ReviserAgent", 5, selected) is True

    def test_selected_agents_does_not_affect_non_peer_tools(self):
        """Non-peer tools are unaffected by selected_agents."""
        selected = ["LiteratureSpecialist"]
        assert _is_tool_allowed("memory_plane", 3, selected) is True
        assert _is_tool_allowed("publish_sources", 3, selected) is True
        assert _is_tool_allowed("completeness_checker", 3, selected) is True

    def test_pro_suffix_agents_in_selected_list(self):
        """Pro-variant agent names work with dynamic filtering."""
        selected = ["LiteratureSpecialistPro", "DrugSpecialistPro"]
        assert _is_tool_allowed("peer_LiteratureSpecialistPro", 2, selected) is True
        assert _is_tool_allowed("peer_GenomicsSpecialistPro", 2, selected) is False

    def test_opus_suffix_agents_in_selected_list(self):
        """Opus-variant agent names work with dynamic filtering."""
        selected = ["LiteratureSpecialistOpus", "DrugSpecialistOpus"]
        assert _is_tool_allowed("peer_LiteratureSpecialistOpus", 2, selected) is True
        assert _is_tool_allowed("peer_GenomicsSpecialistOpus", 2, selected) is False


class TestDynamicPeerFilteringCallback:
    """Test the full callback with _selected_agents in session state."""

    def test_callback_filters_unlisted_peer_at_step_2(self):
        """Callback rejects peer tools not in _selected_agents at DELEGATE step."""
        session_state = {
            _SESSION_STATE_KEY: 2,
            _SELECTED_AGENTS_KEY: ["LiteratureSpecialist", "DrugSpecialist"],
        }
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(
            ("peer_LiteratureSpecialist", {"task": "search"}),
            ("peer_GenomicsSpecialist", {"task": "variants"}),
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is not None
        # LiteratureSpecialist should be kept, GenomicsSpecialist rejected
        fc_parts = [p for p in result.content.parts if p.function_call]
        text_parts = [p for p in result.content.parts if p.text]
        assert len(fc_parts) == 1
        assert fc_parts[0].function_call.name == "peer_LiteratureSpecialist"
        assert any("peer_GenomicsSpecialist" in p.text for p in text_parts)

    def test_callback_allows_all_listed_peers(self):
        """All peers in _selected_agents pass through at DELEGATE step."""
        session_state = {
            _SESSION_STATE_KEY: 2,
            _SELECTED_AGENTS_KEY: [
                "LiteratureSpecialist",
                "DrugSpecialist",
                "ClinicalTrialsSpecialist",
            ],
        }
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(
            ("peer_LiteratureSpecialist", {"task": "search"}),
            ("peer_DrugSpecialist", {"task": "lookup"}),
            ("peer_ClinicalTrialsSpecialist", {"task": "trials"}),
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None  # All valid — pass through

    def test_callback_falls_back_when_no_selected_agents(self):
        """Without _selected_agents, all peer tools pass at DELEGATE."""
        session_state = {_SESSION_STATE_KEY: 2}
        ctx, _ = _make_callback_context(session_state)
        comp = _make_host_component()
        llm_resp = _make_llm_response(
            ("peer_LiteratureSpecialist", {"task": "search"}),
            ("peer_GenomicsSpecialist", {"task": "variants"}),
            ("peer_EnvironmentalSpecialist", {"task": "env"}),
        )

        result = protocol_step_validator_callback(ctx, llm_resp, comp)
        assert result is None  # All valid — fallback allows all peer_*
