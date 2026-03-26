"""Tests for lifesci_common.tracing bootstrap — Langfuse + Phoenix backends."""

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    """Reset module-level state between tests."""
    import lifesci_common.tracing as mod

    mod._initialized = False
    mod._active_backend = None
    yield
    mod._initialized = False
    mod._active_backend = None
    # Clean up env vars that _init_langfuse may have set
    os.environ.pop("LANGFUSE_HOST", None)


@pytest.fixture()
def _reset_litellm_callbacks():
    """Save and restore litellm.callbacks around a test."""
    import litellm
    original = list(litellm.callbacks or [])
    yield
    litellm.callbacks = original


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


def test_langfuse_missing_litellm_falls_through(monkeypatch):
    """If litellm import fails, Langfuse init falls through to Phoenix."""
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "litellm", None)

    import lifesci_common.tracing as mod

    mod._initialized = False
    assert mod.init_tracing() is False


def test_langfuse_successful_init(monkeypatch, _reset_litellm_callbacks):
    """Langfuse backend initialises when both keys are set."""
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://test.langfuse.com")

    import lifesci_common.tracing as mod

    mod._initialized = False
    result = mod.init_tracing()
    assert result is True
    assert mod._initialized is True
    assert mod._active_backend == "langfuse"

    import litellm
    assert "langfuse_otel" in litellm.callbacks


def test_langfuse_base_url_maps_to_host(monkeypatch, _reset_litellm_callbacks):
    """LANGFUSE_BASE_URL is mapped to LANGFUSE_HOST for litellm compat."""
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://custom.langfuse.com")
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    import lifesci_common.tracing as mod

    mod._initialized = False
    mod.init_tracing()
    assert os.environ.get("LANGFUSE_HOST") == "https://custom.langfuse.com"


def test_langfuse_host_not_overwritten(monkeypatch, _reset_litellm_callbacks):
    """If LANGFUSE_HOST is already set, LANGFUSE_BASE_URL does not overwrite."""
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://base.langfuse.com")
    monkeypatch.setenv("LANGFUSE_HOST", "https://host.langfuse.com")

    import lifesci_common.tracing as mod

    mod._initialized = False
    mod.init_tracing()
    assert os.environ.get("LANGFUSE_HOST") == "https://host.langfuse.com"


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
    assert MockInstrumentor.instrumented is True


# ── Langfuse takes priority over Phoenix ────────────────────────────────────


def test_langfuse_takes_priority_over_phoenix(monkeypatch, _reset_litellm_callbacks):
    """When both Langfuse and Phoenix keys are set, Langfuse wins."""
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("PHOENIX_API_KEY", "test-key")

    import lifesci_common.tracing as mod

    mod._initialized = False
    result = mod.init_tracing()
    assert result is True
    assert mod._active_backend == "langfuse"


# ── get_active_backend ──────────────────────────────────────────────────────


def test_get_active_backend_none_by_default():
    import lifesci_common.tracing as mod

    assert mod.get_active_backend() is None


def test_get_active_backend_returns_value():
    import lifesci_common.tracing as mod

    mod._active_backend = "langfuse"
    assert mod.get_active_backend() == "langfuse"
