# Shared utilities and constants for MedExpert

# Bootstrap LLM observability tracing (Langfuse or Phoenix) at import time.
# Silent no-op if neither LANGFUSE_SECRET_KEY nor PHOENIX_API_KEY is set.
try:
    from lifesci_common.tracing import init_tracing as _init_tracing
    _init_tracing()
except Exception:
    pass
