"""Wiring tests for TriageSpecialistPanelTool — JSON extraction integration."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_specialist_panel_retries_on_timeout(tool, mock_ctx):
    """TimeoutError on first attempt triggers one retry."""
    good_response = _mock_llm_response(
        '{"diagnosis": "ME/CFS", "confidence": 75, "thinking": "Classic PEM"}'
    )
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.TimeoutError("simulated timeout")
        return good_response

    with (
        patch("litellm.completion", side_effect=side_effect),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "fatigue"}',
            },
            mock_ctx,
        )

    assert result["verdicts"][0]["diagnosis"] == "ME/CFS"
    assert call_count == 2  # 1 failure + 1 retry


@pytest.mark.asyncio
async def test_specialist_panel_retries_on_transient_error(tool, mock_ctx):
    """Transient litellm errors (503-like) trigger one retry."""
    good_response = _mock_llm_response(
        '{"diagnosis": "Hypothyroidism", "confidence": 60, "thinking": "TSH"}'
    )
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("503 Service Unavailable")
        return good_response

    with (
        patch("litellm.completion", side_effect=side_effect),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "fatigue"}',
            },
            mock_ctx,
        )

    assert result["verdicts"][0]["diagnosis"] == "Hypothyroidism"
    assert call_count == 2


@pytest.mark.asyncio
async def test_specialist_panel_diagnostic_thinking_on_failure(tool, mock_ctx):
    """When all retries fail, the verdict thinking field contains the exception info."""
    with (
        patch(
            "litellm.completion", side_effect=asyncio.TimeoutError("vertex cold start")
        ),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "fatigue"}',
            },
            mock_ctx,
        )

    verdict = result["verdicts"][0]
    assert verdict["confidence"] == 0
    assert "TimeoutError" in verdict["thinking"]
    assert "vertex cold start" in verdict["thinking"]


@pytest.mark.asyncio
async def test_specialist_panel_no_retry_on_non_transient_error(tool, mock_ctx):
    """Non-transient errors (e.g. ValueError) do not trigger retry."""
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise ValueError("bad model name")

    with patch("litellm.completion", side_effect=side_effect):
        result = await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "fatigue"}',
            },
            mock_ctx,
        )

    assert result["verdicts"][0]["confidence"] == 0
    # Should NOT retry on ValueError — only 1 call
    assert call_count == 1


@pytest.mark.asyncio
async def test_specialist_panel_forwards_num_retries(tool, mock_ctx):
    """num_retries from model config dict is forwarded to litellm.completion."""
    tool.tool_config["model"] = {"model": "test-model", "num_retries": 4}
    good = _mock_llm_response(
        '{"diagnosis": "Anemia", "confidence": 50, "thinking": "low Hb"}'
    )

    with patch("litellm.completion", return_value=good) as mock_comp:
        await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "fatigue"}',
            },
            mock_ctx,
        )

    _, kwargs = mock_comp.call_args
    assert kwargs.get("num_retries") == 4
