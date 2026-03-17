"""Tests for lifesci_common.tracing bootstrap — Langfuse + Phoenix backends."""

import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    """Reset module-level state between tests."""
    import lifesci_common.tracing as mod

    mod._initialized = False
    mod._tracer_provider = None
    mod._active_backend = None
    yield
    mod._initialized = False
    mod._tracer_provider = None
    mod._active_backend = None


# ── No backend configured ──────────────────────────────────────────────────


def test_no_keys_returns_false(monkeypatch):
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is False
    assert mod.get_active_backend() is None


# ── Idempotent ──────────────────────────────────────────────────────────────


def test_idempotent_when_initialized():
    import lifesci_common.tracing as mod

    mod._initialized = True
    assert mod.init_tracing() is True


# ── Langfuse backend ───────────────────────────────────────────────────────


def test_langfuse_no_secret_key_skips(monkeypatch):
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is False


def test_langfuse_no_public_key_skips(monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is False


def test_langfuse_missing_deps_falls_through(monkeypatch):
    """If langfuse package not installed, falls through to Phoenix (also missing)."""
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "langfuse", None)

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is False


def test_langfuse_successful_init(monkeypatch):
    """Langfuse backend initialises when both keys are set and deps available."""
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://test.langfuse.com")

    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:
        pytest.skip("OpenTelemetry OTLP exporter not installed")

    try:
        import langfuse  # noqa: F401
    except ImportError:
        pytest.skip("langfuse not installed")

    import lifesci_common.tracing as mod

    mod._initialized = False
    result = mod.init_tracing()
    assert result is True
    assert mod._initialized is True
    assert mod._active_backend == "langfuse"
    assert mod._tracer_provider is not None


def test_encode_basic_auth():
    from lifesci_common.tracing import _encode_basic_auth

    result = _encode_basic_auth("pk-lf-test", "sk-lf-test")
    import base64

    decoded = base64.b64decode(result).decode()
    assert decoded == "pk-lf-test:sk-lf-test"


# ── Phoenix backend ────────────────────────────────────────────────────────


def test_phoenix_no_api_key_returns_false(monkeypatch):
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is False


def test_phoenix_missing_extras_returns_false(monkeypatch):
    """Simulate tracing extras not installed via sys.modules blocking."""
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("PHOENIX_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "phoenix", None)
    monkeypatch.setitem(sys.modules, "phoenix.otel", None)

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is False


def test_phoenix_register_failure_leaves_uninitialized(monkeypatch):
    """If register() raises, _initialized stays False so retry is possible."""
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("PHOENIX_API_KEY", "test-key")

    phoenix_otel = pytest.importorskip("phoenix.otel")
    monkeypatch.setattr(
        phoenix_otel,
        "register",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is False
    assert mod._initialized is False
    assert mod._tracer_provider is None


def test_phoenix_successful_init(monkeypatch):
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

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
        return mock_provider

    monkeypatch.setattr(phoenix_otel, "register", mock_register)
    monkeypatch.setattr(
        openinference_litellm, "LiteLLMInstrumentor", MockInstrumentor
    )

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is True
    assert mod._initialized is True
    assert mod._active_backend == "phoenix"
    assert mod._tracer_provider is mock_provider
    assert MockInstrumentor.instrumented is True


# ── Langfuse takes priority over Phoenix ────────────────────────────────────


def test_langfuse_takes_priority_over_phoenix(monkeypatch):
    """When both Langfuse and Phoenix keys are set, Langfuse wins."""
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("PHOENIX_API_KEY", "test-key")

    try:
        import langfuse  # noqa: F401
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,  # noqa: F401
        )
    except ImportError:
        pytest.skip("langfuse or OTel OTLP exporter not installed")

    import lifesci_common.tracing as mod

    mod._initialized = False
    result = mod.init_tracing()
    assert result is True
    assert mod._active_backend == "langfuse"


# ── Shutdown ────────────────────────────────────────────────────────────────


def test_shutdown_flushes_provider():
    """shutdown_tracing() calls shutdown on the provider and clears it."""
    import lifesci_common.tracing as mod

    class MockProvider:
        shut_down = False

        def shutdown(self):
            MockProvider.shut_down = True

    mod._tracer_provider = MockProvider()
    mod._active_backend = "langfuse"
    mod.shutdown_tracing()
    assert MockProvider.shut_down is True
    assert mod._tracer_provider is None
    assert mod._active_backend is None


def test_shutdown_noop_when_no_provider():
    """shutdown_tracing() is safe to call when no provider is set."""
    import lifesci_common.tracing as mod

    mod._tracer_provider = None
    mod.shutdown_tracing()  # should not raise


# ── get_active_backend ──────────────────────────────────────────────────────


def test_get_active_backend_none_by_default():
    import lifesci_common.tracing as mod

    assert mod.get_active_backend() is None


def test_get_active_backend_returns_value():
    import lifesci_common.tracing as mod

    mod._active_backend = "langfuse"
    assert mod.get_active_backend() == "langfuse"
