#!/usr/bin/env bash
# MedExpert-v2 startup for Cloud Run single-container deployment.
#
# Starts: Redis -> 10 MCP servers -> sam run (39 agents + gateway)
#
# 3 model stacks (13 agents each):
#   Standard: vertex_ai/gemini-2.5-flash     (no suffix)
#   Pro:      vertex_ai/gemini-3.1-pro-preview (Pro suffix)
#   Opus:     vertex_ai/claude-opus-4-6       (Opus suffix)
#
# The frontend model selector maps user choice -> agent name suffix.
# All 39 agents share a single DevBroker + single gateway on PORT.
set -euo pipefail

cd /app

# ---------------------------------------------------------------------------
# 1. Validate all required configs (39 agents + 1 gateway = 40 files)
# ---------------------------------------------------------------------------
REQUIRED_CONFIGS=(
  # Standard stack (13 agents)
  configs/agents/orchestrator.yaml
  configs/agents/literature_specialist.yaml
  configs/agents/clinical_trials_specialist.yaml
  configs/agents/drug_specialist.yaml
  configs/agents/regulatory_specialist.yaml
  configs/agents/epidemiology_specialist.yaml
  configs/agents/genomics_specialist.yaml
  configs/agents/environmental_specialist.yaml
  configs/agents/provider_intel_specialist.yaml
  configs/agents/verifier.yaml
  configs/agents/reviser.yaml
  configs/agents/triage_intake.yaml
  configs/agents/triage_orchestrator.yaml
  # Pro stack (13 agents)
  configs/pro/agents/orchestrator.yaml
  configs/pro/agents/literature_specialist.yaml
  configs/pro/agents/clinical_trials_specialist.yaml
  configs/pro/agents/drug_specialist.yaml
  configs/pro/agents/regulatory_specialist.yaml
  configs/pro/agents/epidemiology_specialist.yaml
  configs/pro/agents/genomics_specialist.yaml
  configs/pro/agents/environmental_specialist.yaml
  configs/pro/agents/provider_intel_specialist.yaml
  configs/pro/agents/verifier.yaml
  configs/pro/agents/reviser.yaml
  configs/pro/agents/triage_intake.yaml
  configs/pro/agents/triage_orchestrator.yaml
  # Opus stack (13 agents)
  configs/opus/agents/orchestrator.yaml
  configs/opus/agents/literature_specialist.yaml
  configs/opus/agents/clinical_trials_specialist.yaml
  configs/opus/agents/drug_specialist.yaml
  configs/opus/agents/regulatory_specialist.yaml
  configs/opus/agents/epidemiology_specialist.yaml
  configs/opus/agents/genomics_specialist.yaml
  configs/opus/agents/environmental_specialist.yaml
  configs/opus/agents/provider_intel_specialist.yaml
  configs/opus/agents/verifier.yaml
  configs/opus/agents/reviser.yaml
  configs/opus/agents/triage_intake.yaml
  configs/opus/agents/triage_orchestrator.yaml
  # Gateway (MUST be last — uvicorn keeps sam run alive)
  configs/gateways/webui.yaml
)

for cfg in "${REQUIRED_CONFIGS[@]}"; do
  if [ ! -s "$cfg" ]; then
    echo "[MedExpert-v2] FATAL: Required config not found or empty: $cfg"
    exit 1
  fi
done

echo "[MedExpert-v2] Starting multi-model MedExpert (39 agents, 3 stacks)..."
echo "[MedExpert-v2] Host: 0.0.0.0, Port: ${PORT:-8080}"
echo "[MedExpert-v2] LiteLLM: $(python -c 'from litellm._version import version; print(version)' 2>/dev/null || echo 'unknown')"

export FASTAPI_HOST="0.0.0.0"
export FASTAPI_PORT="${PORT:-8080}"
export COLD_DB_PATH="data/medexpert-cold.db"

# ---------------------------------------------------------------------------
# 2. Start Redis (ephemeral, localhost only)
# ---------------------------------------------------------------------------
echo "[MedExpert-v2] Starting Redis..."
redis-server --daemonize yes --bind 127.0.0.1 --port 6379

