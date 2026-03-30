"""Tests for the OpenEvidence MCP server.

Covers ask_openevidence, get_openevidence_article, search_openevidence_history
with mocked HTTP responses.  Tests cookie loading, graceful degradation,
polling logic, answer extraction, input sanitization, and caching.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

import mcp_servers.openevidence.server as oe_mod

# FastMCP 3.x: @mcp.tool() returns the original function directly.
ask_openevidence = oe_mod.ask_openevidence
get_openevidence_article = oe_mod.get_openevidence_article
search_openevidence_history = oe_mod.search_openevidence_history


@pytest.fixture(autouse=True)
def _clear_state():
    """Clear module-level cache and cookie cache between tests."""
    oe_mod._cache._cache.clear()
    oe_mod._cookie_header = None
    oe_mod._cookie_loaded_at = 0.0
    yield
    oe_mod._cache._cache.clear()
    oe_mod._cookie_header = None
    oe_mod._cookie_loaded_at = 0.0


def _mock_resp(status_code, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


STORAGE_STATE = {
    "cookies": [
        {"name": "session_id", "value": "abc", "domain": ".openevidence.com"},
        {"name": "auth", "value": "xyz", "domain": ".openevidence.com"},
        {"name": "other", "value": "foo", "domain": ".example.com"},
    ]
}

COMPLETED_ARTICLE = {
    "id": "art-001",
    "status": "completed",
    "inputs": {
        "question": "What is the first-line treatment for CAP?",
        "history": [
            {"outputText": "Amoxicillin is recommended as first-line treatment."}
        ],
    },
}

PENDING_ARTICLE = {
    "id": "art-002",
    "status": "processing",
    "inputs": {"question": "Test", "history": []},
}


def _patch_cookies_available():
    """Patch cookie loading to return valid cookies."""
    return patch.object(oe_mod, "_load_cookie_header", return_value="session_id=abc; auth=xyz")


def _patch_cookies_missing():
    """Patch cookie loading to return None (not configured)."""
    return patch.object(oe_mod, "_load_cookie_header", return_value=None)


# ---------------------------------------------------------------------------
# Cookie loading
# ---------------------------------------------------------------------------


class TestCookieLoading:
    def test_loads_cookies_from_valid_file(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(STORAGE_STATE))
        with patch.object(oe_mod, "OE_AUTH_STATE_PATH", str(state_file)):
            header = oe_mod._load_cookie_header()
        assert "session_id=abc" in header
        assert "auth=xyz" in header
        assert "other=foo" not in header  # filtered out

    def test_returns_none_when_file_missing(self, tmp_path):
        with patch.object(oe_mod, "OE_AUTH_STATE_PATH", str(tmp_path / "missing.json")):
            assert oe_mod._load_cookie_header() is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json")
        with patch.object(oe_mod, "OE_AUTH_STATE_PATH", str(bad_file)):
            assert oe_mod._load_cookie_header() is None

    def test_returns_none_when_no_oe_cookies(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"cookies": [
            {"name": "x", "value": "y", "domain": ".example.com"}
        ]}))
        with patch.object(oe_mod, "OE_AUTH_STATE_PATH", str(state_file)):
            assert oe_mod._load_cookie_header() is None

    def test_caches_cookie_header(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(STORAGE_STATE))
        with patch.object(oe_mod, "OE_AUTH_STATE_PATH", str(state_file)):
            h1 = oe_mod._load_cookie_header()
            # Delete file — should still return cached
            state_file.unlink()
            h2 = oe_mod._load_cookie_header()
        assert h1 == h2


# ---------------------------------------------------------------------------
# ask_openevidence
# ---------------------------------------------------------------------------


class TestAskOpenevidence:
    async def test_successful_ask(self):
        with (
            _patch_cookies_available(),
            patch("mcp_servers.openevidence.server.resilient_post", new_callable=AsyncMock, return_value=_mock_resp(200, PENDING_ARTICLE)),
            patch("mcp_servers.openevidence.server.resilient_get", new_callable=AsyncMock, return_value=_mock_resp(200, COMPLETED_ARTICLE)),
        ):
            resp = await ask_openevidence("What is CAP treatment?")
        assert resp["status"] == "completed"
        assert "Amoxicillin" in resp["answer_text"]
        assert resp["source"] == "OpenEvidence"

    async def test_empty_question(self):
        resp = await ask_openevidence("")
        assert "error" in resp

    async def test_not_configured(self):
        with _patch_cookies_missing():
            resp = await ask_openevidence("test question")
        assert resp["status"] == "not_configured"

    async def test_api_error_on_post(self):
        with (
            _patch_cookies_available(),
            patch("mcp_servers.openevidence.server.resilient_post", new_callable=AsyncMock, return_value=_mock_resp(500)),
        ):
            resp = await ask_openevidence("test")
        assert "error" in resp
        assert "500" in resp["error"]

    async def test_circuit_breaker_error(self):
        with (
            _patch_cookies_available(),
            patch("mcp_servers.openevidence.server.resilient_post", new_callable=AsyncMock, side_effect=oe_mod.CircuitOpenError("https://www.openevidence.com")),
            pytest.raises(ToolError) as exc_info,
        ):
            await ask_openevidence("test")
        err = json.loads(str(exc_info.value))
        assert err["error_category"] == "circuit_open"
        assert err["is_retryable"] is True
        assert err["server"] == "openevidence"

    async def test_cache_hit(self):
        with (
            _patch_cookies_available(),
            patch("mcp_servers.openevidence.server.resilient_post", new_callable=AsyncMock, return_value=_mock_resp(200, PENDING_ARTICLE)) as mock_post,
            patch("mcp_servers.openevidence.server.resilient_get", new_callable=AsyncMock, return_value=_mock_resp(200, COMPLETED_ARTICLE)),
        ):
            await ask_openevidence("CAP treatment?")
            await ask_openevidence("CAP treatment?")
        assert mock_post.call_count == 1

    async def test_article_type_correct(self):
        with (
            _patch_cookies_available(),
            patch("mcp_servers.openevidence.server.resilient_post", new_callable=AsyncMock, return_value=_mock_resp(200, PENDING_ARTICLE)) as mock_post,
            patch("mcp_servers.openevidence.server.resilient_get", new_callable=AsyncMock, return_value=_mock_resp(200, COMPLETED_ARTICLE)),
        ):
            await ask_openevidence("test")
        payload = mock_post.call_args[1]["json"]
        assert payload["article_type"] == "Ask OpenEvidence Light with citations"
        assert payload["inputs"]["use_gatekeeper"] is True


# ---------------------------------------------------------------------------
# get_openevidence_article
# ---------------------------------------------------------------------------


class TestGetOpenEvidenceArticle:
    async def test_successful_fetch(self):
        with (
            _patch_cookies_available(),
            patch("mcp_servers.openevidence.server.resilient_get", new_callable=AsyncMock, return_value=_mock_resp(200, COMPLETED_ARTICLE)),
        ):
            resp = await get_openevidence_article("art-001")
        assert resp["article_id"] == "art-001"
        assert "Amoxicillin" in resp["answer_text"]

    async def test_empty_id(self):
        resp = await get_openevidence_article("")
        assert "error" in resp

    async def test_not_found(self):
        with (
            _patch_cookies_available(),
            patch("mcp_servers.openevidence.server.resilient_get", new_callable=AsyncMock, return_value=_mock_resp(404)),
        ):
            resp = await get_openevidence_article("missing-id")
        assert "not found" in resp["error"].lower()

    async def test_not_configured(self):
        with _patch_cookies_missing():
            resp = await get_openevidence_article("art-001")
        assert resp["status"] == "not_configured"

    async def test_pending_not_cached(self):
        with (
            _patch_cookies_available(),
            patch("mcp_servers.openevidence.server.resilient_get", new_callable=AsyncMock, return_value=_mock_resp(200, PENDING_ARTICLE)) as mock_get,
        ):
            await get_openevidence_article("art-002")
            await get_openevidence_article("art-002")
        assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# search_openevidence_history
# ---------------------------------------------------------------------------


class TestSearchOpenEvidenceHistory:
    async def test_successful_search(self):
        articles = [
            {"id": "art-001", "status": "completed", "inputs": {"question": "CAP"}, "createdAt": "2025-01-15"},
            {"id": "art-002", "status": "completed", "inputs": {"question": "diabetes"}, "createdAt": "2025-01-14"},
        ]
        with (
            _patch_cookies_available(),
            patch("mcp_servers.openevidence.server.resilient_get", new_callable=AsyncMock, return_value=_mock_resp(200, articles)),
        ):
            resp = await search_openevidence_history("treatment")
        assert resp["count"] == 2
        assert resp["results"][0]["article_id"] == "art-001"

    async def test_empty_query(self):
        resp = await search_openevidence_history("")
        assert "error" in resp

    async def test_not_configured(self):
        with _patch_cookies_missing():
            resp = await search_openevidence_history("test")
        assert resp["status"] == "not_configured"

    async def test_limit_capped(self):
        with (
            _patch_cookies_available(),
            patch("mcp_servers.openevidence.server.resilient_get", new_callable=AsyncMock, return_value=_mock_resp(200, [])) as mock_get,
        ):
            await search_openevidence_history("test", limit=100)
        params = mock_get.call_args[1]["params"]
        assert params["limit"] == 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestExtractAnswer:
    def test_valid_article(self):
        assert oe_mod._extract_answer(COMPLETED_ARTICLE) == "Amoxicillin is recommended as first-line treatment."

    def test_empty_history(self):
        assert oe_mod._extract_answer(PENDING_ARTICLE) == ""

    def test_missing_inputs(self):
        assert oe_mod._extract_answer({}) == ""

    def test_non_dict_last_entry(self):
        article = {"inputs": {"history": ["not a dict"]}}
        assert oe_mod._extract_answer(article) == ""

    def test_missing_output_text(self):
        article = {"inputs": {"history": [{"other_field": "value"}]}}
        assert oe_mod._extract_answer(article) == ""
