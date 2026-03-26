"""Test that all orchestrator config variants have matching critical settings.

The orchestrator YAML files use !include directives and YAML anchors from
shared_config*.yaml. Since we only need settings defined in the orchestrator
file itself (not inherited from shared config), we pre-process the raw YAML
to strip !include lines and neutralise anchor references before parsing.
"""

import re

import pytest
import yaml
from pathlib import Path

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"

ORCHESTRATOR_CONFIGS = [
    CONFIGS_DIR / "agents" / "orchestrator.yaml",
    CONFIGS_DIR / "pro" / "agents" / "orchestrator.yaml",
    CONFIGS_DIR / "opus" / "agents" / "orchestrator.yaml",
]

# Settings that MUST match across all variants
PARITY_SETTINGS = [
    "max_llm_calls_per_task",
    "protocol_enforcement",
]


def _load_app_config(yaml_path: Path) -> dict:
    """Load the app_config from an orchestrator YAML.

    Handles !include directives and YAML anchor references by stripping/
    replacing them before parsing, since we only need settings defined
    directly in the orchestrator file.
    """
    text = yaml_path.read_text(encoding="utf-8")

    # Remove standalone !include lines (they reference shared_config*.yaml)
    text = re.sub(r"^!include\s+.*$", "", text, flags=re.MULTILINE)

    # Replace YAML anchor references (*name) with placeholder strings
    # so yaml.safe_load doesn't fail on undefined anchors.
    # Must not replace * inside quoted strings or comments, but the
    # pattern *word only appears as anchor refs in these files.
    text = re.sub(r"\*(\w+)", r'"__anchor_\1__"', text)

    # Replace merge keys (<<:) with a regular mapping key
    text = text.replace("<<:", "_merge_key:")

    data = yaml.safe_load(text)

    apps = data.get("apps", [])
    if not apps:
        pytest.skip(f"No apps in {yaml_path}")
    return apps[0].get("app_config", {})


class TestConfigParity:
    """All 3 orchestrator variants must agree on critical settings."""

    @pytest.mark.parametrize("setting", PARITY_SETTINGS)
    def test_setting_matches_across_variants(self, setting):
        """All 3 orchestrator variants must have the same value for critical settings."""
        values = {}
        for config_path in ORCHESTRATOR_CONFIGS:
            if not config_path.exists():
                pytest.skip(f"{config_path} not found")
            app_config = _load_app_config(config_path)
            values[config_path.name] = app_config.get(setting)

        unique_values = set(v for v in values.values() if v is not None)
        assert len(unique_values) <= 1, (
            f"Config parity violation for '{setting}': {values}"
        )

    def test_max_llm_calls_is_50(self):
        """All orchestrator variants must have max_llm_calls_per_task: 50."""
        for config_path in ORCHESTRATOR_CONFIGS:
            if not config_path.exists():
                continue
            app_config = _load_app_config(config_path)
            val = app_config.get("max_llm_calls_per_task")
            assert val is not None, (
                f"{config_path.name}: max_llm_calls_per_task not set"
            )
            assert val == 50, (
                f"{config_path.name}: max_llm_calls_per_task is {val}, expected 50"
            )

    def test_protocol_enforcement_enabled(self):
        """All orchestrator variants must have protocol_enforcement: true."""
        for config_path in ORCHESTRATOR_CONFIGS:
            if not config_path.exists():
                continue
            app_config = _load_app_config(config_path)
            val = app_config.get("protocol_enforcement")
            assert val is True, (
                f"{config_path.name}: protocol_enforcement is {val!r}, expected True"
            )

    def test_prompt_budget_matches_config(self):
        """The CALL BUDGET number in the instruction prompt must match max_llm_calls_per_task."""
        for config_path in ORCHESTRATOR_CONFIGS:
            if not config_path.exists():
                continue
            app_config = _load_app_config(config_path)
            max_calls = app_config.get("max_llm_calls_per_task")
            instruction = app_config.get("instruction", "")

            match = re.search(r"CALL BUDGET:\s*You have (\d+) LLM calls", instruction)
            assert match, (
                f"{config_path.name}: CALL BUDGET line not found in instruction"
            )
            prompt_budget = int(match.group(1))
            assert prompt_budget == max_calls, (
                f"{config_path.name}: prompt says {prompt_budget} LLM calls "
                f"but max_llm_calls_per_task is {max_calls}"
            )
