# Shared utilities and constants for MedExpert

# Bootstrap LLM observability tracing (Langfuse or Phoenix) at import time.
# Silent no-op if neither LANGFUSE_SECRET_KEY nor PHOENIX_API_KEY is set.
import logging as _logging

_log = _logging.getLogger("medexpert.init")

try:
    from lifesci_common.tracing import init_tracing as _init_tracing

    _result = _init_tracing()
    if _result:
        import litellm as _lm

        _log.warning(
            "Tracing init OK: callbacks=%s", _lm.callbacks
        )
    else:
        _log.info("Tracing init returned False (no backend configured)")
except Exception as _exc:
    _log.warning("Tracing init failed: %s", _exc)
