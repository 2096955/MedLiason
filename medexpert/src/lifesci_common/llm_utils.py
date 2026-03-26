"""Shared LLM utilities for MedExpert reasoning tools."""


def json_mode_kwargs(model: str) -> dict:
    """Return kwargs to enable JSON output mode for the given model.

    Gemini models support ``response_mime_type="application/json"`` which
    forces structured JSON output and avoids thinking-token budget issues.
    Non-Gemini models (e.g., Claude on Vertex AI) do not support this
    parameter — return empty dict so the caller can spread it.
    """
    if "gemini" in model.lower():
        return {"response_mime_type": "application/json"}
    return {}
