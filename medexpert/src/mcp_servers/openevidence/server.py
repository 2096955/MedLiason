"""OpenEvidence Clinical AI MCP Server.

Provides access to OpenEvidence's AI-powered clinical question answering
system.  Agents can ask medical questions and receive evidence-based
answers with citations.

Authentication uses Playwright-exported session cookies loaded from a
``storage-state.json`` file (path configurable via ``OE_AUTH_STATE_PATH``).

API: OpenEvidence REST API (cookie-authenticated).
Port: 9018
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import CircuitOpenError, RetryExhaustedError, resilient_get, resilient_post, structured_error_response
from mcp_servers._security import sanitize_query

log = logging.getLogger(__name__)

mcp = FastMCP("openevidence")

BASE_URL = "https://www.openevidence.com"

OE_AUTH_STATE_PATH = os.environ.get(
    "OE_AUTH_STATE_PATH",
    os.path.expanduser("~/.openevidence-mcp/auth/storage-state.json"),
)

# Polling statuses that indicate the article is still being generated
_PENDING_STATUSES = {"queued", "pending", "processing", "running", "in_progress"}

# Default polling interval (seconds)
_POLL_INTERVAL = 1.2


# ---------------------------------------------------------------------------
# In-memory TTL cache (30-minute TTL for completed answers)
# ---------------------------------------------------------------------------


class _AnswerCache:
    """Simple in-memory cache with TTL and maxsize eviction."""

    def __init__(self, ttl_seconds: int = 1800, maxsize: int = 200):
        self._cache: dict[str, tuple[float, dict]] = {}
        self._ttl = ttl_seconds
        self._maxsize = maxsize

    def get(self, key: str):
        entry = self._cache.get(key)
        if entry and (time.monotonic() - entry[0]) < self._ttl:
            return entry[1]
        if entry:
            del self._cache[key]
        return None

    def set(self, key: str, value: dict) -> None:
        if len(self._cache) >= self._maxsize:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        self._cache[key] = (time.monotonic(), value)


_cache = _AnswerCache(ttl_seconds=1800, maxsize=200)


# ---------------------------------------------------------------------------
# Cookie authentication
# ---------------------------------------------------------------------------


_cookie_header: str | None = None
_cookie_loaded_at: float = 0.0
_COOKIE_CACHE_TTL = 300.0  # 5 minutes


def _load_cookie_header() -> str | None:
    """Load and cache the Cookie header from the Playwright storage-state file.

    Returns the ``Cookie: name1=value1; name2=value2`` header value, or
    ``None`` if the file is missing or contains no relevant cookies.
    The parsed result is cached for 5 minutes.
    """
    global _cookie_header, _cookie_loaded_at

    now = time.monotonic()
    if _cookie_header is not None and (now - _cookie_loaded_at) < _COOKIE_CACHE_TTL:
        return _cookie_header

    state_path = Path(OE_AUTH_STATE_PATH).expanduser()
    if not state_path.is_file():
        log.warning(
            "OpenEvidence auth state file not found: %s — tools will return not_configured",
            state_path,
        )
        _cookie_header = None
        _cookie_loaded_at = now
        return None

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to parse OpenEvidence auth state: %s", exc)
        _cookie_header = None
        _cookie_loaded_at = now
        return None

    cookies = data.get("cookies", [])
    now_epoch = time.time()
    oe_cookies = [
        c
        for c in cookies
        if c.get("domain", "").endswith(".openevidence.com")
        and c.get("expires", float("inf")) > now_epoch
    ]

    if not oe_cookies:
        log.warning("No .openevidence.com cookies found in storage-state file")
        _cookie_header = None
        _cookie_loaded_at = now
        return None

    header_value = "; ".join(f"{c['name']}={c['value']}" for c in oe_cookies)
    _cookie_header = header_value
    _cookie_loaded_at = now
    log.info("Loaded %d OpenEvidence cookies from %s", len(oe_cookies), state_path)
    return _cookie_header


def _get_headers() -> dict[str, str]:
    """Build request headers including cookie authentication."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "MedExpert-MCP/1.0",
    }
    cookie = _load_cookie_header()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _not_configured_response() -> dict:
    return {
        "status": "not_configured",
        "reason": (
            "OpenEvidence session cookies not available. "
            "Run `npm run login` in the openevidence-mcp repo to generate "
            "storage-state.json, then set OE_AUTH_STATE_PATH."
        ),
    }


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------


