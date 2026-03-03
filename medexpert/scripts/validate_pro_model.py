#!/usr/bin/env python3
"""Validate that vertex_ai/gemini-3.1-pro-preview resolves to a real endpoint.

Run BEFORE deploying the Pro stack:
    cd medexpert && python scripts/validate_pro_model.py

Requires:
  - LiteLLM >= 1.81.14 (verified: first version with gemini-3.1-pro-preview in registry)
  - Vertex AI ADC configured (gcloud auth application-default login)
  - GOOGLE_CLOUD_PROJECT or vertex_project set

Exit codes:
  0 — model responded successfully
  1 — model call failed (wrong model ID, auth error, version too old, etc.)
"""

import sys
import os

# Match the shared_config_pro.yaml values
MODEL = "vertex_ai/gemini-3.1-pro-preview"
VERTEX_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "gbg-neuro")
# gemini-3.1-pro-preview requires global endpoint (not regional)
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "global")


def main() -> int:
    # ── Step 1: Check LiteLLM version ──
    try:
        import litellm
    except ImportError:
        print("FAIL: litellm is not installed. Run: pip install litellm>=1.81.9")
        return 1

    # litellm stores its version in litellm._version.version (not __version__)
    try:
        from litellm._version import version
    except ImportError:
        version = getattr(litellm, "__version__", getattr(litellm, "version", "unknown"))
    print(f"LiteLLM version: {version}")

    parts = str(version).split(".")
    try:
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2].split("-")[0])
    except (IndexError, ValueError):
        print(f"WARN: Could not parse version '{version}', proceeding anyway")
        major, minor, patch = 0, 0, 0

    if (major, minor, patch) < (1, 81, 14):
        print(
            f"FAIL: LiteLLM {version} is too old. "
            f"gemini-3.1-pro-preview requires >= 1.81.14 "
            f"(verified: first version with model in registry). "
            f"Run: pip install 'litellm>=1.81.14'"
        )
        # Also check the model registry to confirm
        registry_path = os.path.join(
            os.path.dirname(litellm.__file__),
            "model_prices_and_context_window.json",
        )
        if os.path.exists(registry_path):
            with open(registry_path) as f:
                content = f.read()
            if "gemini-3.1-pro-preview" in content:
                print("NOTE: Model found in registry despite old version — may work anyway.")
            else:
                print(f"CONFIRMED: Model not in registry at {registry_path}")
                print("           Upgrade litellm to >= 1.81.9 first.")
        return 1

    # ── Step 2: Attempt a minimal completion call ──
    print(f"\nModel:    {MODEL}")
    print(f"Project:  {VERTEX_PROJECT}")
    print(f"Location: {VERTEX_LOCATION}")
    print("\nSending minimal prompt ('ping')...")

    try:
        response = litellm.completion(
            model=MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            temperature=0.0,
            vertex_project=VERTEX_PROJECT,
            vertex_location=VERTEX_LOCATION,
        )
    except Exception as exc:
        print(f"\nFAIL: {type(exc).__name__}: {exc}")

        # If it's a model-not-found error, try to list available models
        if "not found" in str(exc).lower() or "404" in str(exc):
            print("\nThe model ID may be incorrect. Possible fixes:")
            print("  1. Check: https://docs.litellm.ai/docs/providers/vertex")
            print("  2. Try vertex_location='global' instead of 'us-central1'")
            print("  3. Verify the model is available in your project")

        return 1

    # ── Step 3: Report success ──
    model_used = getattr(response, "model", "unknown")
    content = response.choices[0].message.content if response.choices else "(empty)"
    usage = getattr(response, "usage", None)

    print(f"\nSUCCESS!")
    print(f"  Resolved model: {model_used}")
    print(f"  Response:       {content[:100]}")
    if usage:
        print(f"  Tokens:         prompt={usage.prompt_tokens}, completion={usage.completion_tokens}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
