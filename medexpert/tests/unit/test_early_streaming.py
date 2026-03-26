"""Tests that orchestrator STEP 4 instructs the LLM to emit the answer before verification.

The orchestrator YAML files use !include directives and YAML anchors from
shared_config*.yaml. We pre-process the raw YAML to strip !include lines and
neutralise anchor references before parsing, since we only need the instruction
text defined directly in the orchestrator file.
"""

import re
from pathlib import Path

import pytest
import yaml

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"

ORCHESTRATOR_CONFIGS = {
    "standard": CONFIGS_DIR / "agents" / "orchestrator.yaml",
    "pro": CONFIGS_DIR / "pro" / "agents" / "orchestrator.yaml",
    "opus": CONFIGS_DIR / "opus" / "agents" / "orchestrator.yaml",
}

EARLY_ANSWER_PHRASES = [
    "respond to the user now",
    "present your answer to the user",
    "emit your answer",
    "stream the answer",
]


def _load_instruction(yaml_path: Path) -> str:
    """Load the instruction text from an orchestrator YAML.

    Handles !include directives and YAML anchor references by stripping/
    replacing them before parsing.
    """
    text = yaml_path.read_text(encoding="utf-8")

    # Remove standalone !include lines
    text = re.sub(r"^!include\s+.*$", "", text, flags=re.MULTILINE)

    # Replace YAML anchor references (*name) with placeholder strings.
    # Use negative lookbehind to avoid matching **bold** markdown patterns.
    text = re.sub(r"(?<!\*)\*(\w+)", r'"__anchor_\1__"', text)

    # Replace merge keys (<<:) with a regular mapping key
    text = text.replace("<<:", "_merge_key:")

    data = yaml.safe_load(text)

    apps = data.get("apps", [])
    if not apps:
        pytest.skip(f"No apps in {yaml_path}")
    return apps[0].get("app_config", {}).get("instruction", "")


class TestEarlyStreamingPrompt:
    """Verify all 3 orchestrator variants instruct early answer emission at STEP 4."""

    def test_orchestrator_prompt_has_early_answer_instruction(self):
        yaml_path = ORCHESTRATOR_CONFIGS["standard"]
        if not yaml_path.exists():
            pytest.skip("Standard orchestrator config not found")
        prompt = _load_instruction(yaml_path)
        step4_idx = prompt.find("STEP 4")
        step5_idx = prompt.find("STEP 5")
        assert step4_idx > 0, "STEP 4 not found in orchestrator prompt"
        assert step5_idx > step4_idx, "STEP 5 must come after STEP 4"
        step4_text = prompt[step4_idx:step5_idx].lower()
        assert any(
            phrase in step4_text for phrase in EARLY_ANSWER_PHRASES
        ), "STEP 4 must instruct emitting the answer before verification"

    def test_pro_orchestrator_has_early_answer_instruction(self):
        yaml_path = ORCHESTRATOR_CONFIGS["pro"]
        if not yaml_path.exists():
            pytest.skip("Pro orchestrator config not found")
        prompt = _load_instruction(yaml_path)
        step4_idx = prompt.find("STEP 4")
        step5_idx = prompt.find("STEP 5")
        assert step4_idx > 0, "STEP 4 not found in Pro orchestrator prompt"
        assert step5_idx > step4_idx, "STEP 5 must come after STEP 4"
        step4_text = prompt[step4_idx:step5_idx].lower()
        assert any(
            phrase in step4_text for phrase in EARLY_ANSWER_PHRASES
        ), "Pro STEP 4 must instruct emitting the answer before verification"

    def test_opus_orchestrator_has_early_answer_instruction(self):
        yaml_path = ORCHESTRATOR_CONFIGS["opus"]
        if not yaml_path.exists():
            pytest.skip("Opus orchestrator config not found")
        prompt = _load_instruction(yaml_path)
        step4_idx = prompt.find("STEP 4")
        step5_idx = prompt.find("STEP 5")
        assert step4_idx > 0, "STEP 4 not found in Opus orchestrator prompt"
        assert step5_idx > step4_idx, "STEP 5 must come after STEP 4"
        step4_text = prompt[step4_idx:step5_idx].lower()
        assert any(
            phrase in step4_text for phrase in EARLY_ANSWER_PHRASES
        ), "Opus STEP 4 must instruct emitting the answer before verification"
