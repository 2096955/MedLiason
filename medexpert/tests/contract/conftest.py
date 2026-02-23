"""Contract test infrastructure — replay recorded API responses via respx.

Contract tests verify that MCP server parsing logic works against real API
response shapes. Cassettes are recorded via RECORD_CASSETTES=1 env var.

Usage:
    # Run with existing cassettes (skip if missing):
    uv run pytest tests/contract/ -v

    # Record fresh cassettes (requires API credentials):
    RECORD_CASSETTES=1 uv run pytest tests/contract/ -v
"""

import json
import os
from pathlib import Path

import httpx
import pytest
import respx

CASSETTES_DIR = Path(__file__).parent.parent / "cassettes"
RECORD_MODE = os.environ.get("RECORD_CASSETTES", "0") == "1"


def cassette_path(server_name: str, test_name: str) -> Path:
    """Return the path to a cassette file."""
    return CASSETTES_DIR / server_name / f"{test_name}.json"


def load_cassette(server_name: str, test_name: str) -> dict | None:
    """Load a cassette file. Returns None if it doesn't exist."""
    path = cassette_path(server_name, test_name)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_cassette(server_name: str, test_name: str, data: dict) -> None:
    """Save a cassette file."""
    path = cassette_path(server_name, test_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def skip_without_cassette(server_name: str, test_name: str):
    """Decorator to skip/fail a test if no cassette exists and we're not recording."""
    cassette = load_cassette(server_name, test_name)
    if cassette is None and not RECORD_MODE:
        if os.environ.get("CI"):
            return pytest.mark.xfail(
                reason=f"Missing cassette for {server_name}/{test_name} (mandatory in CI)",
                strict=True,
            )
        return pytest.mark.skip(reason=f"No cassette for {server_name}/{test_name}")
    return lambda fn: fn


@pytest.fixture
def respx_cassette(request):
    """Fixture that replays a recorded HTTP cassette via respx.

    The cassette file is determined by the test's server_name and test function name.
    Set the `server_name` attribute on the test class or use the cassette_server marker.

    Usage in test:
        @pytest.mark.cassette_server("pubmed")
        async def test_search_pubmed_contract(respx_cassette):
            # respx is pre-configured with recorded responses
            result = await search_pubmed("diabetes")
            assert result["articles"]
    """
    marker = request.node.get_closest_marker("cassette_server")
    server_name = marker.args[0] if marker else "unknown"
    test_name = request.node.name

    cassette = load_cassette(server_name, test_name)
    if cassette is None:
        if os.environ.get("CI"):
            pytest.fail(
                f"Missing cassette for {server_name}/{test_name}. "
                "Contract tests are mandatory in CI. "
                "Run RECORD_CASSETTES=1 pytest locally to record."
            )
        pytest.skip(f"No cassette for {server_name}/{test_name}")

    with respx.mock(assert_all_called=False) as mock:
        for entry in cassette.get("interactions", []):
            req = entry["request"]
            resp = entry["response"]
            pattern = mock.request(
                req["method"],
                url=req["url"],
            )
            pattern.respond(
                status_code=resp["status_code"],
                json=resp.get("json"),
                text=resp.get("text", ""),
                headers=resp.get("headers", {}),
            )
        yield mock


@pytest.fixture
def record_cassette(request):
    """Fixture for recording live API responses to cassette files.

    Use with RECORD_CASSETTES=1 to record new cassettes.
    """
    if not RECORD_MODE:
        pytest.skip("Set RECORD_CASSETTES=1 to record cassettes")

    marker = request.node.get_closest_marker("cassette_server")
    server_name = marker.args[0] if marker else "unknown"
    test_name = request.node.name

    interactions = []

    class RecordingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            transport = httpx.AsyncHTTPTransport()
            response = await transport.handle_async_request(request)
            body = await response.aread()
            try:
                json_body = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                json_body = None

            interactions.append({
                "request": {
                    "method": str(request.method),
                    "url": str(request.url),
                    "headers": dict(request.headers),
                },
                "response": {
                    "status_code": response.status_code,
                    "json": json_body,
                    "text": body.decode("utf-8", errors="replace") if json_body is None else None,
                    "headers": dict(response.headers),
                },
            })
            return response

    yield RecordingTransport()

    save_cassette(server_name, test_name, {"interactions": interactions})
