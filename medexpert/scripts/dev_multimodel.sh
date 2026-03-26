#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# dev_multimodel.sh — Run all 3 model stacks (39 agents) + gateway
#
# Loads Standard (gemini-2.5-flash), Pro (gemini-3.1-pro-preview),
# and Opus (claude-opus-4-6) agent stacks in a single sam run
# process under DevBroker. The frontend model selector maps user
# choices to the correct agent name suffix.
#
# Requires .venv-pro (litellm 1.82.0 — covers all 3 models).
# Standard .venv (litellm 1.76.3) does NOT support Pro/Opus models.
#
# Usage:
#   ./scripts/dev_multimodel.sh              # Start all 39 agents
#   ./scripts/dev_multimodel.sh --validate   # Validate all 3 models
#   ./scripts/dev_multimodel.sh --reset      # Clean all artifacts
#   ./scripts/dev_multimodel.sh --help       # Show usage
#
# Port: 8080 (single gateway serves all stacks)
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

PRO_VENV=".venv-pro"

# ── Help ──
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: ./scripts/dev_multimodel.sh [--validate | --reset | --help]"
  echo ""
  echo "  (no args)    Start all 3 model stacks (39 agents + gateway)"
  echo "  --validate   Validate all 3 model endpoints"
  echo "  --reset      Clean all Pro + Opus artifacts"
  echo "  --help       Show this message"
  echo ""
  echo "Models:"
  echo "  Standard:  vertex_ai/gemini-2.5-flash"
  echo "  Pro:       vertex_ai/gemini-3.1-pro-preview"
  echo "  Opus:      vertex_ai/claude-opus-4-6"
  echo ""
  echo "Prerequisites:"
  echo "  .venv-pro (run: bash scripts/setup_pro_venv.sh)"
  echo "  GOOGLE_APPLICATION_CREDENTIALS set"
  echo "  Redis running"
  echo "  MCP servers running"
  exit 0
fi

# ── Reset ──
if [[ "${1:-}" == "--reset" ]]; then
  echo "Cleaning all Pro + Opus artifacts..."
  rm -f medexpert_webui_pro_gateway.db
  rm -rf /tmp/medexpert-pro /tmp/medexpert-opus
  rm -f *_pro.log *_opus.log
  echo "Done."
  exit 0
fi

# ── Activate .venv-pro (auto-setup if missing) ──
if [ ! -d "$PRO_VENV" ]; then
  echo "$PRO_VENV not found — running setup_pro_venv.sh..."
  bash "$SCRIPT_DIR/setup_pro_venv.sh"
fi

if [ -f "$PRO_VENV/bin/activate" ]; then
  source "$PRO_VENV/bin/activate"
elif [ -f "$PRO_VENV/Scripts/activate" ]; then
  source "$PRO_VENV/Scripts/activate"
else
  echo "ERROR: $PRO_VENV activation failed."
  exit 1
fi

# ── Validate ──
if [[ "${1:-}" == "--validate" ]]; then
  python scripts/validate_models.py
  exit $?
fi

# ── Pre-flight ──
echo "=== MedExpert Multi-Model Stack (39 agents) ==="
echo ""

LITELLM_VERSION=$(python -c "from litellm._version import version; print(version)" 2>/dev/null || echo "unknown")
echo "LiteLLM: ${LITELLM_VERSION}"
echo "Venv:    $PRO_VENV"
echo "Models:  gemini-2.5-flash | gemini-3.1-pro-preview | claude-opus-4-6"

if ! redis-cli ping > /dev/null 2>&1; then
  echo "WARNING: Redis not responding. Memory plane will fail."
fi

echo ""
echo "Starting 39 agents + gateway on port 8080..."
echo ""

# ── Launch ──
# All 39 agents in one process. Gateway YAML must be LAST.
sam run \
  configs/agents/literature_specialist.yaml \
  configs/agents/clinical_trials_specialist.yaml \
  configs/agents/drug_specialist.yaml \
  configs/agents/regulatory_specialist.yaml \
  configs/agents/epidemiology_specialist.yaml \
  configs/agents/genomics_specialist.yaml \
  configs/agents/environmental_specialist.yaml \
  configs/agents/provider_intel_specialist.yaml \
  configs/agents/verifier.yaml \
  configs/agents/reviser.yaml \
  configs/agents/orchestrator.yaml \
  configs/agents/triage_intake.yaml \
  configs/agents/triage_orchestrator.yaml \
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
  configs/opus/agents/literature_specialist.yaml \
  configs/opus/agents/clinical_trials_specialist.yaml \
  configs/opus/agents/drug_specialist.yaml \
  configs/opus/agents/regulatory_specialist.yaml \
  configs/opus/agents/epidemiology_specialist.yaml \
  configs/opus/agents/genomics_specialist.yaml \
  configs/opus/agents/environmental_specialist.yaml \
  configs/opus/agents/provider_intel_specialist.yaml \
  configs/opus/agents/verifier.yaml \
  configs/opus/agents/reviser.yaml \
  configs/opus/agents/orchestrator.yaml \
  configs/opus/agents/triage_intake.yaml \
  configs/opus/agents/triage_orchestrator.yaml \
  configs/gateways/webui.yaml
