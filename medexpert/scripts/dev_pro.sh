#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# dev_pro.sh — Run the Pro (gemini-3.1-pro-preview) agent stack
#
# Activates .venv-pro (created by setup_pro_venv.sh) and launches
# the Pro agent stack on port 8081. Does NOT modify the standard
# .venv or root pyproject.toml.
#
# Usage:
#   ./scripts/dev_pro.sh              # Start Pro stack
#   ./scripts/dev_pro.sh --setup      # Run setup_pro_venv.sh and exit
#   ./scripts/dev_pro.sh --validate   # Run model validation only
#   ./scripts/dev_pro.sh --reset      # Clean Pro artifacts + venv
#   ./scripts/dev_pro.sh --help       # Show usage
#
# First-time setup:
#   bash scripts/setup_pro_venv.sh
#
# Port mapping:
#   Pro gateway: 8081  (standard: 8080)
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

PRO_VENV=".venv-pro"

# ── Help ──
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: ./scripts/dev_pro.sh [--setup | --validate | --reset | --help]"
  echo ""
  echo "  (no args)    Activate .venv-pro and start the Pro stack"
  echo "  --setup      Run setup_pro_venv.sh (create/rebuild .venv-pro) and exit"
  echo "  --validate   Run model validation script only"
  echo "  --reset      Clean Pro artifacts + remove .venv-pro"
  echo "  --help       Show this message"
  echo ""
  echo "First-time setup:  bash scripts/setup_pro_venv.sh"
  echo ""
  echo "Port mapping:"
  echo "  Pro gateway:      http://localhost:8081"
  echo "  Standard gateway: http://localhost:8080  (not affected)"
  exit 0
fi

# ── Setup (delegates entirely to setup_pro_venv.sh) ──
if [[ "${1:-}" == "--setup" ]]; then
  bash "$SCRIPT_DIR/setup_pro_venv.sh"
  exit $?
fi

# ── Reset ──
if [[ "${1:-}" == "--reset" ]]; then
  echo "Cleaning Pro artifacts..."
  rm -f medexpert_webui_pro_gateway.db
  rm -rf /tmp/medexpert-pro
  rm -f orchestrator_pro.log literature_specialist_pro.log \
        clinical_trials_specialist_pro.log drug_specialist_pro.log \
        regulatory_specialist_pro.log epidemiology_specialist_pro.log \
        genomics_specialist_pro.log environmental_specialist_pro.log \
        provider_intel_specialist_pro.log verifier_pro.log reviser_pro.log \
        triage_intake_pro.log triage_orchestrator_pro.log \
        feedback_bridge_pro.log webui_pro.log
  if [ -d "$PRO_VENV" ]; then
    echo "Removing $PRO_VENV..."
    rm -rf "$PRO_VENV"
  fi
  echo "Done."
  exit 0
fi

# ── Activate Pro venv (auto-setup if missing) ──
if [ ! -d "$PRO_VENV" ]; then
  echo "$PRO_VENV not found — running setup_pro_venv.sh..."
  bash "$SCRIPT_DIR/setup_pro_venv.sh"
fi

if [ -f "$PRO_VENV/bin/activate" ]; then
  source "$PRO_VENV/bin/activate"
elif [ -f "$PRO_VENV/Scripts/activate" ]; then
  source "$PRO_VENV/Scripts/activate"
else
  echo "ERROR: $PRO_VENV activation failed after setup."
  exit 1
fi

# ── Validate ──
if [[ "${1:-}" == "--validate" ]]; then
  echo "Running model validation..."
  python scripts/validate_pro_model.py
  exit $?
fi

# ──────────────────────────────────────────────────────────────────
# Main: launch Pro stack
# ──────────────────────────────────────────────────────────────────
echo "=== MedExpert Pro Stack (gemini-3.1-pro-preview) ==="
echo ""

LITELLM_VERSION=$(python -c "from litellm._version import version; print(version)" 2>/dev/null || echo "unknown")
echo "LiteLLM version: ${LITELLM_VERSION}"
echo "Venv: $PRO_VENV"

# Check Redis
if ! redis-cli ping > /dev/null 2>&1; then
  echo "WARNING: Redis is not responding. Memory plane will fail."
  echo "         Start Redis or set REDIS_URL to a running instance."
fi

echo ""
echo "Starting Pro agents on DevBroker..."
echo "Pro gateway will be at: http://localhost:8081"
echo ""

# ── Launch ──
# Explicit file list for deterministic agent discovery ordering.
# Gateway YAML must be LAST — uvicorn keeps the process alive.
# feedback_bridge.yaml excluded in dev mode — requires external broker.
sam run \
  configs/pro/agents/literature_specialist.yaml \
  configs/pro/agents/clinical_trials_specialist.yaml \
  configs/pro/agents/drug_specialist.yaml \
  configs/pro/agents/regulatory_specialist.yaml \
  configs/pro/agents/epidemiology_specialist.yaml \
  configs/pro/agents/genomics_specialist.yaml \
  configs/pro/agents/environmental_specialist.yaml \
  configs/pro/agents/provider_intel_specialist.yaml \
  configs/pro/agents/verifier.yaml \
  configs/pro/agents/reviser.yaml \
  configs/pro/agents/orchestrator.yaml \
  configs/pro/agents/triage_intake.yaml \
  configs/pro/agents/triage_orchestrator.yaml \
  configs/pro/gateways/webui.yaml
