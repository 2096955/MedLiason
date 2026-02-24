"""Startup configuration validator for MedExpert.

Validates that required environment variables and configuration
are set before agents start. Fails fast with clear error messages.
"""

import logging
import os
import sys

log = logging.getLogger(__name__)

# Environment variables that must be set (at least one LLM key required)
_LLM_KEY_VARS = ["GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]

# Variables that should not use default/placeholder values
_BANNED_DEFAULTS = {
    "SESSION_SECRET_KEY": {"change-me-in-production", "change-me", ""},
}

# Variables that are recommended but not strictly required
_RECOMMENDED_VARS = ["NCBI_API_KEY", "REDIS_URL"]


def validate_config(*, fail_on_error: bool = True) -> list[str]:
    """Validate MedExpert startup configuration.

    Args:
        fail_on_error: If True, exit the process on critical errors.

    Returns:
        List of warning messages (non-fatal issues).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Check that at least one LLM API key is set
    has_llm_key = any(os.environ.get(var) for var in _LLM_KEY_VARS)
    if not has_llm_key:
        errors.append(
            f"No LLM API key found. Set at least one of: {', '.join(_LLM_KEY_VARS)}"
        )

    # Check for banned default values
    for var, banned in _BANNED_DEFAULTS.items():
        val = os.environ.get(var, "")
        if val in banned and os.environ.get("MEDEXPERT_ENV", "").lower() != "development":
            errors.append(
                f"{var} is set to a banned default value. "
                f"Generate a secure value (e.g., python -c \"import secrets; print(secrets.token_hex(32))\")"
            )

    # Check recommended variables
    for var in _RECOMMENDED_VARS:
        if not os.environ.get(var):
            warnings.append(f"{var} is not set. Some features may be unavailable.")

    # Report results
    for w in warnings:
        log.warning("Config validation: %s", w)

    if errors:
        for e in errors:
            log.error("Config validation FAILED: %s", e)
        if fail_on_error:
            print("\n=== MedExpert Configuration Errors ===", file=sys.stderr)
            for e in errors:
                print(f"  ERROR: {e}", file=sys.stderr)
            if warnings:
                for w in warnings:
                    print(f"  WARNING: {w}", file=sys.stderr)
            print(
                "\nFix the above errors and restart. "
                "Set MEDEXPERT_ENV=development to bypass in dev mode.\n",
                file=sys.stderr,
            )
            sys.exit(1)

    return warnings


if __name__ == "__main__":
    # Allow running directly: python -m lifesci_common.config_validator
    logging.basicConfig(level=logging.INFO)
    warnings = validate_config(fail_on_error=True)
    if not warnings:
        print("All configuration checks passed.")
