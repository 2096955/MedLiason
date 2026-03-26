"""LLM observability tracing bootstrap for MedExpert.

Supports two backends (mutually exclusive, checked in order):

1. **Langfuse** — set ``LANGFUSE_SECRET_KEY`` + ``LANGFUSE_PUBLIC_KEY``
2. **Arize Phoenix** — set ``PHOENIX_API_KEY``

LiteLLM has a native ``"langfuse_otel"`` callback that reads
``LANGFUSE_PUBLIC_KEY``, ``LANGFUSE_SECRET_KEY``, and ``LANGFUSE_HOST``
and configures OTel export to the Langfuse ingestion endpoint.

Call ``init_tracing()`` once at process startup (after .env is loaded).
If neither backend is configured or deps are missing, the function is a
silent no-op — agents run without instrumentation.
"""

import logging
import os
import threading

logger = logging.getLogger("medexpert.tracing")

_initialized = False
_lock = threading.Lock()
_active_backend: str | None = None


# ── Langfuse backend ────────────────────────────────────────────────────────


def _init_langfuse() -> bool:
    """Activate LiteLLM's native ``langfuse_otel`` callback.

    Requires env vars:
        LANGFUSE_SECRET_KEY  — project secret key (sk-lf-...)
        LANGFUSE_PUBLIC_KEY  — project public key (pk-lf-...)
        LANGFUSE_HOST        — optional, defaults to US cloud
    """
    global _active_backend

    secret = os.getenv("LANGFUSE_SECRET_KEY")
    public = os.getenv("LANGFUSE_PUBLIC_KEY")
    if not secret or not public:
        return False

    # LiteLLM reads LANGFUSE_HOST, but we may have LANGFUSE_BASE_URL
    base_url = os.getenv("LANGFUSE_BASE_URL")
    if base_url and not os.getenv("LANGFUSE_HOST"):
        os.environ["LANGFUSE_HOST"] = base_url

    try:
        import litellm

        litellm.callbacks = litellm.callbacks or []
        if "langfuse_otel" not in litellm.callbacks:
            litellm.callbacks.append("langfuse_otel")

        _active_backend = "langfuse"
        host = os.getenv("LANGFUSE_HOST", "(US cloud default)")
        logger.info("Langfuse tracing enabled via langfuse_otel → %s", host)
        return True
    except Exception:
        logger.warning("Failed to initialise Langfuse tracing", exc_info=True)
        return False


# ── Phoenix backend ─────────────────────────────────────────────────────────


def _init_phoenix() -> bool:
    """Wire LiteLLM → OpenTelemetry → Arize Phoenix."""
    global _active_backend

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
            _initialized = True
            return True

        # Fall back to Phoenix
        if _init_phoenix():
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
