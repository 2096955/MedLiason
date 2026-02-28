#!/usr/bin/env bash
# MedExpert dev container entrypoint
# Starts all MCP servers, then runs all agents + gateway in a single sam process.
set -euo pipefail

echo "[entrypoint] Starting MCP servers..."

cd /app/medexpert/src

MCP_SERVERS=(
  pubmed clinicaltrials openfda cdc regulatory
  seer genomic environmental census_sdoh provider_intel
  imaging pharmacovigilance
  knowledge_graph societies vigibase
)

for srv in "${MCP_SERVERS[@]}"; do
  python -m "mcp_servers.${srv}.server" &
done

echo "[entrypoint] Started ${#MCP_SERVERS[@]} MCP servers"
echo "[entrypoint] Waiting 10s for MCP readiness..."
sleep 10

echo "[entrypoint] Starting agents + gateway (single sam run)..."

cd /app/medexpert

CONFIGS_DIR="/app/medexpert/configs"

# exec replaces the shell so sam receives Docker signals (SIGTERM, etc.)
# Note: feedback_bridge.yaml excluded — its raw broker_input component hangs
# in dev mode (no real Solace broker). The learning loop is not needed for dev.
exec sam run \
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
  "$CONFIGS_DIR/agents/triage_intake.yaml" \
  "$CONFIGS_DIR/agents/triage_orchestrator.yaml" \
  "$CONFIGS_DIR/gateways/webui.yaml"
