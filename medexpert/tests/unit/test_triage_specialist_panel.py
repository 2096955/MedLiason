"""Wiring tests for TriageSpecialistPanelTool — JSON extraction integration."""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub litellm before importing the tool (avoids slow real import)
_litellm_stub = MagicMock()
if "litellm" not in sys.modules:
    sys.modules["litellm"] = _litellm_stub

from lifesci_tools.triage_specialist_panel import (  # noqa: E402
    TriageSpecialistPanelTool,
    _clear_prompt_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset prompt cache between tests."""
    _clear_prompt_cache()
    yield
    _clear_prompt_cache()


@pytest.fixture
def tool(tmp_path):
    """Create tool with a minimal family_physician prompt on disk."""
    prompts_dir = tmp_path / "specialists"
    prompts_dir.mkdir()
    (prompts_dir / "family_physician.md").write_text("You are a family physician.")
    # Shared instructions (loaded from parent dir)
    (tmp_path / "shared_output_instructions.md").write_text("Return valid JSON only.")

    t = TriageSpecialistPanelTool()
    t.tool_config = {
        "model": "test-model",
        "temperature": 0.1,
        "timeout": 5,
        "prompts_dir": str(prompts_dir),
    }
    return t


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.state = {}
    return ctx


def _mock_llm_response(content: str) -> MagicMock:
    """Build a mock litellm.completion response."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.mark.asyncio
async def test_specialist_panel_parses_markdown_fenced_response(tool, mock_ctx):
    """Fenced JSON in LLM response is correctly parsed into a verdict."""
    fenced = '```json\n{"diagnosis": "Common Cold", "confidence": 70, "thinking": "Mild URI"}\n```'

    with patch("litellm.completion", return_value=_mock_llm_response(fenced)):
        result = await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "runny nose"}',
            },
            mock_ctx,
        )

    assert result["total_consulted"] == 1
    verdict = result["verdicts"][0]
    assert verdict["diagnosis"] == "Common Cold"
    assert verdict["confidence"] == 70
    assert verdict["specialist"] == "family_physician"


@pytest.mark.asyncio
async def test_specialist_panel_handles_unparseable_response(tool, mock_ctx):
    """Garbage LLM output results in 'Insufficient information', no exception."""
    garbage = "I'm sorry, I cannot provide a diagnosis in JSON format right now."

    with patch("litellm.completion", return_value=_mock_llm_response(garbage)):
        result = await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "runny nose"}',
            },
            mock_ctx,
        )

    assert result["total_consulted"] == 1
    verdict = result["verdicts"][0]
    assert verdict["diagnosis"] == "Insufficient information"
    assert verdict["confidence"] == 0
