"""Tests for specialist response validator (Phase 2B)."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from lifesci_tools.specialist_response_validator import (
    validate_specialist_response,
    specialist_response_validator_callback,
)


# ---------------------------------------------------------------------------
# Tests: validate_specialist_response (pure function)
# ---------------------------------------------------------------------------

class TestValidateSpecialistResponse:
    """Tests for the validation logic."""

    def test_valid_response(self):
        response = {
            "execution_status": "success",
            "findings": ["Finding 1"],
            "sources": [{"pmid": "12345"}],
        }
        warnings = validate_specialist_response("peer_LiteratureSpecialist", response)
        assert warnings == []

    def test_ignores_non_peer_tools(self):
        warnings = validate_specialist_response("query_decomposer", {"bad": True})
        assert warnings == []

    def test_missing_required_fields(self):
        response = {"confidence": 0.8}
        warnings = validate_specialist_response("peer_DrugSpecialist", response)
        assert any("missing required fields" in w for w in warnings)
        assert any("execution_status" in w for w in warnings)

    def test_invalid_execution_status(self):
        response = {
            "execution_status": "unknown",
            "findings": [],
            "sources": [],
        }
        warnings = validate_specialist_response("peer_DrugSpecialist", response)
        assert any("invalid execution_status" in w for w in warnings)

    def test_oversized_response(self):
        response = {
            "execution_status": "success",
            "findings": ["x" * 5000],
            "sources": [{"pmid": "12345"}],
        }
        warnings = validate_specialist_response("peer_LiteratureSpecialist", response)
        assert any("exceeds" in w for w in warnings)

    def test_success_with_empty_sources(self):
        response = {
            "execution_status": "success",
            "findings": ["Something found"],
            "sources": [],
        }
        warnings = validate_specialist_response("peer_GenomicsSpecialist", response)
        assert any("sources array is empty" in w for w in warnings)

    def test_failed_with_empty_sources_ok(self):
        """Failed status with empty sources should NOT warn about sources."""
        response = {
            "execution_status": "failed",
            "findings": [],
            "sources": [],
        }
        warnings = validate_specialist_response("peer_GenomicsSpecialist", response)
        assert not any("sources array is empty" in w for w in warnings)

    def test_partial_status_valid(self):
        response = {
            "execution_status": "partial",
            "findings": ["Some data"],
            "sources": [{"pmid": "11111"}],
        }
        warnings = validate_specialist_response("peer_EpidemiologySpecialist", response)
        assert warnings == []

    def test_string_json_response(self):
        response = json.dumps({
            "execution_status": "success",
            "findings": ["f1"],
            "sources": [{"pmid": "1"}],
        })
        warnings = validate_specialist_response("peer_DrugSpecialist", response)
        assert warnings == []

    def test_invalid_json_string(self):
        warnings = validate_specialist_response("peer_DrugSpecialist", "not json at all")
        assert any("not valid JSON" in w for w in warnings)

    def test_none_response(self):
        warnings = validate_specialist_response("peer_DrugSpecialist", None)
        assert any("not valid JSON" in w for w in warnings)

    def test_ignores_verifier_tool(self):
        """Verifier has a different response schema — should be excluded."""
        response = {"verification_status": "completed", "verdict": "PASS"}
        warnings = validate_specialist_response("peer_VerifierAgent", response)
        assert warnings == []

    def test_ignores_verifier_pro_variant(self):
        response = {"verification_status": "completed"}
        warnings = validate_specialist_response("peer_VerifierAgentPro", response)
        assert warnings == []

    def test_ignores_reviser_tool(self):
        response = {"revised_text": "..."}
        warnings = validate_specialist_response("peer_ReviserAgent", response)
        assert warnings == []

    def test_ignores_reviser_opus_variant(self):
        response = {"revised_text": "..."}
        warnings = validate_specialist_response("peer_ReviserAgentOpus", response)
        assert warnings == []


# ---------------------------------------------------------------------------
# Tests: specialist_response_validator_callback (async callback)
# ---------------------------------------------------------------------------

class TestSpecialistResponseValidatorCallback:
    """Tests for the after_tool callback."""

    @pytest.mark.asyncio
    async def test_returns_response_unchanged_on_valid(self):
        tool = MagicMock()
        tool.name = "peer_LiteratureSpecialist"
        response = {
            "execution_status": "success",
            "findings": ["f1"],
            "sources": [{"pmid": "1"}],
        }
        ctx = MagicMock()
        ctx.state = {}

        result = await specialist_response_validator_callback(
            tool, {}, ctx, response
        )
        assert result == response

    @pytest.mark.asyncio
    async def test_returns_response_unchanged_on_invalid(self):
        """Invalid response should still be returned (graceful degradation)."""
        tool = MagicMock()
        tool.name = "peer_DrugSpecialist"
        response = {"bad_field": True}
        ctx = MagicMock()
        ctx.state = {}

        result = await specialist_response_validator_callback(
            tool, {}, ctx, response
        )
        assert result == response  # NOT rejected

    @pytest.mark.asyncio
    async def test_attaches_warnings_to_state(self):
        tool = MagicMock()
        tool.name = "peer_DrugSpecialist"
        response = {"bad_field": True}
        ctx = MagicMock()
        ctx.state = {}

        await specialist_response_validator_callback(tool, {}, ctx, response)
        assert "_validation_warnings" in ctx.state
        assert len(ctx.state["_validation_warnings"]) > 0

    @pytest.mark.asyncio
    async def test_skips_non_peer_tools(self):
        tool = MagicMock()
        tool.name = "query_decomposer"
        response = {"anything": True}
        ctx = MagicMock()
        ctx.state = {}

        result = await specialist_response_validator_callback(
            tool, {}, ctx, response
        )
        assert result == response
        assert "_validation_warnings" not in ctx.state

    @pytest.mark.asyncio
    async def test_accumulates_warnings_across_calls(self):
        ctx = MagicMock()
        ctx.state = {}

        for name in ["peer_DrugSpecialist", "peer_LiteratureSpecialist"]:
            tool = MagicMock()
            tool.name = name
            await specialist_response_validator_callback(
                tool, {}, ctx, {"bad": True}
            )

        assert len(ctx.state["_validation_warnings"]) >= 2