def _extract_answer(article: dict) -> str:
    """Extract the answer text from an OpenEvidence article response."""
    inputs = article.get("inputs", {})
    history = inputs.get("history", [])
    if history:
        last_entry = history[-1]
        if isinstance(last_entry, dict):
            return last_entry.get("outputText", "")
    return ""


def _build_article_response(article: dict, question: str | None = None) -> dict:
    """Build a standardised response dict from an article object."""
    article_id = article.get("id", article.get("_id", ""))
    status = article.get("status", "unknown")
    answer_text = _extract_answer(article)

    response = {
        "article_id": str(article_id),
        "status": status,
        "question": question or article.get("inputs", {}).get("question", ""),
        "answer_text": answer_text,
        "source": "OpenEvidence",
    }
    return response


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def ask_openevidence(
    question: str,
    timeout_sec: int = 120,
) -> dict:
    """Ask OpenEvidence a medical question and get an evidence-based answer.

    Submits a clinical question to OpenEvidence's AI engine, waits for
    the answer to be generated, and returns the response with citations.

    Args:
        question: The medical/clinical question to ask (e.g.
            ``"What is the first-line treatment for type 2 diabetes?"``,
            ``"What are the latest guidelines for hypertension management?"``).
        timeout_sec: Maximum seconds to wait for answer generation
            (default 120, max 300).

    Returns:
        Dictionary with ``article_id``, ``status``, ``question``,
        ``answer_text``, and ``source`` keys.
    """
    safe_question = sanitize_query(question, max_len=2000)
    if not safe_question:
        return {"error": "Empty question after sanitization"}

    # Check cookie auth
    if _load_cookie_header() is None:
        return _not_configured_response()

    # Check cache by question
    cache_key = f"ask:{safe_question}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    headers = _get_headers()
    timeout_sec = min(max(timeout_sec, 10), 300)

    # Submit the question
    payload = {
        "article_type": "Ask OpenEvidence Light with citations",
        "inputs": {
            "question": safe_question,
            "variant_configuration_file": "prod",
            "attachments": [],
            "use_gatekeeper": True,
        },
        "personalization_enabled": False,
        "disable_caching": False,
        "original_article": None,
    }

    try:
        submit_resp = await resilient_post(
            f"{BASE_URL}/api/article",
            json=payload,
            headers=headers,
            timeout=30.0,
        )
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "openevidence", "ask_openevidence"), "question": safe_question}
    except Exception as exc:
        log.error("Failed to submit question to OpenEvidence: %s", exc)
        return {"error": f"Failed to submit question: {exc}", "question": safe_question}

    if submit_resp.status_code not in (200, 201):
        return {
            "error": f"OpenEvidence returned status {submit_resp.status_code}",
            "question": safe_question,
        }

    try:
        article = submit_resp.json()
    except Exception:
        return {"error": "Failed to parse OpenEvidence response", "question": safe_question}

    article_id = article.get("id", article.get("_id", ""))
    if not article_id:
        return {"error": "No article ID in response", "question": safe_question}

    # Poll until complete or timeout
    status = article.get("status", "")
    deadline = time.monotonic() + timeout_sec

    while status in _PENDING_STATUSES and time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            poll_resp = await resilient_get(
                f"{BASE_URL}/api/article/{article_id}",
                headers=headers,
                timeout=15.0,
            )
            if poll_resp.status_code == 200:
                article = poll_resp.json()
                status = article.get("status", "")
            else:
                log.warning(
                    "Poll returned status %d for article %s",
                    poll_resp.status_code,
                    article_id,
                )
                break
        except (CircuitOpenError, RetryExhaustedError) as exc:
            return {
                **structured_error_response(exc, "openevidence", "ask_openevidence"),
                "article_id": str(article_id),
                "question": safe_question,
            }
        except Exception as exc:
            log.warning("Poll error for article %s: %s", article_id, exc)
            break

    if status in _PENDING_STATUSES:
        return {
            "error": "Timed out waiting for OpenEvidence answer",
            "article_id": str(article_id),
            "status": status,
            "question": safe_question,
            "timeout_sec": timeout_sec,
        }

    response = {**_build_article_response(article, question=safe_question), "success": True}

    # Cache completed answers
    if status not in _PENDING_STATUSES and response.get("answer_text"):
        _cache.set(cache_key, response)
        _cache.set(f"article:{article_id}", response)

    return response


