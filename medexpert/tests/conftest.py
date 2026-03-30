"""Shared test fixtures for MedExpert test suite."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def unwrap_mcp_tool(tool_or_fn):
    """Return the underlying async callable from an MCP tool.

    FastMCP 3.x decorators return the original function unchanged, so this
    is now essentially a no-op.  Kept for backward compatibility with any
    test that still calls it.
    """
    # FastMCP 2.x wrapped in FunctionTool with .fn; 3.x returns raw function
    if hasattr(tool_or_fn, "fn"):
        return tool_or_fn.fn
    return tool_or_fn


@pytest.fixture
def mock_tool_context():
    """Create a mock ADK ToolContext for DynamicTool tests."""
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.id = "test-session-001"
    ctx.state = {}
    return ctx


@pytest.fixture
def mock_http_client(monkeypatch):
    """Patch httpx.AsyncClient for MCP server tests.

    Returns a mock whose .get() and .post() are AsyncMock instances.
    Use mock_http_client.get.return_value = httpx.Response(200, json={...})
    to configure responses.
    """
    import httpx

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock()
    mock_client.post = AsyncMock()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: mock_client)
    return mock_client


@pytest.fixture
def sample_evidence():
    """Sample evidence items for tool tests."""
    return [
        {
            "source": "pubmed",
            "pmid": "12345678",
            "title": "Metformin in Type 2 Diabetes: A Systematic Review",
            "snippet": "Metformin reduces HbA1c by 1.0-1.5% compared to placebo.",
            "study_type": "systematic_review",
            "year": 2023,
        },
        {
            "source": "pubmed",
            "pmid": "87654321",
            "title": "GLP-1 Receptor Agonists: Cardiovascular Outcomes",
            "snippet": "Semaglutide demonstrated 26% reduction in MACE events.",
            "study_type": "rct",
            "year": 2024,
        },
        {
            "source": "clinicaltrials",
            "nct_id": "NCT05000001",
            "title": "Phase 3 Trial of Novel SGLT2 Inhibitor",
            "snippet": "Recruiting participants with eGFR > 30 mL/min.",
            "study_type": "rct",
            "year": 2024,
        },
    ]


@pytest.fixture
def sample_decomposition():
    """Sample query decomposition for completeness/reflection tests."""
    return {
        "original_question": "What are the best treatments for type 2 diabetes with cardiovascular risk?",
        "sub_questions": [
            {
                "question": "What is the current first-line treatment for type 2 diabetes?",
                "domain": "drugs",
                "target_agent": "DrugSpecialist",
                "priority": 1,
            },
            {
                "question": "What clinical trials are studying cardiovascular outcomes in diabetes?",
                "domain": "clinical_trials",
                "target_agent": "ClinicalTrialsSpecialist",
                "priority": 2,
            },
            {
                "question": "What are the cardiovascular safety profiles of diabetes medications?",
                "domain": "literature",
                "target_agent": "LiteratureSpecialist",
                "priority": 2,
            },
        ],
    }


@pytest.fixture
def sample_citations():
    """Sample citations for claim verifier and citation validator tests."""
    return [
        {
            "id": "cite:s0r0",
            "title": "Metformin in Type 2 Diabetes: A Systematic Review",
            "snippet": "Metformin reduces HbA1c by 1.0-1.5% and is first-line therapy.",
            "url": "https://pubmed.ncbi.nlm.nih.gov/12345678",
        },
        {
            "id": "cite:s0r1",
            "title": "GLP-1 Receptor Agonists and Cardiovascular Outcomes",
            "snippet": "Semaglutide reduced major adverse cardiovascular events by 26%.",
            "url": "https://pubmed.ncbi.nlm.nih.gov/87654321",
        },
    ]
