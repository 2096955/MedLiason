"""Tests for C1 Tool Description Enrichment.

Asserts that enriched tool descriptions contain the required keywords
documenting operations, prerequisites, thresholds, and valid inputs.
"""

import pytest

from lifesci_tools.memory_plane import MemoryPlaneTool
from lifesci_tools.claim_verifier import ClaimVerifierTool
from lifesci_tools.triage_specialist_panel import TriageSpecialistPanelTool
from lifesci_tools.source_collector import SourceCollectorTool
from lifesci_tools.completeness_checker import CompletenessCheckerTool


class TestMemoryPlaneDescription:
    """memory_plane description documents all operations, namespaces, and workflow ordering."""

    def test_readwrite_contains_all_operations(self):
        tool = MemoryPlaneTool(tool_config={})
        desc = tool.tool_description
        assert "seed_session" in desc
        assert "flush_cold" in desc
        assert "retrieve" in desc
        assert "store" in desc
        assert "append" in desc
        assert "query_cold" in desc
        assert "get_strategy" in desc

    def test_readwrite_contains_namespaces(self):
        tool = MemoryPlaneTool(tool_config={})
        desc = tool.tool_description
        assert "citations" in desc
        assert "evidence" in desc
        assert "intermediate" in desc
        assert "learning" in desc
        assert "verification" in desc

    def test_readwrite_contains_workflow_ordering(self):
        tool = MemoryPlaneTool(tool_config={})
        desc = tool.tool_description
        assert "STEP 0" in desc
        assert "STEP 6" in desc

    def test_readonly_contains_read_operations(self):
        tool = MemoryPlaneTool(tool_config={"read_only": True})
        desc = tool.tool_description
        assert "retrieve" in desc
        assert "list_keys" in desc
        assert "query_cold" in desc
        assert "get_strategy" in desc

    def test_readonly_contains_namespaces(self):
        tool = MemoryPlaneTool(tool_config={"read_only": True})
        desc = tool.tool_description
        assert "citations" in desc
        assert "evidence" in desc
        assert "intermediate" in desc
        assert "learning" in desc
        assert "verification" in desc

    def test_readonly_labels_operations_as_readonly(self):
        tool = MemoryPlaneTool(tool_config={"read_only": True})
        desc = tool.tool_description
        assert "Read-only" in desc or "READ-ONLY" in desc


class TestClaimVerifierDescription:
    """claim_verifier description documents prerequisites and thresholds."""

    def test_contains_prerequisite(self):
        tool = ClaimVerifierTool()
        desc = tool.tool_description
        assert "publish_sources" in desc or "PREREQUISITE" in desc

    def test_contains_citation_map_retrieval(self):
        tool = ClaimVerifierTool()
        desc = tool.tool_description
        assert "citation_map_json" in desc

    def test_contains_confidence_threshold(self):
        tool = ClaimVerifierTool()
        desc = tool.tool_description
        assert "0.5" in desc


class TestTriageSpecialistPanelDescription:
    """triage_specialist_panel description lists valid specialists and scales."""

    def test_contains_valid_specialists(self):
        tool = TriageSpecialistPanelTool()
        desc = tool.tool_description
        assert "emergency_physician" in desc
        assert "family_physician" in desc
        assert "internist" in desc
        assert "paediatrician" in desc
        assert "pharmacist" in desc

    def test_contains_confidence_scale(self):
        tool = TriageSpecialistPanelTool()
        desc = tool.tool_description
        assert "0-100" in desc

    def test_contains_tier_explanation(self):
        tool = TriageSpecialistPanelTool()
        desc = tool.tool_description
        assert "tier1" in desc or "tier1_specialists" in desc
        assert "tier2" in desc


class TestSourceCollectorDescription:
    """source_collector description documents citation_map_json output."""

    def test_contains_citation_map_json(self):
        tool = SourceCollectorTool()
        desc = tool.tool_description
        assert "citation_map_json" in desc

    def test_contains_memory_plane_storage_instruction(self):
        tool = SourceCollectorTool()
        desc = tool.tool_description
        assert "memory_plane" in desc


class TestCompletenessCheckerDescription:
    """completeness_checker description documents proceed thresholds."""

    def test_contains_structural_coverage_threshold(self):
        tool = CompletenessCheckerTool()
        desc = tool.tool_description
        assert "0.70" in desc or "structural_coverage" in desc

    def test_contains_quality_coverage_threshold(self):
        tool = CompletenessCheckerTool()
        desc = tool.tool_description
        assert "0.40" in desc or "quality_coverage" in desc

    def test_contains_proceed_flag(self):
        tool = CompletenessCheckerTool()
        desc = tool.tool_description
        assert "proceed" in desc
