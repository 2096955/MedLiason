"""Tests for lifesci_common.tracing bootstrap."""

import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def _reset_tracing_state(monkeypatch):
    """Reset module-level _initialized flag between tests."""
    import lifesci_common.tracing as mod

    mod._initialized = False
    yield
    mod._initialized = False


def test_no_api_key_returns_false(monkeypatch):
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)
    from lifesci_common.tracing import init_tracing

    assert init_tracing() is False


def test_missing_extras_returns_false(monkeypatch):
    monkeypatch.setenv("PHOENIX_API_KEY", "test-key")
    import lifesci_common.tracing as mod

    original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def mock_import(name, *args, **kwargs):
        if "phoenix" in name or "openinference" in name:
            raise ImportError("mocked")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)
    # Re-import to trigger the mocked import
    mod._initialized = False
    assert mod.init_tracing() is False


def test_idempotent_when_initialized():
    import lifesci_common.tracing as mod

    mod._initialized = True
    assert mod.init_tracing() is True


def test_successful_init(monkeypatch):
    monkeypatch.setenv("PHOENIX_API_KEY", "test-key")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
    monkeypatch.setenv("PHOENIX_PROJECT_NAME", "test-project")

    mock_provider = object()

    class MockInstrumentor:
        instrumented = False

        def instrument(self, tracer_provider=None):
            MockInstrumentor.instrumented = True

    def mock_register(**kwargs):
        assert kwargs["project_name"] == "test-project"
        assert kwargs["endpoint"] == "http://localhost:6006"
        assert kwargs["headers"] == {"api_key": "test-key"}
        assert kwargs["batch"] is True
        return mock_provider

    import lifesci_common.tracing as mod

    monkeypatch.setattr("lifesci_common.tracing.init_tracing.__module__", mod.__name__)

    # Patch at module level after import
    import phoenix.otel
    import openinference.instrumentation.litellm

    monkeypatch.setattr(phoenix.otel, "register", mock_register)
    monkeypatch.setattr(
        openinference.instrumentation.litellm,
        "LiteLLMInstrumentor",
        MockInstrumentor,
    )

    mod._initialized = False
    assert mod.init_tracing() is True
    assert mod._initialized is True
    assert MockInstrumentor.instrumented is True