for i in $(seq 1 10); do
  redis-cli ping 2>/dev/null | grep -q PONG && break
  sleep 1
done

if ! redis-cli ping 2>/dev/null | grep -q PONG; then
  echo "[MedExpert-v2] FATAL: Redis failed to start"
  exit 1
fi
echo "[MedExpert-v2] Redis ready"

# ---------------------------------------------------------------------------
# 3. Start MCP servers (shared across all 3 stacks — stateless)
# ---------------------------------------------------------------------------
MCP_PIDS=()
MCP_SERVERS=(
  "mcp_servers.pubmed.server:9001"
  "mcp_servers.clinicaltrials.server:9002"
  "mcp_servers.openfda.server:9003"
  "mcp_servers.cdc.server:9004"
  "mcp_servers.regulatory.server:9005"
  "mcp_servers.seer.server:9006"
  "mcp_servers.genomic.server:9007"
  "mcp_servers.environmental.server:9008"
  "mcp_servers.census_sdoh.server:9009"
  "mcp_servers.provider_intel.server:9010"
  "mcp_servers.knowledge_graph.server:9011"
  "mcp_servers.nhs_111.server:9016"
  "mcp_servers.bma_library.server:9017"
  "mcp_servers.openevidence.server:9018"
)

for entry in "${MCP_SERVERS[@]}"; do
  module="${entry%%:*}"
  port="${entry##*:}"
  echo "[MedExpert-v2] Starting MCP: $module (port $port)"
  python -m "$module" &
  MCP_PIDS+=($!)
done

# ---------------------------------------------------------------------------
# 4. Health check MCP servers
# ---------------------------------------------------------------------------
echo "[MedExpert-v2] Waiting for MCP servers..."
for entry in "${MCP_SERVERS[@]}"; do
  port="${entry##*:}"
  module="${entry%%:*}"
  ready=false
  for i in $(seq 1 30); do
    if python -c "import socket; s=socket.create_connection(('localhost',$port),timeout=2); s.close()" 2>/dev/null; then
      ready=true
      break
    fi
    sleep 1
  done
  if [ "$ready" = true ]; then
    echo "[MedExpert-v2]   Port $port ready ($module)"
  else
    echo "[MedExpert-v2]   WARNING: Port $port not ready ($module) — continuing (graceful degradation)"
  fi
done

# ---------------------------------------------------------------------------
# 4b. Start Memgraph MCP server (streamable-http on port 8000)
# ---------------------------------------------------------------------------
echo "[MedExpert-v2] Starting Memgraph MCP server..."
MCP_TRANSPORT=streamable-http python -m mcp_memgraph &
MCP_PIDS+=($!)

ready=false
for i in $(seq 1 30); do
  if python -c "import socket; s=socket.create_connection(('localhost',8000),timeout=2); s.close()" 2>/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" = true ]; then
  echo "[MedExpert-v2]   Port 8000 ready (memgraph-mcp)"
else
  echo "[MedExpert-v2]   WARNING: Port 8000 not ready (memgraph-mcp) — continuing (graceful degradation)"
fi

# ---------------------------------------------------------------------------
# 5. Graceful shutdown handler
# ---------------------------------------------------------------------------
cleanup() {
  echo "[MedExpert-v2] Shutting down..."
  if [ -n "${SAM_PID:-}" ]; then
    kill "$SAM_PID" 2>/dev/null || true
    wait "$SAM_PID" 2>/dev/null || true
  fi
  for (( i=${#MCP_PIDS[@]}-1; i>=0; i-- )); do
    kill "${MCP_PIDS[$i]}" 2>/dev/null || true
  done
  redis-cli shutdown nosave 2>/dev/null || true
  echo "[MedExpert-v2] Shutdown complete"
}
trap cleanup SIGTERM SIGINT EXIT

# ---------------------------------------------------------------------------
# 6. Start sam run (39 agents + 1 gateway)
# ---------------------------------------------------------------------------
echo "[MedExpert-v2] Starting sam run with ${#REQUIRED_CONFIGS[@]} configs (39 agents + 1 gateway)..."
sam run --system-env "${REQUIRED_CONFIGS[@]}" &
SAM_PID=$!
wait $SAM_PID
