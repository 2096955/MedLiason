"""Tests for CitationValidatorTool — PMID/DOI resolution via PubMed and CrossRef."""

import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from lifesci_tools.citation_validator import (
    CitationValidatorTool,
    _validate_pmid,
    _validate_doi,
)


@pytest.fixture
def tool():
    return CitationValidatorTool()


@pytest.fixture
def ctx():
    return MagicMock()


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


# ── _validate_pmid ────────────────────────────────────────────


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_valid_pmid(mock_get):
    mock_get.return_value = _mock_response(200, {
        "result": {
            "12345678": {
                "title": "Metformin in Type 2 Diabetes",
                "authors": [{"name": "Smith J"}, {"name": "Doe A"}],
                "fulljournalname": "The Lancet",
                "pubdate": "2023 Jan",
                "source": "Lancet",
                "elocationid": "10.1016/S0140-6736(23)00001-1",
            }
        }
    })
    result = await _validate_pmid("12345678")
    assert result["valid"] is True
    assert result["metadata"]["title"] == "Metformin in Type 2 Diabetes"
    assert result["metadata"]["journal"] == "The Lancet"
    assert result["metadata"]["year"] == "2023"
    assert len(result["metadata"]["authors"]) == 2


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_invalid_pmid(mock_get):
    mock_get.return_value = _mock_response(200, {
        "result": {
            "99999999": {"error": "cannot get document summary"}
        }
    })
    result = await _validate_pmid("99999999")
    assert result["valid"] is False
    assert "error" in result


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_pmid_http_error(mock_get):
    mock_get.return_value = _mock_response(500)
    result = await _validate_pmid("12345678")
    assert result["valid"] is False


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_pmid_no_title(mock_get):
    mock_get.return_value = _mock_response(200, {
        "result": {"12345678": {"title": "", "authors": []}}
    })
    result = await _validate_pmid("12345678")
    assert result["valid"] is False


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_pmid_network_exception(mock_get):
    mock_get.side_effect = Exception("Connection timeout")
    result = await _validate_pmid("12345678")
    assert result["valid"] is False
    assert "Connection timeout" in result["error"]


# ── _validate_doi ─────────────────────────────────────────────


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_valid_doi(mock_get):
    mock_get.return_value = _mock_response(200, {
        "message": {
            "title": ["Cardiovascular Outcomes with GLP-1 Agonists"],
            "author": [
                {"given": "Jane", "family": "Smith"},
                {"given": "John", "family": "Doe"},
            ],
            "container-title": ["New England Journal of Medicine"],
            "published-print": {"date-parts": [[2024, 3, 15]]},
        }
    })
    result = await _validate_doi("10.1056/NEJMoa2300001")
    assert result["valid"] is True
    assert "GLP-1" in result["metadata"]["title"]
    assert result["metadata"]["year"] == "2024"
    assert "Jane Smith" in result["metadata"]["authors"]


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_doi_not_found(mock_get):
    mock_get.return_value = _mock_response(404)
    result = await _validate_doi("10.9999/nonexistent")
    assert result["valid"] is False
    assert "not found" in result["error"].lower()


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_doi_http_error(mock_get):
    mock_get.return_value = _mock_response(503)
    result = await _validate_doi("10.1056/test")
    assert result["valid"] is False


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_doi_network_exception(mock_get):
    mock_get.side_effect = Exception("DNS resolution failed")
    result = await _validate_doi("10.1056/test")
    assert result["valid"] is False


# ── full tool tests ───────────────────────────────────────────


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_tool_validates_mixed_types(mock_get, tool, ctx):
    async def mock_get_impl(url, **kwargs):
        if "esummary" in url:
            return _mock_response(200, {
                "result": {
                    "11111111": {
                        "title": "PubMed Article",
                        "authors": [{"name": "Author A"}],
                        "fulljournalname": "J Med",
                        "pubdate": "2023",
                        "elocationid": "",
                    }
                }
            })
        else:
            return _mock_response(200, {
                "message": {
                    "title": ["CrossRef Article"],
                    "author": [{"given": "B", "family": "Author"}],
                    "container-title": ["Nature"],
                    "published-print": {"date-parts": [[2024]]},
                }
            })

    mock_get.side_effect = mock_get_impl
    citations = [
        {"type": "pmid", "identifier": "11111111"},
        {"type": "doi", "identifier": "10.1038/test"},
    ]
    result = await tool._run_async_impl(
        {"citations_json": json.dumps(citations)}, ctx
    )
    assert result["total"] == 2
    assert result["valid_count"] == 2
    assert result["invalid_count"] == 0


async def test_empty_identifier(tool, ctx):
    citations = [{"type": "pmid", "identifier": ""}]
    result = await tool._run_async_impl(
        {"citations_json": json.dumps(citations)}, ctx
    )
    assert result["results"][0]["valid"] is False
    assert "Empty" in result["results"][0]["error"]


async def test_unknown_type(tool, ctx):
    citations = [{"type": "isbn", "identifier": "978-0-123456-78-9"}]
    result = await tool._run_async_impl(
        {"citations_json": json.dumps(citations)}, ctx
    )
    assert result["results"][0]["valid"] is False
    assert "Unknown type" in result["results"][0]["error"]


async def test_invalid_json_error(tool, ctx):
    result = await tool._run_async_impl(
        {"citations_json": "not json"}, ctx
    )
    assert "error" in result


async def test_not_array_error(tool, ctx):
    result = await tool._run_async_impl(
        {"citations_json": '{"key": "val"}'}, ctx
    )
    assert "error" in result


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_counts_valid_and_invalid(mock_get, tool, ctx):
    async def mock_get_impl(url, **kwargs):
        if "esummary" in url:
            return _mock_response(200, {
                "result": {
                    "11111111": {"title": "Valid", "authors": [], "fulljournalname": "J", "pubdate": "2023", "elocationid": ""},
                    "99999999": {"error": "not found"},
                }
            })
        return _mock_response(404)

    mock_get.side_effect = mock_get_impl
    citations = [
        {"type": "pmid", "identifier": "11111111"},
        {"type": "pmid", "identifier": "99999999"},
    ]
    result = await tool._run_async_impl(
        {"citations_json": json.dumps(citations)}, ctx
    )
    assert result["total"] == 2
    assert result["valid_count"] == 1
    assert result["invalid_count"] == 1


@patch("lifesci_tools.citation_validator.resilient_get")
async def test_authors_limited_to_5(mock_get, tool, ctx):
    mock_get.return_value = _mock_response(200, {
        "result": {
            "12345678": {
                "title": "Big Study",
                "authors": [{"name": f"Author{i}"} for i in range(20)],
                "fulljournalname": "J",
                "pubdate": "2024",
                "elocationid": "",
            }
        }
    })
    citations = [{"type": "pmid", "identifier": "12345678"}]
    result = await tool._run_async_impl(
        {"citations_json": json.dumps(citations)}, ctx
    )
    assert len(result["results"][0]["metadata"]["authors"]) <= 5
