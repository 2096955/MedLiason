"""LLM observability tracing bootstrap for MedExpert.

Supports two backends (mutually exclusive, checked in order):

1. **Langfuse** — set ``LANGFUSE_SECRET_KEY`` + ``LANGFUSE_PUBLIC_KEY``
2. **Arize Phoenix** — set ``PHOENIX_API_KEY``

Both backends use OpenTelemetry under the hood.  LiteLLM's built-in
``"otel"`` callback reads ``OTEL_EXPORTER_OTLP_*`` env vars; we configure
them to point at Langfuse's OTel ingestion endpoint.

Call ``init_tracing()`` once at process startup (after .env is loaded).
If neither backend is configured or deps are missing, the function is a
silent no-op — agents run without instrumentation.
"""

import atexit
import logging
import os
import threading

logger = logging.getLogger("medexpert.tracing")

_initialized = False
_lock = threading.Lock()
_tracer_provider = None
_active_backend: str | None = None


# ── Langfuse backend ────────────────────────────────────────────────────────


def _init_langfuse() -> bool:
    """Wire LiteLLM → OpenTelemetry → Langfuse.

    Sets OTEL_EXPORTER_OTLP_* env vars so that litellm's built-in "otel"
    callback auto-creates the correct exporter.  Then appends "otel" to
    litellm.callbacks to activate the integration.

    Requires env vars:
        LANGFUSE_SECRET_KEY  — project secret key (sk-lf-...)
        LANGFUSE_PUBLIC_KEY  — project public key (pk-lf-...)
        LANGFUSE_BASE_URL    — optional, defaults to https://cloud.langfuse.com
    """
    global _active_backend

    secret = os.getenv("LANGFUSE_SECRET_KEY")
    public = os.getenv("LANGFUSE_PUBLIC_KEY")
    if not secret or not public:
        return False

    base_url = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    try:
        # Set OTEL env vars so litellm's OTel callback auto-configures.
        # LiteLLM reads these in OpenTelemetryConfig.from_env() when the
        # "otel" callback is first instantiated.
        os.environ.setdefault(
            "OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"
        )
        os.environ.setdefault(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            f"{base_url.rstrip('/')}/api/public/otel",
        )
        os.environ.setdefault(
            "OTEL_EXPORTER_OTLP_HEADERS",
            f"Authorization=Basic {_encode_basic_auth(public, secret)}",
        )
        os.environ.setdefault("OTEL_SERVICE_NAME", "medexpert")

        # Enable LiteLLM's built-in OpenTelemetry callback
        import litellm

        litellm.callbacks = litellm.callbacks or []
        if "otel" not in litellm.callbacks:
            litellm.callbacks.append("otel")

        _active_backend = "langfuse"
        logger.info("Langfuse tracing enabled → %s", base_url)
        return True
    except Exception:
        logger.warning("Failed to initialise Langfuse tracing", exc_info=True)
        return False


def _encode_basic_auth(public_key: str, secret_key: str) -> str:
    """Encode Langfuse public:secret as base64 for HTTP Basic Auth."""
    import base64

    credentials = f"{public_key}:{secret_key}"
    return base64.b64encode(credentials.encode()).decode()


# ── Phoenix backend ─────────────────────────────────────────────────────────


def _init_phoenix() -> bool:
    """Wire LiteLLM → OpenTelemetry → Arize Phoenix."""
    global _tracer_provider, _active_backend

    api_key = os.getenv("PHOENIX_API_KEY")
    if not api_key:
        return False

    endpoint = os.getenv(
        "PHOENIX_COLLECTOR_ENDPOINT", "https://app.phoenix.arize.com"
    )
    project = os.getenv("PHOENIX_PROJECT_NAME", "medexpert")

    try:
        from phoenix.otel import register
        from openinference.instrumentation.litellm import LiteLLMInstrumentor
    except ImportError:
        logger.info(
            "Phoenix tracing extras not installed — run: "
            "pip install medexpert[tracing]"
        )
        return False

    try:
        _tracer_provider = register(
            project_name=project,
            endpoint=endpoint,
            headers={"api_key": api_key},
            batch=True,
            verbose=False,
            resource_attributes={
                "service.name": "medexpert",
                "deployment.environment": os.getenv(
                    "MEDEXPERT_ENV", "development"
                ),
            },
        )
        LiteLLMInstrumentor().instrument(tracer_provider=_tracer_provider)
        _active_backend = "phoenix"
        logger.info(
            "Phoenix tracing enabled → %s (project=%s)",
            endpoint,
            project,
        )
        return True
    except Exception:
        logger.warning(
            "Failed to initialise Phoenix tracing", exc_info=True
        )
        return False


# ── Public API ──────────────────────────────────────────────────────────────


def init_tracing() -> bool:
    """Initialise LLM observability tracing.

    Checks Langfuse first, then Phoenix.  Returns True if either was enabled.
    """
    global _initialized
    with _lock:
        if _initialized:
            return True

        # Try Langfuse first (preferred — richer LLM-specific UI)
        if _init_langfuse():
            atexit.register(shutdown_tracing)
            _initialized = True
            return True

        # Fall back to Phoenix
        if _init_phoenix():
            atexit.register(shutdown_tracing)
            _initialized = True
            return True

        logger.debug(
            "No tracing backend configured — "
            "set LANGFUSE_SECRET_KEY+LANGFUSE_PUBLIC_KEY or PHOENIX_API_KEY"
        )
        return False


def get_active_backend() -> str | None:
    """Return the name of the active tracing backend, or None."""
    return _active_backend


def shutdown_tracing() -> None:
    """Flush pending spans and shut down the tracer provider."""
    global _tracer_provider, _active_backend
    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
            logger.debug("%s tracer provider shut down", _active_backend)
        except Exception:
            logger.debug("Error during tracer shutdown", exc_info=True)
        _tracer_provider = None
        _active_backend = None