@mcp.tool()
async def get_openevidence_article(article_id: str) -> dict:
    """Retrieve a previously generated OpenEvidence article by ID.

    Use this to fetch the full answer for an article that was previously
    submitted via ``ask_openevidence``.

    Args:
        article_id: The OpenEvidence article ID to retrieve.

    Returns:
        Dictionary with ``article_id``, ``status``, ``question``,
        ``answer_text``, and ``source`` keys.
    """
    if not article_id or not article_id.strip():
        return {"error": "Empty article_id parameter"}

    article_id = article_id.strip()

    # Check cookie auth
    if _load_cookie_header() is None:
        return _not_configured_response()

    # Check cache
    cache_key = f"article:{article_id}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    headers = _get_headers()

    try:
        resp = await resilient_get(
            f"{BASE_URL}/api/article/{article_id}",
            headers=headers,
            timeout=15.0,
        )
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "openevidence", "get_openevidence_article"), "article_id": article_id}
    except Exception as exc:
        log.error("Failed to fetch article %s: %s", article_id, exc)
        return {"error": f"Failed to fetch article: {exc}", "article_id": article_id}

    if resp.status_code == 404:
        return {"error": "Article not found", "article_id": article_id}

    if resp.status_code != 200:
        return {
            "error": f"OpenEvidence returned status {resp.status_code}",
            "article_id": article_id,
        }

    try:
        article = resp.json()
    except Exception:
        return {"error": "Failed to parse response", "article_id": article_id}

    response = {**_build_article_response(article), "success": True}

    # Cache completed answers
    status = article.get("status", "")
    if status not in _PENDING_STATUSES and response.get("answer_text"):
        _cache.set(cache_key, response)

    return response


@mcp.tool()
async def search_openevidence_history(
    query: str,
    limit: int = 20,
) -> dict:
    """Search your OpenEvidence question history.

    Searches previously asked questions on OpenEvidence for relevant
    past answers.  Useful for finding prior research without re-asking.

    Args:
        query: Search term to filter past questions (e.g.
            ``"diabetes"``, ``"statin therapy"``).
        limit: Maximum number of results (default 20, max 50).

    Returns:
        Dictionary with ``query``, ``count``, and ``results`` list.
        Each result has ``article_id``, ``question``, ``status``,
        and ``created_at`` keys.
    """
    safe_query = sanitize_query(query, max_len=500)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    # Check cookie auth
    if _load_cookie_header() is None:
        return _not_configured_response()

    headers = _get_headers()
    capped = min(max(limit, 1), 50)

    try:
        resp = await resilient_get(
            f"{BASE_URL}/api/article/list",
            params={"search": safe_query, "limit": capped},
            headers=headers,
            timeout=15.0,
        )
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "openevidence", "search_openevidence_history"), "query": safe_query, "results": []}
    except Exception as exc:
        log.error("Failed to search OpenEvidence history: %s", exc)
        return {
            "error": f"Failed to search history: {exc}",
            "query": safe_query,
            "results": [],
        }

    if resp.status_code != 200:
        return {
            "error": f"OpenEvidence returned status {resp.status_code}",
            "query": safe_query,
            "results": [],
        }

    try:
        data = resp.json()
    except Exception:
        return {
            "error": "Failed to parse response",
            "query": safe_query,
            "results": [],
        }

    # Normalise: response may be a list or a dict with a "results" key
    if isinstance(data, list):
        articles = data
    elif isinstance(data, dict):
        articles = data.get("results", data.get("articles", []))
    else:
        articles = []

    results = []
    for item in articles[:capped]:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "article_id": str(item.get("id", item.get("_id", ""))),
                "question": item.get("inputs", {}).get("question", ""),
                "status": item.get("status", "unknown"),
                "created_at": item.get("createdAt", item.get("created_at", "")),
            }
        )

    return {
        "success": True,
        "query": safe_query,
        "count": len(results),
        "results": results,
        "source": "OpenEvidence",
    }


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9018)
