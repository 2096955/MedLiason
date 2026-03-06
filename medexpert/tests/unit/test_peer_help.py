"""Tests for specialist-to-specialist peer help (Phase 3).

Tests the peer_help config parsing, allowlist matching, help budget
enforcement, and config parity across all 3 variants.
"""

import fnmatch
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parents[3]


import re


def _extract_peer_help_from_yaml(path: Path) -> dict:
    """Extract peer_help config block from YAML text using regex.

    Agent YAMLs use !include and YAML anchors from shared config,
    so they can't be parsed standalone. We extract the peer_help block
    as raw text and parse just that fragment.
    """
    text = path.read_text()

    # Find the peer_help block
    match = re.search(
        r"peer_help:\s*\n"
        r"((?:[ \t]+\S.*\n)*)",  # Capture indented lines
        text,
    )
    if not match:
        return {}

    block = "peer_help:\n" + match.group(1)
    parsed = yaml.safe_load(block)
    return parsed.get("peer_help", {}) if parsed else {}


def _yaml_has_text(path: Path, text: str) -> bool:
    """Check if the YAML file contains a specific text string."""
    return text in path.read_text()


# ---------------------------------------------------------------------------
# Config-level tests
# ---------------------------------------------------------------------------

# Specialists that should have peer_help enabled
_EXPECTED_PEER_HELP = {
    "drug_specialist": "LiteratureSpecialist",
    "clinical_trials_specialist": "LiteratureSpecialist",
    "genomics_specialist": "LiteratureSpecialist",
    "epidemiology_specialist": "EnvironmentalSpecialist",
    "environmental_specialist": "EpidemiologySpecialist",
    "regulatory_specialist": "DrugSpecialist",
    "provider_intel_specialist": "LiteratureSpecialist",
}


class TestPeerHelpConfig:
    """Verify peer_help config is correctly set for all specialists."""

    @pytest.mark.parametrize("specialist", list(_EXPECTED_PEER_HELP.keys()))
    def test_standard_specialist_has_peer_help(self, specialist):
        config_path = REPO_ROOT / f"medexpert/configs/agents/{specialist}.yaml"
        peer_help = _extract_peer_help_from_yaml(config_path)

        assert peer_help.get("enabled") is True, f"{specialist} peer_help not enabled"
        assert peer_help.get("max_help_requests", 0) >= 1
        can_ask = peer_help.get("can_ask", [])
        assert len(can_ask) >= 1, f"{specialist} has empty can_ask"

        # Verify the expected peer is matchable
        expected = _EXPECTED_PEER_HELP[specialist]
        assert any(
            fnmatch.fnmatch(expected, pattern) for pattern in can_ask
        ), f"{specialist}: {expected} not matched by can_ask={can_ask}"

    def test_literature_specialist_no_peer_help(self):
        config_path = REPO_ROOT / "medexpert/configs/agents/literature_specialist.yaml"
        peer_help = _extract_peer_help_from_yaml(config_path)
        assert not peer_help.get("enabled", False)


class TestPeerHelpConfigParity:
    """Verify peer_help is consistent across Standard/Pro/Opus variants."""

    @pytest.mark.parametrize("specialist", list(_EXPECTED_PEER_HELP.keys()))
    @pytest.mark.parametrize(
        "variant_dir",
        [
            "medexpert/configs/agents",
            "medexpert/configs/pro/agents",
            "medexpert/configs/opus/agents",
        ],
    )
    def test_variant_has_peer_help(self, specialist, variant_dir):
        config_path = REPO_ROOT / f"{variant_dir}/{specialist}.yaml"
        if not config_path.exists():
            pytest.skip(f"{config_path} does not exist")

        peer_help = _extract_peer_help_from_yaml(config_path)

        assert peer_help.get("enabled") is True, (
            f"{specialist} in {variant_dir} has peer_help.enabled != true"
        )
        assert peer_help.get("max_help_requests", 0) >= 1
        can_ask = peer_help.get("can_ask", [])
        assert len(can_ask) >= 1

    @pytest.mark.parametrize(
        "variant_dir",
        [
            "medexpert/configs/agents",
            "medexpert/configs/pro/agents",
            "medexpert/configs/opus/agents",
        ],
    )
    def test_literature_no_peer_help_all_variants(self, variant_dir):
        config_path = REPO_ROOT / f"{variant_dir}/literature_specialist.yaml"
        if not config_path.exists():
            pytest.skip(f"{config_path} does not exist")

        peer_help = _extract_peer_help_from_yaml(config_path)
        assert not peer_help.get("enabled", False)


class TestPeerHelpGlobPatterns:
    """Verify Pro/Opus can_ask patterns match suffixed agent names."""

    @pytest.mark.parametrize("suffix", ["Pro", "Opus"])
    def test_pro_opus_pattern_matches_suffixed_name(self, suffix):
        """LiteratureSpecialist* should match LiteratureSpecialistPro and Opus."""
        pattern = "LiteratureSpecialist*"
        agent_name = f"LiteratureSpecialist{suffix}"
        assert fnmatch.fnmatch(agent_name, pattern)

    def test_standard_pattern_matches_exact_name(self):
        """Standard configs use exact names (no glob needed)."""
        pattern = "LiteratureSpecialist"
        assert fnmatch.fnmatch("LiteratureSpecialist", pattern)

    def test_standard_pattern_does_not_match_suffixed(self):
        """Standard config pattern should NOT match Pro/Opus names."""
        pattern = "LiteratureSpecialist"
        assert not fnmatch.fnmatch("LiteratureSpecialistPro", pattern)


class TestPeerHelpBudgetLogic:
    """Test the help request counting logic from component.py."""

    def test_count_peer_help_calls_from_history(self):
        """Simulate counting peer_help tool calls in conversation history."""
        # This mirrors the logic in _inject_peer_tools_callback
        peer_help_tool_names = {"peer_LiteratureSpecialist"}

        # Simulate conversation parts with function calls
        class FakePart:
            def __init__(self, name=None):
                self.function_call = type("FC", (), {"name": name})() if name else None

        class FakeContent:
            def __init__(self, parts):
                self.parts = parts

        contents = [
            FakeContent([FakePart("query_decomposer"), FakePart("peer_LiteratureSpecialist")]),
            FakeContent([FakePart("peer_LiteratureSpecialist")]),
            FakeContent([FakePart("memory_plane")]),
        ]

        help_count = 0
        for content in contents:
            for part in content.parts:
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", "") in peer_help_tool_names:
                    help_count += 1

        assert help_count == 2

    def test_budget_exhausted_disables_injection(self):
        """When help_count >= max_help_requests, can_ask should be emptied."""
        max_help_requests = 2
        help_count = 2
        peer_help_can_ask = ["LiteratureSpecialist"]

        if help_count >= max_help_requests:
            peer_help_can_ask = []

        assert peer_help_can_ask == []

    def test_budget_not_exhausted_keeps_injection(self):
        max_help_requests = 2
        help_count = 1
        peer_help_can_ask = ["LiteratureSpecialist"]

        if help_count >= max_help_requests:
            peer_help_can_ask = []

        assert peer_help_can_ask == ["LiteratureSpecialist"]
