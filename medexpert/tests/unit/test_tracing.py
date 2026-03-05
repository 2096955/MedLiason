"""Tests for lifesci_common.tracing bootstrap."""

import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    """Reset module-level state between tests."""
    import lifesci_common.tracing as mod

    mod._initialized = False
    mod._tracer_provider = None
    yield
    mod._initialized = False
    mod._tracer_provider = None


def test_no_api_key_returns_false(monkeypatch):
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)
    from lifesci_common.tracing import init_tracing

    assert init_tracing() is False


def test_missing_extras_returns_false(monkeypatch):
    """Simulate tracing extras not installed via sys.modules blocking."""
    monkeypatch.setenv("PHOENIX_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "phoenix", None)
    monkeypatch.setitem(sys.modules, "phoenix.otel", None)

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is False


def test_idempotent_when_initialized():
    import lifesci_common.tracing as mod

    mod._initialized = True
    assert mod.init_tracing() is True


def test_register_failure_leaves_uninitialized(monkeypatch):
    """If register() raises, _initialized stays False so retry is possible."""
    monkeypatch.setenv("PHOENIX_API_KEY", "test-key")

    phoenix_otel = pytest.importorskip("phoenix.otel")
    monkeypatch.setattr(
        phoenix_otel, "register", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is False
    assert mod._initialized is False
    assert mod._tracer_provider is None


def test_shutdown_flushes_provider():
    """shutdown_tracing() calls shutdown on the provider and clears it."""
    import lifesci_common.tracing as mod

    class MockProvider:
        shut_down = False

        def shutdown(self):
            MockProvider.shut_down = True

    mod._tracer_provider = MockProvider()
    mod.shutdown_tracing()
    assert MockProvider.shut_down is True
    assert mod._tracer_provider is None


def test_shutdown_noop_when_no_provider():
    """shutdown_tracing() is safe to call when no provider is set."""
    import lifesci_common.tracing as mod

    mod._tracer_provider = None
    mod.shutdown_tracing()  # should not raise


def test_successful_init(monkeypatch):
    phoenix_otel = pytest.importorskip("phoenix.otel")
    openinference_litellm = pytest.importorskip(
        "openinference.instrumentation.litellm"
    )

    monkeypatch.setenv("PHOENIX_API_KEY", "test-key")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
    monkeypatch.setenv("PHOENIX_PROJECT_NAME", "test-project")

    mock_provider = type("MockProvider", (), {"shutdown": lambda self: None})()

    class MockInstrumentor:
        instrumented = False

        def instrument(self, tracer_provider=None):
            MockInstrumentor.instrumented = True

    def mock_register(**kwargs):
        assert kwargs["project_name"] == "test-project"
        assert kwargs["endpoint"] == "http://localhost:6006"
        assert kwargs["headers"] == {"api_key": "test-key"}
        assert kwargs["batch"] is True
        assert "resource_attributes" in kwargs
        assert kwargs["resource_attributes"]["service.name"] == "medexpert"
        return mock_provider

    monkeypatch.setattr(phoenix_otel, "register", mock_register)
    monkeypatch.setattr(
        openinference_litellm, "LiteLLMInstrumentor", MockInstrumentor
    )

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is True
    assert mod._initialized is True
    assert mod._tracer_provider is mock_provider
    assert MockInstrumentor.instrumented is True
