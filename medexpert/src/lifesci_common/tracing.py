"""Arize Phoenix / OpenTelemetry tracing bootstrap for MedExpert.

Call ``init_tracing()`` once at process startup (after .env is loaded).
If the tracing extras are not installed or PHOENIX_API_KEY is unset,
the function is a silent no-op — agents run without instrumentation.
"""

import logging
import os

logger = logging.getLogger("medexpert.tracing")

_initialized = False


def init_tracing() -> bool:
    """Wire LiteLLM → OpenTelemetry → Arize Phoenix.

    Returns True if tracing was enabled, False otherwise.
    """
    global _initialized
    if _initialized:
        return True

    api_key = os.getenv("PHOENIX_API_KEY")
    if not api_key:
        logger.debug("PHOENIX_API_KEY not set — tracing disabled")
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
            "Tracing extras not installed — run: "
            "pip install medexpert[tracing]"
        )
        return False

    try:
        tracer_provider = register(
            project_name=project,
            endpoint=endpoint,
            headers={"api_key": api_key},
            batch=True,
            verbose=False,
        )
        LiteLLMInstrumentor().instrument(tracer_provider=tracer_provider)
        _initialized = True
        logger.info(
            "Phoenix tracing enabled → %s (project=%s)", endpoint, project
        )
        return True
    except Exception:
        logger.warning("Failed to initialise Phoenix tracing", exc_info=True)
        return False
