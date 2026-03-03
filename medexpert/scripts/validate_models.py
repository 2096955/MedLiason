#!/usr/bin/env python3
"""Validate all 3 model endpoints used by the MedExpert multi-model stack.

Run BEFORE deploying the multi-model stack:
    cd medexpert && python scripts/validate_models.py

Requires:
  - LiteLLM >= 1.81.14 (for gemini-3.1-pro-preview and claude-opus-4-6)
  - Vertex AI ADC configured (GOOGLE_APPLICATION_CREDENTIALS or gcloud auth)
  - GOOGLE_CLOUD_PROJECT or vertex_project set

Exit codes:
  0 — all models responded successfully
  1 — one or more models failed
"""

import sys
import os


MODELS = [
    {
        "name": "Standard",
        "model": "vertex_ai/gemini-2.5-flash",
        "location": "us-central1",
    },
    {
        "name": "Pro",
        "model": "vertex_ai/gemini-3.1-pro-preview",
        "location": "global",
    },
    {
        "name": "Opus",
        "model": "vertex_ai/claude-opus-4-6",
        "location": "global",
    },
]

VERTEX_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "gbg-neuro")


def main() -> int:
    try:
        import litellm
    except ImportError:
        print("FAIL: litellm not installed.")
        return 1

    try:
        from litellm._version import version
    except ImportError:
        version = getattr(litellm, "__version__", "unknown")
    print(f"LiteLLM version: {version}")
    print(f"Project:         {VERTEX_PROJECT}")
    print()

    all_passed = True

    for entry in MODELS:
        name = entry["name"]
        model = entry["model"]
        location = entry["location"]

        print(f"[{name}] {model} (location={location})...")

        try:
            response = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": "say hello"}],
                max_tokens=4096,
                temperature=0.0,
                vertex_project=VERTEX_PROJECT,
                vertex_location=location,
            )
            content = response.choices[0].message.content or "(empty)"
            tokens = response.usage
            print(
                f"  PASS — {content[:60]}  "
                f"(prompt={tokens.prompt_tokens}, completion={tokens.completion_tokens})"
            )
        except Exception as exc:
            print(f"  FAIL — {type(exc).__name__}: {exc}")
            all_passed = False

        print()

    if all_passed:
        print("ALL 3 MODELS VALIDATED")
        return 0
    else:
        print("ONE OR MORE MODELS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
