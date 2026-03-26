#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# setup_pro_venv.sh — Create an isolated .venv-pro for the Pro stack
#
# Creates medexpert/.venv-pro with litellm>=1.81.14 (overriding SAM's
# ==1.76.3 pin via --force-reinstall). The standard .venv is NEVER
# touched. Root pyproject.toml is NEVER modified.
#
# Usage:
#   cd medexpert && bash scripts/setup_pro_venv.sh
#
# What it does:
#   1. Creates .venv-pro in medexpert/
#   2. Installs SAM framework (editable) — gets litellm==1.76.3
#   3. Installs MedExpert deps (editable)
#   4. Force-reinstalls litellm>=1.81.14 (bypasses SAM's == pin)
#   5. Verifies the model exists in the new registry
#
# WARNING: SAM framework was built for litellm 1.76.3.
#          .venv-pro runs a newer version. Do NOT use .venv-pro
#          for standard stack operations.
# ──────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

PRO_VENV=".venv-pro"
# SAM root is one level up from medexpert/
SAM_ROOT=".."

echo "=== Setting up Pro venv (medexpert/$PRO_VENV) ==="
echo ""

# ── Step 1: Create venv ──
if [ -d "$PRO_VENV" ]; then
  echo "Removing existing $PRO_VENV..."
  rm -rf "$PRO_VENV"
fi

echo "[1/5] Creating Python venv..."
python -m venv "$PRO_VENV"

# Activate (cross-platform: Unix vs Windows Git Bash)
if [ -f "$PRO_VENV/bin/activate" ]; then
  source "$PRO_VENV/bin/activate"
elif [ -f "$PRO_VENV/Scripts/activate" ]; then
  source "$PRO_VENV/Scripts/activate"
else
  echo "ERROR: Could not find activate script in $PRO_VENV"
  exit 1
fi

echo "  Python: $(python --version)"
echo "  pip:    $(pip --version | head -c 40)"
echo ""

# ── Step 2: Install SAM framework (editable) ──
echo "[2/5] Installing SAM framework from $SAM_ROOT (editable)..."
echo "  This installs litellm==1.76.3 per SAM's pyproject.toml"
pip install -e "$SAM_ROOT" -q
echo "  Done."
echo ""

# ── Step 3: Install MedExpert deps (editable) ──
echo "[3/5] Installing MedExpert deps (editable)..."
pip install -e ".[dev]" -q
echo "  Done."
echo ""

# ── Step 4: Force-reinstall litellm ──
# --force-reinstall overwrites the SAM-pinned version in this venv only.
# --no-deps prevents pulling litellm's transitive deps (already installed).
LITELLM_TARGET="litellm>=1.81.14,<2.0.0"
echo "[4/5] Overriding litellm to '$LITELLM_TARGET'..."
echo "  Using --force-reinstall to bypass SAM's ==1.76.3 pin"
pip install --force-reinstall --no-deps "$LITELLM_TARGET" -q
echo "  Done."
echo ""

# ── Step 5: Verify ──
echo "[5/5] Verifying installation..."

LITELLM_VER=$(python -c "from litellm._version import version; print(version)" 2>/dev/null || echo "FAILED")
echo "  litellm version: $LITELLM_VER"

if [[ "$LITELLM_VER" == "FAILED" ]]; then
  echo "  ERROR: Could not import litellm._version"
  exit 1
fi

# Assert version >= 1.81.14 (numeric comparison)
REQUIRED_MIN="1.81.14"
VERSION_OK=$(python -c "
from packaging.version import Version
installed = Version('$LITELLM_VER')
required  = Version('$REQUIRED_MIN')
print('YES' if installed >= required else 'NO')
" 2>/dev/null || echo "NO")

if [[ "$VERSION_OK" != "YES" ]]; then
  echo "  ERROR: litellm $LITELLM_VER is below required minimum $REQUIRED_MIN"
  exit 1
fi
echo "  Version assertion: >= $REQUIRED_MIN — PASSED"

REGISTRY_CHECK=$(python -c "
import litellm, os, json, pathlib
p = pathlib.Path(litellm.__file__).parent / 'model_prices_and_context_window.json'
data = json.loads(p.read_text())
print('FOUND' if 'vertex_ai/gemini-3.1-pro-preview' in data else 'NOT FOUND')
" 2>/dev/null || echo "CHECK FAILED")
echo "  vertex_ai/gemini-3.1-pro-preview in registry: $REGISTRY_CHECK"

if [[ "$REGISTRY_CHECK" != "FOUND" ]]; then
  echo ""
  echo "ERROR: Model not found in litellm registry."
  echo "       Installed version $LITELLM_VER may be too old."
  exit 1
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Pro venv ready: medexpert/$PRO_VENV"
echo "  litellm: $LITELLM_VER (SAM built for 1.76.3)"
echo "  Model:   vertex_ai/gemini-3.1-pro-preview — FOUND"
echo ""
echo "  WARNING: SAM framework was built for litellm 1.76.3."
echo "  Pro venv runs $LITELLM_VER. Do NOT use .venv-pro for"
echo "  standard stack operations."
echo ""
echo "  Next steps:"
echo "    1. Run regression tests on the STANDARD venv to confirm no breakage:"
echo "       source .venv/bin/activate && pytest tests/ -v"
echo "    2. Validate Pro model access (uses validate_pro_model.py):"
echo "       bash scripts/dev_pro.sh --validate"
echo "    3. Start the Pro stack:"
echo "       bash scripts/dev_pro.sh"
echo "════════════════════════════════════════════════════════════"
