#!/usr/bin/env bash
# Start all agents, feedback bridge, and gateway via a SINGLE sam run process.
#
# In dev mode (SOLACE_DEV_MODE=true), each sam run process creates its own
# in-memory DevBroker. Running agents in separate processes means they each
# get an isolated broker and can never discover each other. The fix is to
# pass ALL config YAMLs to one sam run invocation so they share a broker.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIGS_DIR="$PROJECT_DIR/configs"

cd "$PROJECT_DIR"

echo "Starting MedExpert agents (single process)..."

# Specialists first (for discovery ordering), then orchestrator, bridge, gateway
sam run \
  "$CONFIGS_DIR/agents/literature_specialist.yaml" \
  "$CONFIGS_DIR/agents/clinical_trials_specialist.yaml" \
  "$CONFIGS_DIR/agents/drug_specialist.yaml" \
  "$CONFIGS_DIR/agents/regulatory_specialist.yaml" \
  "$CONFIGS_DIR/agents/epidemiology_specialist.yaml" \
  "$CONFIGS_DIR/agents/genomics_specialist.yaml" \
  "$CONFIGS_DIR/agents/environmental_specialist.yaml" \
  "$CONFIGS_DIR/agents/provider_intel_specialist.yaml" \
  "$CONFIGS_DIR/agents/verifier.yaml" \
  "$CONFIGS_DIR/agents/reviser.yaml" \
  "$CONFIGS_DIR/agents/orchestrator.yaml" \
  "$CONFIGS_DIR/feedback_bridge.yaml" \
  "$CONFIGS_DIR/gateways/webui.yaml"
